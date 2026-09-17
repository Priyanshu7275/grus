"""
GRUS — Rule Engine

Deterministic clinical rules. No LLM, no model, no inference.
Each rule reads specific rows, applies a published threshold, and writes
an alert_traces row recording exactly what it saw and where it read it.

The trace is written BEFORE any text is generated. The LLM later narrates
the trace; it cannot invent a reason because the reason already exists.

Three severities, and 'unknown' is a real one — a rule that cannot
evaluate says so rather than staying silent.
"""

import os
import json
import psycopg
from dataclasses import dataclass, field
from typing import Optional
from grus_config import DB
from psycopg.rows import tuple_row



HOST = DB.HOST
PWD = DB.PASSWORD

RULE_VERSION = "1.0"


@dataclass
class Alert:
    code: str
    severity: str                    # critical | warning | info | unknown
    title: str
    body: str
    action: str
    inputs: dict = field(default_factory=dict)
    sources: list = field(default_factory=list)   # [{"table": "labs", "id": 123}]


# ---------------------------------------------------------------
# Data access — every rule reads through these, so the as_of cutoff
# is enforced in one place rather than remembered in twenty.
# ---------------------------------------------------------------
class PatientView:
    """
    Everything a rule may read, capped at as_of_hours.

    This is the point-in-time boundary. Without it, rules would see
    data from after the moment they claim to be reasoning about, and
    every prediction would be reading the answer key.
    """

    def __init__(self, conn, hadm_id, as_of_hours=None):
        self.conn = conn
        self.hadm_id = hadm_id
        self.as_of = as_of_hours
        self._cache = {}

        row = conn.execute("""
            SELECT subject_id, admission_type, cohort FROM admissions
            WHERE hadm_id = %s
        """, (hadm_id,)).fetchone()
        self.subject_id, self.admission_type, self.cohort = row

    def _cap(self, col="hours_since_admit"):
        if self.as_of is None:
            return ""
        return f" AND ({col} IS NULL OR {col} <= {self.as_of}) "

    def labs(self, label, first_only=False):
        key = ("labs", label, first_only)
        if key in self._cache:
            return self._cache[key]
        q = f"""
            SELECT lab_id, valuenum, valueuom, hours_since_admit, flag
            FROM labs
            WHERE hadm_id = %s AND label = %s AND valuenum IS NOT NULL
              {self._cap()}
              {"AND is_first_of_stay" if first_only else ""}
            ORDER BY hours_since_admit
        """
        r = self.conn.execute(q, (self.hadm_id, label)).fetchall()
        self._cache[key] = r
        return r

    def latest_lab(self, label):
        r = self.labs(label)
        return r[-1] if r else None

    def first_lab(self, label):
        r = self.labs(label)
        return r[0] if r else None

    def nadir_lab(self, label):
        """
        Lowest value seen this admission.

        For anything measuring bleeding risk, the trough is the signal.
        Platelets that fell to 62 and recovered to 164 are a bleeding
        event that happened; reporting only the latest value hides it.
        """
        r = self.labs(label)
        return min(r, key=lambda x: float(x[1])) if r else None

    def peak_lab(self, label):
        """Highest value seen. For creatinine, lactate, troponin."""
        r = self.labs(label)
        return max(r, key=lambda x: float(x[1])) if r else None

    def meds_by_class(self, drug_class, current_only=True):
        q = f"""
            SELECT medication_id, drug_normalized, is_active, start_hours,
                   stop_hours, time_valid, status
            FROM medications
            WHERE hadm_id = %s AND drug_class = %s
              {self._cap("start_hours")}
        """
        return self.conn.execute(q, (self.hadm_id, drug_class)).fetchall()

    def med_history_class(self, drug_class):
        """Same drug class, but from PREVIOUS admissions."""
        return self.conn.execute("""
            SELECT m.medication_id, m.drug_normalized, a.admittime
            FROM medications m
            JOIN admissions a ON a.hadm_id = m.hadm_id
            WHERE m.subject_id = %s AND a.is_current = FALSE
              AND m.drug_class = %s
        """, (self.subject_id, drug_class)).fetchall()

    def latest_vital(self, code):
        q = f"""
            SELECT vital_id, valuenum, hours_since_admit
            FROM vitals
            WHERE hadm_id = %s AND vital_code = %s AND valuenum IS NOT NULL
              {self._cap()}
            ORDER BY hours_since_admit DESC LIMIT 1
        """
        return self.conn.execute(q, (self.hadm_id, code)).fetchone()

    def diagnoses(self, prefixes=None, icd_version=None):
        q = """SELECT diagnosis_id, icd_code, long_title, icd_version, seq_num
               FROM diagnoses WHERE hadm_id = %s
               ORDER BY seq_num NULLS LAST"""
        rows = self.conn.execute(q, (self.hadm_id,)).fetchall()
        if icd_version:
            rows = [r for r in rows if r[3] == icd_version]
        if prefixes:
            rows = [r for r in rows if any(r[1].startswith(p) for p in prefixes)]
        return rows

    def trauma_diagnoses(self):
        """
        Injury codes only, version-aware, ordered by clinical priority.

        Two traps this avoids:

        1. ICD-9 and ICD-10 reuse letters. V42 is 'organ replaced by
           transplant' in ICD-9 but 'transport accident' in ICD-10.
           Prefix matching alone reported a heart valve as an injury.

        2. E930-E949 are adverse-drug-effect codes, not injuries.
           'Anticoagulants causing adverse effects' is a complication
           the hospital recorded, not something that happened to the
           patient in a crash.

        Ordered by seq_num, so the principal diagnosis comes first
        rather than whatever the database returns.
        """
        rows = self.conn.execute("""
            SELECT diagnosis_id, icd_code, long_title, icd_version, seq_num
            FROM diagnoses WHERE hadm_id = %s
            ORDER BY seq_num NULLS LAST
        """, (self.hadm_id,)).fetchall()

        out = []
        for d in rows:
            code, ver = d[1], d[3]
            if ver == 9:
                # 800-959: injuries and poisonings
                if code[:3].isdigit() and 800 <= int(code[:3]) <= 959:
                    out.append(d)
                # E800-E999: external causes, but NOT E930-E949 (adverse drug effects)
                elif code.startswith("E") and code[1:4].isdigit():
                    n = int(code[1:4])
                    if 800 <= n <= 999 and not (930 <= n <= 949):
                        out.append(d)
            elif ver == 10:
                # S00-T88: injuries. T36-T50 are poisonings by drugs — exclude.
                if code[0] == "S":
                    out.append(d)
                elif code[0] == "T" and code[1:3].isdigit():
                    if not (36 <= int(code[1:3]) <= 50):
                        out.append(d)
                # V00-Y99: external causes. Y40-Y84 are medical misadventure — exclude.
                elif code[0] in ("V", "W", "X"):
                    out.append(d)
                elif code[0] == "Y" and code[1:3].isdigit():
                    if not (40 <= int(code[1:3]) <= 84):
                        out.append(d)
        return out

    def bleeding_diagnoses(self):
        """
        Active or recent haemorrhage, either ICD version.
        Ordered by seq_num so the principal diagnosis leads.
        """
        return (self.diagnoses(prefixes=["423", "578", "285", "431", "432",
                                         "459", "596", "998"], icd_version=9) +
                self.diagnoses(prefixes=["K92", "I31", "D62", "I60", "I61",
                                         "I62", "R58", "K25", "K26"], icd_version=10))

    def has_note_mention(self, *terms):
        """Keyword search over this patient's notes. Complements vector search."""
        clauses = " OR ".join(["text ILIKE %s"] * len(terms))
        params = [self.subject_id] + [f"%{t}%" for t in terms]
        return self.conn.execute(f"""
            SELECT chunk_id, section, text FROM note_chunks
            WHERE subject_id = %s AND ({clauses})
            LIMIT 5
        """, params).fetchall()


