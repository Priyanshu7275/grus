"""
GRUS — Tools

The functions an LLM is allowed to call. Nothing else touches the database.

The model chooses WHICH tool and WITH WHAT ARGUMENTS. It never writes the
query — those are written here, already correct, already capped at the
as-of cutoff, already returning source row ids.

Why not let the model generate SQL: a wrong join or a missing time filter
returns a confident number nobody catches, it bypasses the point-in-time
boundary, and it breaks the source tracking the Verifier depends on. The
gap this leaves is bounded and visible; generated SQL leaves an unbounded
and invisible one.

Tools are deliberately generic. get_lab(label) covers every lab in MIMIC,
so a question about magnesium works without anyone writing a magnesium
tool. discover_* tools let the model find out what exists before asking
for it.
"""

import os
import json
import psycopg
from grus_config import DB

from grus_retriever import Retriever, Evidence



HOST = DB.HOST
PWD = DB.PASSWORD


class ToolResult:
    """
    Every tool returns this shape: data the model can read, sources the
    Verifier can check, and an explicit found/not-found flag.

    found=False is a real answer, not an error. 'No creatinine on file'
    is information a doctor needs.
    """

    def __init__(self, found, summary, rows=None, sources=None, note=None):
        self.found = found
        self.summary = summary
        self.rows = rows or []
        self.sources = sources or []
        self.note = note

    def to_dict(self):
        d = {"found": self.found, "summary": self.summary}
        if self.rows:
            d["data"] = self.rows
        if self.sources:
            d["sources"] = self.sources
        if self.note:
            d["note"] = self.note
        return d

    def __repr__(self):
        return json.dumps(self.to_dict(), indent=2, default=str)[:600]


def _fmt(v):
    """MIMIC stores NUMERIC(12,4), so 384 comes back as 384.0000."""
    if v is None:
        return None
    f = float(v)
    return int(f) if f == int(f) else round(f, 2)


class GrusTools:
    def __init__(self, conn, hadm_id, as_of_hours=None):
        self.conn = conn
        self.hadm_id = hadm_id
        self.as_of = as_of_hours
        self.r = Retriever(conn, hadm_id, as_of_hours)
        self.subject_id = self.r.subject_id
        self.call_log = []

    def _log(self, name, args, result):
        self.call_log.append({
            "tool": name, "args": args,
            "found": result.found, "n_sources": len(result.sources),
        })
        return result

    # ===============================================================
    # Discovery — what exists for this patient
    # ===============================================================

    def list_available_labs(self):
        """
        Which labs this patient actually has. Call this before get_lab
        when unsure of the exact name — MIMIC uses 'INR(PT)', not 'INR',
        and 'Urea Nitrogen', not 'BUN'.
        """
        rows = self.conn.execute(f"""
            SELECT label, COUNT(*) AS n,
                   MIN(hours_since_admit), MAX(hours_since_admit)
            FROM labs
            WHERE hadm_id = %s AND valuenum IS NOT NULL
              {self.r._cap()}
            GROUP BY label ORDER BY label
        """, (self.hadm_id,)).fetchall()

        if not rows:
            return self._log("list_available_labs", {}, ToolResult(
                False, "No labs on file for this admission."))

        data = [{"label": r[0], "n_results": r[1],
                 "first_hours": _fmt(r[2]), "last_hours": _fmt(r[3])}
                for r in rows]
        return self._log("list_available_labs", {}, ToolResult(
            True, f"{len(rows)} lab types available.", data))

    def list_available_vitals(self):
        """Which vitals this patient has, by short code."""
        rows = self.conn.execute(f"""
            SELECT vital_code, label, COUNT(*), MAX(hours_since_admit)
            FROM vitals
            WHERE hadm_id = %s AND valuenum IS NOT NULL AND vital_code IS NOT NULL
              {self.r._cap()}
            GROUP BY 1,2 ORDER BY 1
        """, (self.hadm_id,)).fetchall()

        if not rows:
            return self._log("list_available_vitals", {}, ToolResult(
                False, "No vitals recorded for this admission.",
                note="ICU charting begins at ICU admission. Emergency "
                     "department vitals are often not captured."))

        data = [{"code": r[0], "label": r[1], "n_readings": r[2],
                 "latest_hours": _fmt(r[3])} for r in rows]
        return self._log("list_available_vitals", {}, ToolResult(
            True, f"{len(rows)} vital signs available.", data))

    def list_note_sections(self):
        """Which sections of the clinical notes exist, current and prior."""
        rows = self.conn.execute("""
            SELECT section, COUNT(*),
                   BOOL_OR(hadm_id = %s) AS in_current
            FROM note_chunks WHERE subject_id = %s
            GROUP BY 1 ORDER BY 2 DESC
        """, (self.hadm_id, self.subject_id)).fetchall()

        if not rows:
            return self._log("list_note_sections", {}, ToolResult(
                False, "No clinical notes on file for this patient.",
                note="Discharge summaries are written at discharge. A "
                     "patient who died or was transferred may have none."))

        data = [{"section": r[0], "chunks": r[1], "in_current_admission": r[2]}
                for r in rows]
        return self._log("list_note_sections", {}, ToolResult(
            True, f"{len(rows)} note sections available.", data))

    # ===============================================================
    # Labs
    # ===============================================================

    def get_lab(self, label, mode="trend"):
        """
        Any lab, by exact MIMIC label.

        mode: 'trend' (all values), 'latest', 'first' (arrival value),
              'nadir' (lowest), 'peak' (highest)

        Use list_available_labs first if unsure of the name.
        """
        rows = self.conn.execute(f"""
            SELECT lab_id, label, valuenum, valueuom, hours_since_admit,
                   flag, ref_range_lower, ref_range_upper
            FROM labs
            WHERE hadm_id = %s AND label = %s AND valuenum IS NOT NULL
              {self.r._cap()}
            ORDER BY hours_since_admit
        """, (self.hadm_id, label)).fetchall()

        args = {"label": label, "mode": mode}

        if not rows:
            near = self.conn.execute("""
                SELECT DISTINCT label FROM labs
                WHERE hadm_id = %s AND label ILIKE %s LIMIT 5
            """, (self.hadm_id, f"%{label.split('(')[0].strip()}%")).fetchall()
            note = (f"Similar labels on file: {[n[0] for n in near]}"
                    if near else "Call list_available_labs to see what exists.")
            return self._log("get_lab", args, ToolResult(
                False, f"No '{label}' results for this admission.", note=note))

        if mode == "latest":
            sel = [rows[-1]]
        elif mode == "first":
            sel = [rows[0]]
        elif mode == "nadir":
            sel = [min(rows, key=lambda r: float(r[2]))]
        elif mode == "peak":
            sel = [max(rows, key=lambda r: float(r[2]))]
        else:
            sel = rows

        data = [{
            "value": _fmt(r[2]), "unit": r[3],
            "hours_since_admit": _fmt(r[4]),
            "pre_arrival": r[4] is not None and float(r[4]) < 0,
            "abnormal": (r[5] or "").lower() == "abnormal",
            "reference_range": (
                f"{_fmt(r[6])}-{_fmt(r[7])}" if r[6] is not None else None),
        } for r in sel]

        vals = [_fmt(r[2]) for r in rows]
        summary = f"{label}: {vals[0]}" if len(vals) == 1 else \
                  f"{label}: {len(vals)} results, {vals[0]} to {vals[-1]}"

        return self._log("get_lab", args, ToolResult(
            True, summary, data,
            [{"table": "labs", "id": r[0]} for r in sel]))

    def get_arrival_labs(self, priority_only=True):
        """
        What the patient walked in with, before the hospital changed
        anything. Priority order: coagulation, then haematology, then
        chemistry.
        """
        ev = self.r.arrival_labs(priority_only=priority_only, limit=15)
        if not ev:
            return self._log("get_arrival_labs", {}, ToolResult(
                False, "No arrival labs on file."))

        data = [{"label": e.label, "value": _fmt(e.value), "unit": e.unit,
                 "abnormal": e.is_abnormal, "when": e.when()} for e in ev]
        return self._log("get_arrival_labs", {"priority_only": priority_only},
                         ToolResult(True, f"{len(ev)} arrival labs.", data,
                                    [e.source() for e in ev]))

    # ===============================================================
    # Vitals
    # ===============================================================

    def get_vital(self, code, mode="latest"):
        """
        Any vital by short code: hr, sbp, dbp, map, spo2, rr, temp_c,
        gcs_eye, gcs_verbal, gcs_motor, weight_kg.

        mode: 'latest', 'trend', 'nadir', 'peak'
        """
        rows = self.conn.execute(f"""
            SELECT vital_id, label, valuenum, valueuom, hours_since_admit
            FROM vitals
            WHERE hadm_id = %s AND vital_code = %s AND valuenum IS NOT NULL
              {self.r._cap()}
            ORDER BY hours_since_admit
        """, (self.hadm_id, code)).fetchall()

        args = {"code": code, "mode": mode}
        if not rows:
            return self._log("get_vital", args, ToolResult(
                False, f"No '{code}' readings in this window.",
                note="Call list_available_vitals to see what exists."))

        if mode == "latest":
            sel = [rows[-1]]
        elif mode == "nadir":
            sel = [min(rows, key=lambda r: float(r[2]))]
        elif mode == "peak":
            sel = [max(rows, key=lambda r: float(r[2]))]
        else:
            sel = rows

        latest_h = _fmt(rows[-1][4])
        age = None
        if self.as_of is not None and latest_h is not None:
            age = round(self.as_of - latest_h, 1)

        data = [{"value": _fmt(r[2]), "unit": r[3],
                 "hours_since_admit": _fmt(r[4])} for r in sel]

        res = ToolResult(True, f"{rows[0][1]}: {_fmt(sel[-1][2])}", data,
                         [{"table": "vitals", "id": r[0]} for r in sel])
        if age is not None and age > 4:
            res.note = f"STALE: this reading is {age}h old."
        return self._log("get_vital", args, res)

    def get_current_vitals(self):
        """Latest reading of every vital, with staleness."""
        ev = self.r.current_vitals()
        if not ev:
            return self._log("get_current_vitals", {}, ToolResult(
                False, "No vitals recorded in this window.",
                note="ICU charting begins at ICU admission; emergency "
                     "department vitals are often absent from the record."))

        data = []
        for e in ev:
            d = {"label": e.label, "value": _fmt(e.value), "hours": e.hours}
            if self.as_of is not None and e.hours is not None:
                age = round(self.as_of - e.hours, 1)
                if age > 4:
                    d["stale_hours"] = age
            data.append(d)

        return self._log("get_current_vitals", {}, ToolResult(
            True, f"{len(ev)} vitals.", data, [e.source() for e in ev]))

    # ===============================================================
    # Medications
    # ===============================================================

    def get_medications(self, drug_class=None, drug_name=None, status=None):
        """
        Medications this admission.

        drug_class: anticoagulant, antiplatelet, nsaid, opioid,
                    reversal_agent, beta_blocker, antibiotic,
                    vasopressor, insulin, diuretic
        status: active, stopped, unknown

        'unknown' status is real — roughly 6% of records, mostly IV
        infusions with no pharmacy dispensing record.
        """
        where, params = ["hadm_id = %s"], [self.hadm_id]
        if drug_class:
            where.append("drug_class = %s")
            params.append(drug_class)
        if drug_name:
            where.append("drug_normalized ILIKE %s")
            params.append(f"%{drug_name}%")
        if status:
            where.append("is_active = %s")
            params.append(status)

        rows = self.conn.execute(f"""
            SELECT medication_id, drug_normalized, drug_class, route,
                   start_hours, stop_hours, is_active, status, time_valid
            FROM medications
            WHERE {' AND '.join(where)} {self.r._cap("start_hours")}
            ORDER BY drug_normalized
        """, params).fetchall()

        args = {"drug_class": drug_class, "drug_name": drug_name,
                "status": status}
        if not rows:
            what = drug_name or drug_class or "medications"
            return self._log("get_medications", args, ToolResult(
                False, f"No {what} on file for this admission.",
                note="Absence of a record is not absence of the drug. Home "
                     "medications are frequently not reconciled on admission "
                     "— check the clinical notes."))

        data = [{
            "drug": r[1], "class": r[2], "route": r[3],
            "start_hours": _fmt(r[4]), "stop_hours": _fmt(r[5]),
            "status": r[6],
            "timing_reliable": r[8],
        } for r in rows]

        res = ToolResult(True, f"{len(rows)} medications.", data,
                         [{"table": "medications", "id": r[0]} for r in rows])
        if any(not r[8] for r in rows):
            res.note = ("Some rows have a stop time before their start time; "
                        "status for those cannot be determined.")
        return self._log("get_medications", args, res)

    # Drugs given in hospital, not taken at home. Prior-admission
    # prescriptions include everything the patient received as an
    # inpatient — antibiotics, IV drips, one-off doses. Returning those
    # as 'home medications' is misleading.
    INPATIENT_CLASSES = {"antibiotic", "vasopressor", "reversal_agent"}

    def get_home_medications(self):
        """
        What the patient was taking BEFORE arrival.

        Looks in three places, in order of reliability:
          1. The 'Medications on Admission' section of the notes — this is
             the reconciled list, written by a clinician.
          2. Discharge medications from the last admission — what they
             were sent home on.
          3. Chronic drug classes from prior admissions.

        Home medications are frequently absent from the structured record.
        The trauma demo patient arrived with an INR of 6.3 and no
        anticoagulant in any medication table; the warfarin was only ever
        written in the notes.
        """
        sources, parts, data = [], [], []

        # 1. the reconciled list
        admission_list = self.r.notes_by_section(
            "Medications on Admission", "Discharge Medications", k=4)
        if admission_list:
            for e in admission_list:
                tag = ("this admission" if e.is_current_admission
                       else "previous admission")
                data.append({"source": f"{e.section} ({tag})",
                             "text": e.text[:1500]})
                sources.append(e.source())
            parts.append(f"Reconciled medication list found in the notes "
                         f"({len(admission_list)} section(s)).")

        # 2. chronic drugs from prior admissions, inpatient-only classes removed
        #
        # Only fall back to this when no reconciled list exists. Prior
        # prescriptions include everything given as an inpatient, so a
        # list built from them reads as home therapy when it is not —
        # the cardiac patient's "home medications" came back naming
        # ampicillin and ceftriaxone.
        prior = self.r.prior_medications()
        chronic = [e for e in prior
                   if (e.value or "") not in self.INPATIENT_CLASSES]
        excluded = len(prior) - len(chronic)

        if chronic and not admission_list:
            drugs = sorted({e.label for e in chronic})
            parts.append(f"No reconciled list. Inferred from previous "
                         f"admissions: {', '.join(drugs[:20])}")
            sources += [e.source() for e in chronic]

        if not parts:
            return self._log("get_home_medications", {}, ToolResult(
                False, "No home medication list on file.",
                note="No reconciled list in the notes and no prior "
                     "admissions. ASK the patient or family before "
                     "prescribing — particularly about anticoagulants."))

        res = ToolResult(True, " ".join(parts), data, sources)
        notes = []
        if excluded and not admission_list:
            notes.append(f"{excluded} inpatient-only drugs (antibiotics, "
                         f"vasopressors, reversal agents) excluded — those "
                         f"were given in hospital, not taken at home.")
        if not admission_list:
            notes.append("No reconciled admission list in the notes; the "
                         "list above is inferred from prior prescriptions "
                         "and may not reflect current home therapy.")
        if notes:
            res.note = " ".join(notes)
        return self._log("get_home_medications", {}, res)

    # ===============================================================
    # Diagnoses, procedures, outputs
    # ===============================================================

    def get_diagnoses(self, current_only=True):
        """Coded diagnoses, principal first."""
        if current_only:
            ev = self.r.diagnoses()
            if not ev:
                return self._log("get_diagnoses", {}, ToolResult(
                    False, "No coded diagnoses for this admission."))
            data = [{"code": e.value, "title": e.label} for e in ev]
            return self._log("get_diagnoses", {"current_only": True},
                             ToolResult(True, f"{len(ev)} diagnoses.", data,
                                        [e.source() for e in ev]))

        ev = self.r.recurring_diagnoses(min_visits=1)
        data = [{"title": e.label, "seen_in": e.value} for e in ev]
        return self._log("get_diagnoses", {"current_only": False},
                         ToolResult(bool(ev), f"{len(ev)} across prior visits.",
                                    data, [e.source() for e in ev]))

    def get_recurring_diagnoses(self, min_visits=2):
        """
        Conditions appearing across several previous admissions.
        'Fifth presentation with heart failure' is a pattern nobody
        would look up manually.
        """
        ev = self.r.recurring_diagnoses(min_visits)
        if not ev:
            n = len(self.r.prior_admissions())
            return self._log("get_recurring_diagnoses", {"min_visits": min_visits},
                             ToolResult(False,
                                        "No recurring conditions found."
                                        if n else "No prior admissions on file.",
                                        note=None if n else
                                        "First presentation at this facility. "
                                        "Records may exist elsewhere."))

        data = [{"condition": e.label, "visits": e.value} for e in ev]
        return self._log("get_recurring_diagnoses", {"min_visits": min_visits},
                         ToolResult(True, f"{len(ev)} recurring conditions.",
                                    data, [e.source() for e in ev]))

    def get_procedures(self):
        rows = self.conn.execute("""
            SELECT procedure_id, icd_code, long_title, hours_since_admit
            FROM procedures WHERE hadm_id = %s ORDER BY seq_num NULLS LAST
        """, (self.hadm_id,)).fetchall()

        if not rows:
            return self._log("get_procedures", {}, ToolResult(
                False, "No coded procedures for this admission."))

        data = [{"code": r[1], "title": r[2], "hours": _fmt(r[3])} for r in rows]
        return self._log("get_procedures", {}, ToolResult(
            True, f"{len(rows)} procedures.", data,
            [{"table": "procedures", "id": r[0]} for r in rows]))

    def get_outputs(self, label=None):
        """
        Drains and urine output. Drain volumes are a direct measure of
        bleeding — often the most concrete number in a haemorrhage case.
        """
        where, params = ["hadm_id = %s", "value IS NOT NULL"], [self.hadm_id]
        if label:
            where.append("label ILIKE %s")
            params.append(f"%{label}%")

        rows = self.conn.execute(f"""
            SELECT output_id, label, value, valueuom, hours_since_admit
            FROM outputs WHERE {' AND '.join(where)} {self.r._cap()}
            ORDER BY hours_since_admit
        """, params).fetchall()

        if not rows:
            return self._log("get_outputs", {"label": label}, ToolResult(
                False, f"No {label or 'output'} records in this window."))

        data = [{"label": r[1], "value": _fmt(r[2]), "unit": r[3],
                 "hours": _fmt(r[4])} for r in rows]
        return self._log("get_outputs", {"label": label}, ToolResult(
            True, f"{len(rows)} output records.", data,
            [{"table": "outputs", "id": r[0]} for r in rows]))

    # ===============================================================
    # Notes and history
    # ===============================================================

    def search_notes(self, question, keywords=None, k=5, prior_only=False):
        """
        Search the clinical notes. Runs semantic search and, when keywords
        are given, exact matching too.

        Both are needed. Semantic search found a cardiac history at 0.417
        similarity but scored only 0.185 on a passage naming vitamin K —
        general-purpose embeddings map drug names poorly. Keyword matching
        catches those reliably.
        """
        kw = tuple(keywords) if keywords else ()
        ev = self.r.find_in_notes(question, keywords=kw, k=k)
        if prior_only:
            ev = [e for e in ev if not e.is_current_admission]

        args = {"question": question, "keywords": list(kw)}
        if not ev:
            return self._log("search_notes", args, ToolResult(
                False, "Nothing relevant in the notes.",
                note="Try different wording, or call list_note_sections."))

        data = [{
            "section": e.section,
            "from": "current admission" if e.is_current_admission else "prior admission",
            "match": "semantic" if e.similarity else "keyword",
            "similarity": e.similarity,
            "text": e.text[:1200],
        } for e in ev]

        return self._log("search_notes", args, ToolResult(
            True, f"{len(ev)} relevant passages.", data,
            [e.source() for e in ev]))

    def get_admission_history(self):
        """Previous admissions: when, why, how long."""
        rows = self.conn.execute("""
            SELECT a.hadm_id, a.admittime, a.admission_type, a.hosp_days,
                   a.hospital_expire_flag
            FROM admissions a
            WHERE a.subject_id = %s AND a.is_current = FALSE
            ORDER BY a.admittime DESC
        """, (self.subject_id,)).fetchall()

        if not rows:
            return self._log("get_admission_history", {}, ToolResult(
                False, "No prior admissions at this facility.",
                note="First presentation here. No baseline vitals, no home "
                     "medication list, no allergy history. Records may exist "
                     "at another hospital."))

        data = []
        for r in rows:
            dx = self.conn.execute("""
                SELECT long_title FROM diagnoses WHERE hadm_id = %s
                ORDER BY seq_num NULLS LAST LIMIT 3
            """, (r[0],)).fetchall()
            data.append({
                "admission_type": r[2],
                "length_of_stay_days": _fmt(r[3]),
                "principal_diagnoses": [d[0] for d in dx if d[0]],
            })

        return self._log("get_admission_history", {}, ToolResult(
            True, f"{len(rows)} prior admissions.", data))

    def get_patient_summary(self):
        """Demographics and admission context. Cheap orienting call."""
        r = self.conn.execute("""
            SELECT p.gender, p.anchor_age, a.admission_type, a.arrival_unit,
                   a.cohort, a.hosp_days, a.icu_days
            FROM admissions a JOIN patients p ON p.subject_id = a.subject_id
            WHERE a.hadm_id = %s
        """, (self.hadm_id,)).fetchone()

        n_prior = len(self.r.prior_admissions())
        data = [{
            "age": r[1], "sex": r[0], "admission_type": r[2],
            "arrival_unit": r[3], "category": r[4],
            "prior_admissions": n_prior,
            "as_of_hours": self.as_of,
        }]
        return self._log("get_patient_summary", {}, ToolResult(
            True, f"{r[1]}{r[0]}, {r[2]}.", data))

    # ===============================================================
    # Alerts already computed by the rule engine
    # ===============================================================

    def get_active_alerts(self):
        """
        Alerts the rule engine produced for this patient.

        Read these rather than recomputing. The trace records what each
        rule saw and where it read it, so an explanation built from a
        trace cannot invent a reason.
        """
        from grus_rules import evaluate
        alerts = evaluate(self.conn, self.hadm_id, self.as_of, persist=False)

        if not alerts:
            return self._log("get_active_alerts", {}, ToolResult(
                True, "No alerts triggered.",
                note="No rule thresholds crossed with the data available. "
                     "This is not the same as no risk."))

        data = [{
            "code": a.code, "severity": a.severity, "title": a.title,
            "detail": a.body, "action": a.action, "inputs": a.inputs,
        } for a in alerts]

        srcs = [s for a in alerts for s in a.sources]
        return self._log("get_active_alerts", {}, ToolResult(
            True, f"{len(alerts)} alerts.", data, srcs))