# ---------------------------------------------------------------
# Rules
# Each takes a PatientView, returns an Alert or None.
# ---------------------------------------------------------------

def rule_shock_index(v: PatientView):
    """
    Shock Index = HR / systolic BP.
    Above 0.9 is a concern, above 1.3 suggests significant blood loss.
    Rises before blood pressure falls, which is why it matters in trauma.
    """
    hr = v.latest_vital("hr")
    sbp = v.latest_vital("sbp")

    if not hr or not sbp:
        missing = [n for n, x in (("heart rate", hr), ("systolic BP", sbp)) if not x]
        return Alert(
            code="SHOCK_INDEX",
            severity="unknown",
            title="SHOCK INDEX — CANNOT CALCULATE",
            body=f"Missing {' and '.join(missing)}.",
            action="Obtain vitals.",
            inputs={"hr": hr[1] if hr else None, "sbp": sbp[1] if sbp else None},
            sources=[],
        )

    si = round(float(hr[1]) / float(sbp[1]), 3)
    src = [{"table": "vitals", "id": hr[0]}, {"table": "vitals", "id": sbp[0]}]
    inputs = {"hr": float(hr[1]), "sbp": float(sbp[1]), "shock_index": si,
              "hours": float(hr[2])}

    if si >= 1.3:
        return Alert("SHOCK_INDEX", "critical",
                     "SHOCK INDEX - SEVERE",
                     f"Shock Index {si} (HR {hr[1]:.0f} / SBP {sbp[1]:.0f}). "
                     f"Above 1.3 suggests significant volume loss.",
                     "Assess for haemorrhage. Consider transfusion.",
                     inputs, src)
    if si >= 0.9:
        return Alert("SHOCK_INDEX", "warning",
                     "SHOCK INDEX ELEVATED",
                     f"Shock Index {si} (HR {hr[1]:.0f} / SBP {sbp[1]:.0f}). "
                     f"Above 0.9 warrants attention.",
                     "Monitor closely. Recheck in 15 minutes.",
                     inputs, src)
    return None