# ===================================================================
# Schemas for the model
# ===================================================================

TOOL_SCHEMAS = [
        {
        "name": "calculate_score",
        "description": (
            "Compute a validated clinical score. Fifteen encoded: PERC, "
            "Wells PE, HEART, qSOFA, SIRS, HAS-BLED, CHA2DS2-VASc, "
            "CURB-65, NEWS2, MEWS, KDIGO AKI, Shock Index, "
            "Glasgow-Blatchford, SOFA respiratory, anion gap. "
            "Criteria the record cannot supply come back under "
            "components.missing with the question to ask. When anything "
            "is missing there is no total — report the questions, not a "
            "partial number."),
        "input_schema": {
            "type": "object",
            "properties": {
                "score_name": {"type": "string",
                               "description": "e.g. 'PERC', 'HEART'"},
                "provided": {"type": "object",
                             "description": "answers the clinician gave"},
            },
            "required": ["score_name"],
        },
    },
    {
        "name": "suggest_scores",
        "description": "Which scores are worth running for this patient.",
        "input_schema": {
            "type": "object",
            "properties": {
                "presentation": {"type": "string",
                                 "description": "e.g. 'chest pain'"},
            },
        },
    },
    {
        "name": "get_patient_summary",
        "description": "Demographics, admission type, and how many prior admissions exist. Cheap orienting call — use first.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_active_alerts",
        "description": "Alerts the deterministic rule engine produced, with the values each rule saw. Prefer this over recomputing risk yourself.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_available_labs",
        "description": "Which labs this patient has, with exact MIMIC labels. Call before get_lab if unsure of a name — MIMIC uses 'INR(PT)' not 'INR', 'Urea Nitrogen' not 'BUN'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_lab",
        "description": "Any lab result by exact label.",
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Exact MIMIC label, e.g. 'INR(PT)'"},
                "mode": {"type": "string", "enum": ["trend", "latest", "first", "nadir", "peak"],
                         "description": "'first' is the arrival value; 'nadir' the lowest, which is what matters for bleeding risk"},
            },
            "required": ["label"],
        },
    },
    {
        "name": "get_arrival_labs",
        "description": "What the patient arrived with, before treatment. Coagulation and haematology first.",
        "input_schema": {
            "type": "object",
            "properties": {"priority_only": {"type": "boolean"}},
        },
    },
    {
        "name": "list_available_vitals",
        "description": "Which vitals exist for this patient.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_vital",
        "description": "A vital sign by short code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "hr, sbp, dbp, map, spo2, rr, temp_c, gcs_eye, gcs_verbal, gcs_motor, weight_kg"},
                "mode": {"type": "string", "enum": ["latest", "trend", "nadir", "peak"]},
            },
            "required": ["code"],
        },
    },
    {
        "name": "get_current_vitals",
        "description": "Latest reading of every vital, flagged if stale.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_medications",
        "description": "Medications this admission. Filter by class, name, or status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drug_class": {"type": "string", "description": "anticoagulant, antiplatelet, nsaid, opioid, reversal_agent, beta_blocker, antibiotic, vasopressor, insulin, diuretic"},
                "drug_name": {"type": "string"},
                "status": {"type": "string", "enum": ["active", "stopped", "unknown"]},
            },
        },
    },
    {
        "name": "get_home_medications",
        "description": "What the patient took before arrival, from prior admissions and the notes. Home medications are often absent from the structured record — this is how an anticoagulant gets missed.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_diagnoses",
        "description": "Coded diagnoses, principal first.",
        "input_schema": {
            "type": "object",
            "properties": {"current_only": {"type": "boolean"}},
        },
    },
    {
        "name": "get_recurring_diagnoses",
        "description": "Conditions appearing across several prior admissions.",
        "input_schema": {
            "type": "object",
            "properties": {"min_visits": {"type": "integer"}},
        },
    },
    {
        "name": "get_procedures",
        "description": "Coded procedures for this admission.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_outputs",
        "description": "Drain and urine volumes. Drain output is a direct measure of bleeding.",
        "input_schema": {
            "type": "object",
            "properties": {"label": {"type": "string", "description": "e.g. 'Pericardial', 'Void'"}},
        },
    },
    {
        "name": "search_notes",
        "description": "Search clinical notes semantically, plus exact keyword matching when keywords are supplied. Use keywords for drug names — embeddings map those poorly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "k": {"type": "integer"},
                "prior_only": {"type": "boolean"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "get_admission_history",
        "description": "Previous admissions with their principal diagnoses.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_note_sections",
        "description": "Which note sections exist for this patient.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def dispatch(tools: GrusTools, name, args):
    """Route a model tool call. Unknown names fail loudly, not silently."""
    args = args or {}

    # Scores live in their own engine rather than on GrusTools, so they
    # are handled before the attribute lookup below.
    if name == "calculate_score":
        try:
            from grus_score_engine import ScoreEngine
            eng = ScoreEngine(tools.conn, tools.hadm_id, tools.as_of)
            r = eng.compute(args.get("score_name"), args.get("provided") or {})
            if r is None:
                return ToolResult(False, f"No score '{args.get('score_name')}'.")
            d = r.to_dict()
            return ToolResult(True, d.get("name", ""), [d], r.sources)
        except Exception as e:
            tools.conn.rollback()
            return ToolResult(False, f"Score failed: {str(e)[:120]}")

    if name == "suggest_scores":
        try:
            from grus_score_engine import ScoreEngine
            from grus_scores import SCORES
            eng = ScoreEngine(tools.conn, tools.hadm_id, tools.as_of)
            keys = eng.suggest(args.get("presentation"))
            return ToolResult(True, f"{len(keys)} scores apply",
                              [{"key": k, "name": SCORES[k]["name"]}
                               for k in keys], [])
        except Exception as e:
            tools.conn.rollback()
            return ToolResult(False, f"Score failed: {str(e)[:120]}")

    fn = getattr(tools, name, None)
    if fn is None or name.startswith("_"):
        return ToolResult(False, f"No tool named '{name}'.",
                          note=f"Available: {[t['name'] for t in TOOL_SCHEMAS]}")
    try:
        return fn(**args)
    except TypeError as e:
        return ToolResult(False, f"Bad arguments for {name}: {e}")    


if __name__ == "__main__":
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus", user="grusadmin",
        password=PWD, sslmode="require", keepalives=1, keepalives_idle=30)
    conn.execute("SET search_path TO grus, public")

    for label, hadm, as_of in [("TRAUMA hour 1", 28173870, 1),
                               ("CARDIAC full", 27180495, None)]:
        print("\n" + "=" * 62)
        print(label)
        print("=" * 62)
        t = GrusTools(conn, hadm, as_of)

        print("\n-- summary --");           print(t.get_patient_summary())
        print("\n-- arrival labs --");      print(t.get_arrival_labs())
        print("\n-- INR nadir --");         print(t.get_lab("INR(PT)", "first"))
        print("\n-- a lab nobody wrote a rule for --")
        print(t.get_lab("Magnesium", "trend"))
        print("\n-- anticoagulants --");    print(t.get_medications(drug_class="anticoagulant"))
        print("\n-- home meds --");         print(t.get_home_medications())
        print("\n-- vitals --");            print(t.get_current_vitals())
        print("\n-- missing tool --");      print(dispatch(t, "get_ecg", {}))
        print("\n-- missing lab --");       print(t.get_lab("Troponin"))
        print(f"\ncalls made: {len(t.call_log)}")

    conn.close()