def rule_anticoagulated(v: PatientView):
    """
    INR above therapeutic range. The number is what matters, not whether
    a prescription exists — the trauma demo patient proves that.
    """
    inr = v.first_lab("INR(PT)")
    if not inr:
        return None

    val = float(inr[1])
    src = [{"table": "labs", "id": inr[0]}]
    inputs = {"inr": val, "hours": float(inr[2]) if inr[2] is not None else None}

    if val < 1.5:
        return None

    # Is the drug actually documented anywhere?
    meds = v.meds_by_class("anticoagulant")
    history = v.med_history_class("anticoagulant")
    notes = v.has_note_mention("warfarin", "coumadin", "apixaban", "rivaroxaban")

    if meds:
        drugs = ", ".join(sorted({m[1] for m in meds}))
        src += [{"table": "medications", "id": m[0]} for m in meds]
        provenance = f"On {drugs} this admission."
    elif history:
        drugs = ", ".join(sorted({m[1] for m in history}))
        src += [{"table": "medications", "id": m[0]} for m in history]
        provenance = f"On {drugs} at a previous admission. Not charted this visit."
    elif notes:
        src += [{"table": "note_chunks", "id": n[0]} for n in notes]
        provenance = ("Anticoagulant appears in clinical notes but in no "
                      "structured medication record.")
    else:
        provenance = ("No anticoagulant in any medication table or note. "
                      "Agent and last dose UNKNOWN.")

    severity = "critical" if val >= 4.0 else "warning"
    return Alert("ANTICOAGULATED", severity,
                 f"INR {val} - ANTICOAGULATED",
                 f"Arrival INR {val}. Therapeutic range is typically 2-3. {provenance}",
                 "Confirm agent and last dose with patient or family. "
                 "Reversal may be indicated before any procedure.",
                 inputs, src)


def rule_anticoag_trauma(v: PatientView):
    """
    Anticoagulation plus injury OR active bleeding.

    Each alone is manageable. Together they mean occult haemorrhage
    until proven otherwise.
    """
    inr = v.first_lab("INR(PT)")
    if not inr or float(inr[1]) < 2.0:
        return None

    trauma_dx = v.trauma_diagnoses()
    bleed_dx = v.bleeding_diagnoses()

    dx = trauma_dx or bleed_dx
    if not dx:
        return None

    val = float(inr[1])
    kind = "injury" if trauma_dx else "active bleeding"
    titles = [d[2] for d in dx[:3] if d[2]]
    return Alert(
        "ANTICOAG_TRAUMA", "critical",
        f"BLEEDING RISK - ANTICOAGULATED WITH {kind.upper()}",
        f"INR {val} with {'; '.join(titles)}. Anticoagulation plus {kind} "
        f"raises the risk of occult haemorrhage, particularly intracranial.",
        "Low threshold for CT imaging. Consider reversal.",
        {"inr": val, "codes": [d[1] for d in dx[:5]], "kind": kind},
        [{"table": "labs", "id": inr[0]}] +
        [{"table": "diagnoses", "id": d[0]} for d in dx[:3]],
    )


def rule_nsaid_bleeding(v: PatientView):
    """NSAIDs impair platelets. Poor choice in an actively bleeding patient."""
    nsaids = v.meds_by_class("nsaid")
    if not nsaids:
        return None

    inr = v.first_lab("INR(PT)")
    hgb = v.latest_lab("Hemoglobin")
    plt = v.latest_lab("Platelet Count")
    bleeding_dx = v.bleeding_diagnoses()

    risky = (
        (inr and float(inr[1]) >= 2.0) or
        (hgb and float(hgb[1]) < 9.0) or
        (plt and float(plt[1]) < 100) or
        bool(bleeding_dx)
    )
    if not risky:
        return None

    drugs = ", ".join(sorted({n[1] for n in nsaids}))
    reasons = []
    src = [{"table": "medications", "id": n[0]} for n in nsaids]
    if inr and float(inr[1]) >= 2.0:
        reasons.append(f"INR {float(inr[1])}")
        src.append({"table": "labs", "id": inr[0]})
    if hgb and float(hgb[1]) < 9.0:
        reasons.append(f"haemoglobin {float(hgb[1])}")
        src.append({"table": "labs", "id": hgb[0]})
    if plt and float(plt[1]) < 100:
        reasons.append(f"platelets {float(plt[1]):.0f}")
        src.append({"table": "labs", "id": plt[0]})
    if bleeding_dx:
        reasons.append(bleeding_dx[0][2] or bleeding_dx[0][1])
        src.append({"table": "diagnoses", "id": bleeding_dx[0][0]})

    return Alert(
        "NSAID_BLEEDING", "warning",
        "NSAID WITH BLEEDING RISK",
        f"{drugs} ordered alongside {', '.join(reasons)}. "
        f"NSAIDs impair platelet function.",
        "Review analgesia. Paracetamol or an opioid may be safer.",
        {"nsaids": [n[1] for n in nsaids], "reasons": reasons},
        src,
    )


def rule_renal_contrast(v: PatientView):
    """
    Creatinine before contrast imaging. Uses raw creatinine rather than
    eGFR, which needs age, sex, and race — the last of which we do not store.
    """
    cr = v.latest_lab("Creatinine")
    if not cr:
        return Alert(
            "RENAL_CONTRAST", "unknown",
            "RENAL FUNCTION - NO RESULT",
            "No creatinine on file for this admission.",
            "Obtain before any contrast study.",
            {}, [],
        )

    val = float(cr[1])
    if val < 1.5:
        return None

    sev = "critical" if val >= 2.5 else "warning"
    return Alert(
        "RENAL_CONTRAST", sev,
        f"RENAL IMPAIRMENT - CREATININE {val}",
        f"Creatinine {val} mg/dL at {cr[3]}h. Elevated.",
        "Contrast carries added risk. Hydrate; consider a non-contrast study.",
        {"creatinine": val, "hours": float(cr[3]) if cr[3] is not None else None},
        [{"table": "labs", "id": cr[0]}],
    )


def rule_anaemia(v: PatientView):
    """
    Haemoglobin. Judged on the nadir as well as the current value —
    a count that crashed and recovered is a bleeding event that happened.

    The drop matters more than any single number: 9 that was 12 yesterday
    is more alarming than a stable 8.5.
    """
    hgbs = v.labs("Hemoglobin")
    if not hgbs:
        return None

    latest = hgbs[-1]
    nadir = min(hgbs, key=lambda x: float(x[1]))
    cur = float(latest[1])
    low = float(nadir[1])

    src = [{"table": "labs", "id": latest[0]}]
    if nadir[0] != latest[0]:
        src.append({"table": "labs", "id": nadir[0]})

    inputs = {
        "hemoglobin_current": cur,
        "hemoglobin_nadir": low,
        "nadir_hours": float(nadir[3]) if nadir[3] is not None else None,
    }

    drop = None
    if len(hgbs) >= 2:
        first = float(hgbs[0][1])
        drop = round(first - low, 1)
        inputs["hemoglobin_first"] = first
        inputs["max_drop"] = drop
        if hgbs[0][0] not in (latest[0], nadir[0]):
            src.append({"table": "labs", "id": hgbs[0][0]})

    recovered = cur >= 9.0 and low < 9.0
    nadir_txt = ""
    if low < cur:
        nadir_txt = f" (nadir {low} at {inputs['nadir_hours']:.0f}h)"

    if low < 7.0:
        return Alert("ANAEMIA", "info" if recovered else "critical",
                     f"HAEMOGLOBIN {'RECOVERED - NADIR' if recovered else ''} {low} ".strip(),
                     f"Haemoglobin {cur} g/dL{nadir_txt}." +
                     (f" Fell {drop} g/dL from {inputs.get('hemoglobin_first')}."
                      if drop and drop > 0 else ""),
                     "Transfusion likely indicated. Identify the source."
                     if not recovered else "Note the episode.",
                     inputs, src)

    if drop and drop >= 2.0:
        return Alert("ANAEMIA", "info" if recovered else "warning",
                     f"HAEMOGLOBIN FELL - {inputs['hemoglobin_first']} to {low}",
                     f"Dropped {drop} g/dL during this admission. Now {cur}.",
                     "Look for ongoing blood loss. Recheck."
                     if not recovered else "Note the episode.",
                     inputs, src)

    if cur < 9.0:
        return Alert("ANAEMIA", "info",
                     f"ANAEMIA - HAEMOGLOBIN {cur}",
                     f"Haemoglobin {cur} g/dL, below normal{nadir_txt}.",
                     "Note before any procedure with blood loss.",
                     inputs, src)
    return None


def rule_no_prior_records(v: PatientView):
    """
    Absence of a record is not absence of a condition.
    A blank chart and 'we have nothing, here is what to ask' are
    very different things.
    """
    n_prior = v.conn.execute("""
        SELECT COUNT(*) FROM admissions
        WHERE subject_id = %s AND is_current = FALSE
    """, (v.subject_id,)).fetchone()[0]

    if n_prior > 0:
        return None

    return Alert(
        "NO_PRIOR_RECORDS", "unknown",
        "NO PRIOR RECORDS AT THIS FACILITY",
        "First presentation here. No baseline vitals, no home medication "
        "list, no allergy history.",
        "ASK patient or family before anticoagulants, contrast, or anaesthesia. "
        "Records may exist at another hospital.",
        {"prior_admissions": 0}, [],
    )


def rule_unreliable_med_timing(v: PatientView):
    """
    Medication rows whose stop time precedes their start time.
    Reporting a confident status from a corrupt row is worse than
    admitting the row is broken.
    """
    rows = v.conn.execute("""
        SELECT medication_id, drug_normalized, drug_class
        FROM medications
        WHERE hadm_id = %s AND NOT time_valid
    """, (v.hadm_id,)).fetchall()

    if not rows:
        return None

    important = [r for r in rows if r[2] in
                 ("anticoagulant", "antiplatelet", "vasopressor", "insulin")]
    target = important or rows
    

    drug_list = ", ".join([r[1] for r in target])
    source_lines = " · ".join(
        f"{r[1]} → medications#{r[0]}" for r in target)

    return Alert(
        "MED_TIMING_UNRELIABLE",
        "warning" if important else "info",
        f"Check with nurse — {drug_list}",
        f"Problem: The pharmacy record shows {drug_list} stopping "
        f"before they started. This is likely a data-entry error.\n"
        f"Why it matters: We can't tell if the patient is still "
        f"receiving them.\n"
        f"Action: Ask the bedside nurse if each drug is running right "
        f"now.\n"
        f"Source: {source_lines}",
        "CONFIRM with the bedside nurse.",
        {"drugs": [r[1] for r in target]},
        [{"table": "medications", "id": r[0]} for r in target],
    )

def rule_thrombocytopenia(v: PatientView):
    """
    Low platelets. Platelets plug the bleed; INR measures the clotting
    cascade. A normal INR with platelets of 40 is still a bleeding patient.

    Judged on the NADIR, not the latest value. A count that fell to 62 and
    recovered to 164 records a bleeding event that happened — reporting
    only the current number hides it.
    """
    latest = v.latest_lab("Platelet Count")
    nadir = v.nadir_lab("Platelet Count")
    if not latest or not nadir:
        return None

    cur = float(latest[1])
    low = float(nadir[1])
    if low >= 150:
        return None

    recovered = cur >= 150 and low < 150
    src = [{"table": "labs", "id": nadir[0]}]
    if latest[0] != nadir[0]:
        src.append({"table": "labs", "id": latest[0]})

    inputs = {
        "platelets_current": cur,
        "platelets_nadir": low,
        "nadir_hours": float(nadir[3]) if nadir[3] is not None else None,
        "recovered": recovered,
    }

    # What else is thinning the blood?
    compounding = []
    for cls, name in (("anticoagulant", "anticoagulant"),
                      ("antiplatelet", "antiplatelet"),
                      ("nsaid", "NSAID")):
        meds = v.meds_by_class(cls)
        if meds:
            drugs = ", ".join(sorted({m[1] for m in meds}))
            compounding.append(f"{drugs} ({name})")
            src += [{"table": "medications", "id": m[0]} for m in meds]

    inr = v.first_lab("INR(PT)")
    if inr and float(inr[1]) >= 1.5:
        compounding.append(f"INR {float(inr[1])}")
        src.append({"table": "labs", "id": inr[0]})
        inputs["inr"] = float(inr[1])

    inputs["compounding"] = compounding

    if recovered:
        sev = "info"
        title = f"PLATELETS RECOVERED - NADIR {low:.0f}"
        body = (f"Fell to {low:.0f} K/uL at {inputs['nadir_hours']:.0f}h, "
                f"now {cur:.0f}. A bleeding risk that has resolved.")
        action = "Note the episode. No current restriction."
    else:
        if low < 50:
            sev = "critical"
            note = "Below 50 carries a risk of spontaneous bleeding."
        elif low < 100:
            sev = "critical" if compounding else "warning"
            note = "Below 100 raises procedural bleeding risk."
        else:
            sev = "warning" if compounding else "info"
            note = "Mildly low."

        title = f"PLATELETS {cur:.0f} - LOW"
        body = f"Platelets {cur:.0f} K/uL"
        if low < cur:
            body += f" (nadir {low:.0f} at {inputs['nadir_hours']:.0f}h)"
        body += f". {note}"
        action = ("Avoid NSAIDs. Discuss transfusion threshold before any "
                  "procedure." if low < 100 else
                  "Note before any procedure with blood loss.")

    if compounding:
        body += f" Compounded by {'; '.join(compounding)}."

    return Alert("THROMBOCYTOPENIA", sev, title, body, action, inputs, src)


RULES = [
    rule_anticoagulated,
    rule_anticoag_trauma,
    rule_thrombocytopenia,
    rule_shock_index,
    rule_anaemia,
    rule_nsaid_bleeding,
    rule_renal_contrast,
    rule_unreliable_med_timing,
    rule_no_prior_records,
]

SEVERITY_ORDER = {"critical": 0, "warning": 1, "unknown": 2, "info": 3}


# ---------------------------------------------------------------
# Run and persist
# ---------------------------------------------------------------
def evaluate(conn, hadm_id, as_of_hours=None, persist=True):
    view = PatientView(conn, hadm_id, as_of_hours)
    alerts = []

    for rule in RULES:
        try:
            a = rule(view)
        except Exception as e:
            print(f"  rule {rule.__name__} failed: {e}")
            continue
        if a:
            alerts.append(a)

    alerts.sort(key=lambda a: SEVERITY_ORDER.get(a.severity, 9))

    if persist:
        conn.execute("""
            DELETE FROM alert_traces
            WHERE hadm_id = %s AND rule_version = %s
        """, (hadm_id, RULE_VERSION))
        for a in alerts:
            conn.execute("""
                INSERT INTO alert_traces
                    (hadm_id, subject_id, alert_code, severity, rule_version,
                     inputs, source_row_ids)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (hadm_id, view.subject_id, a.code, a.severity, RULE_VERSION,
                  json.dumps(a.inputs, default=str), json.dumps(a.sources)))
        conn.commit()

    return alerts


def show(alerts):
    icons = {"critical": "[!!]", "warning": "[! ]", "unknown": "[? ]", "info": "[i ]"}
    for a in alerts:
        print(f"\n{icons.get(a.severity,'')} {a.title}")
        print(f"     {a.body}")
        print(f"     -> {a.action}")
        if a.sources:
            print(f"     sources: {a.sources}")
        else:
            print(f"     sources: none (absence of data is the finding)")


if __name__ == "__main__":
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus",
        user="grusadmin", password=PWD, sslmode="require",
        keepalives=1, keepalives_idle=30
    )
    conn.execute("SET search_path TO grus, public")

    for label, hadm, as_of in [
        ("TRAUMA — arrival (hour 1)",     28173870, 1),
        ("TRAUMA — full record",          28173870, None),
        ("CARDIAC — arrival (hour 1)",    27180495, 1),
        ("CARDIAC — full record",         27180495, None),
    ]:
        print("\n" + "=" * 64)
        print(label)
        print("=" * 64)
        show(evaluate(conn, hadm, as_of))

    print("\n\ntraces written:")
    print(conn.execute("""
        SELECT alert_code, severity, COUNT(*) FROM alert_traces
        GROUP BY 1,2 ORDER BY 3 DESC
    """).fetchall())

    conn.close()
