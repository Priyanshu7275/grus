"""
GRUS — Score engine

Computes a clinical score from the record, and says plainly what it
could not find.

The arithmetic is here, in Python. The model never calculates — it picks
which score applies and explains the result. A score computed by a
language model is a number nobody can check.

Most scores need findings no database holds. PERC asks about unilateral
leg swelling; MIMIC has no column for that. So a score is usually
PARTIAL: what was found, what is missing, and the question to ask for
each missing piece.

A partial score is an honest answer. A score computed by assuming absent
findings are negative is not — PERC assumes every criterion is checked,
and silently treating unknowns as negative would turn 'not assessed' into
'rules out PE'.
"""

import os
import re
import json
import psycopg
from dataclasses import dataclass, field
from typing import Optional
from grus_config import DB

from grus_scores import SCORES, ALIASES, BY_PRESENTATION, resolve, list_scores



HOST = DB.HOST
PWD = DB.PASSWORD

@dataclass
class InputValue:
    key: str
    label: str
    kind: str
    found: bool
    value: object = None
    points: float = 0
    source: Optional[dict] = None
    ask: Optional[str] = None
    note: Optional[str] = None


@dataclass
class ScoreResult:
    key: str
    name: str
    purpose: str
    citation: str
    complete: bool
    total: Optional[float] = None
    max_possible: Optional[float] = None
    risk: Optional[str] = None
    interpretation: Optional[str] = None
    caution: Optional[str] = None
    found: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    note: Optional[str] = None

    def to_dict(self):
        d = {
            "score": self.key, "name": self.name, "purpose": self.purpose,
            "citation": self.citation, "complete": self.complete,
            "components": {
                "found": [{"label": i.label, "value": i.value,
                           "points": i.points,
                           "source": f"{i.source['table']}#{i.source['id']}"
                           if i.source else None} for i in self.found],
                "missing": [{"label": i.label, "ask": i.ask,
                             "why": "not recorded in the structured data"}
                            for i in self.missing],
            },
        }
        if self.total is not None:
            d["total"] = self.total
            d["max_possible"] = self.max_possible
            d["risk"] = self.risk
            d["interpretation"] = self.interpretation
        if self.caution:
            d["caution"] = self.caution
        if self.note:
            d["note"] = self.note
        if self.sources:
            d["sources"] = self.sources
        return d


class ScoreEngine:
    def __init__(self, conn, hadm_id, as_of_hours=None):
        self.conn = conn
        self.hadm_id = hadm_id
        self.as_of = as_of_hours
        self._cache = {}

        row = conn.execute("""
            SELECT p.anchor_age, p.gender, a.subject_id
            FROM admissions a JOIN patients p ON p.subject_id = a.subject_id
            WHERE a.hadm_id = %s
        """, (hadm_id,)).fetchone()
        if not row:
            raise ValueError(f"no admission {hadm_id}")
        self.age, self.sex, self.subject_id = row

    def _cap(self, col="hours_since_admit"):
        if self.as_of is None:
            return ""
        return f" AND ({col} IS NULL OR {col} <= {self.as_of}) "

    # -----------------------------------------------------------
    # Fetchers
    # -----------------------------------------------------------
    def _vital(self, code, aggregate="latest"):
        key = ("v", code, aggregate)
        if key in self._cache:
            return self._cache[key]

        if code == "gcs_total":
            r = self.conn.execute(f"""
                SELECT MAX(vital_id), SUM(valuenum) FROM (
                    SELECT DISTINCT ON (vital_code) vital_id, valuenum
                    FROM vitals
                    WHERE hadm_id = %s
                      AND vital_code IN ('gcs_eye','gcs_verbal','gcs_motor')
                      AND valuenum IS NOT NULL {self._cap()}
                    ORDER BY vital_code, hours_since_admit DESC
                ) t
            """, (self.hadm_id,)).fetchone()
            out = (float(r[1]), {"table": "vitals", "id": r[0]}) \
                if r and r[1] is not None else (None, None)
            self._cache[key] = out
            return out

        order = {"latest": "hours_since_admit DESC",
                 "min": "valuenum ASC", "max": "valuenum DESC"}[aggregate]
        r = self.conn.execute(f"""
            SELECT vital_id, valuenum FROM vitals
            WHERE hadm_id = %s AND vital_code = %s AND valuenum IS NOT NULL
              {self._cap()}
            ORDER BY {order} LIMIT 1
        """, (self.hadm_id, code)).fetchone()

        out = (float(r[1]), {"table": "vitals", "id": r[0]}) if r else (None, None)
        self._cache[key] = out
        return out

    def _lab(self, label, aggregate="latest"):
        key = ("l", label, aggregate)
        if key in self._cache:
            return self._cache[key]

        order = {"latest": "hours_since_admit DESC",
                 "first": "hours_since_admit ASC",
                 "min": "valuenum ASC", "max": "valuenum DESC"}[aggregate]
        r = self.conn.execute(f"""
            SELECT lab_id, valuenum FROM labs
            WHERE hadm_id = %s AND label = %s AND valuenum IS NOT NULL
              {self._cap()}
            ORDER BY {order} LIMIT 1
        """, (self.hadm_id, label)).fetchone()

        out = (float(r[1]), {"table": "labs", "id": r[0]}) if r else (None, None)
        self._cache[key] = out
        return out

    def _has_diagnosis(self, codes_9=None, codes_10=None):
        """
        Diagnosis prefix match, version-aware.

        ICD-9 and ICD-10 reuse letters for different things, so matching
        on a prefix without checking the version reports a heart valve
        replacement as a transport accident.
        """
        rows = self.conn.execute("""
            SELECT diagnosis_id, icd_code, icd_version, long_title
            FROM diagnoses WHERE subject_id = %s
        """, (self.subject_id,)).fetchall()

        for d in rows:
            code, ver = d[1], d[2]
            prefixes = (codes_9 if ver == 9 else codes_10) or []
            for p in prefixes:
                if "-" in p and ver == 9 and code[:3].isdigit():
                    lo, hi = p.split("-")
                    if int(lo) <= int(code[:3]) <= int(hi):
                        return True, {"table": "diagnoses", "id": d[0]}, d[3]
                elif code.startswith(p):
                    return True, {"table": "diagnoses", "id": d[0]}, d[3]
        return False, None, None

    def _count_diagnoses(self, code_map):
        """How many of a set of conditions the patient has."""
        rows = self.conn.execute("""
            SELECT diagnosis_id, icd_code, icd_version FROM diagnoses
            WHERE subject_id = %s
        """, (self.subject_id,)).fetchall()

        hits, srcs = set(), []
        for d in rows:
            code, ver = d[1], d[2]
            for p in code_map.get(str(ver), []):
                if code.startswith(p):
                    hits.add(p)
                    srcs.append({"table": "diagnoses", "id": d[0]})
        return len(hits), srcs

    def _has_med(self, drug_class=None, drugs=None):
        where, params = ["hadm_id = %s"], [self.hadm_id]
        if drug_class:
            where.append("drug_class = %s")
            params.append(drug_class)
        if drugs:
            where.append("(" + " OR ".join(
                ["drug_normalized ILIKE %s"] * len(drugs)) + ")")
            params += [f"%{d}%" for d in drugs]

        r = self.conn.execute(f"""
            SELECT medication_id, drug_normalized FROM medications
            WHERE {' AND '.join(where)} LIMIT 1
        """, params).fetchone()
        return (True, {"table": "medications", "id": r[0]}, r[1]) if r \
            else (False, None, None)

    # -----------------------------------------------------------
    # Tests
    # -----------------------------------------------------------
    @staticmethod
    def _test(value, expr):
        """
        Evaluate a threshold expression against a value.

        Supported: >=50  >100  <95  <=100  ==F
                   outside:36:38    between:65:74
        """
        if value is None:
            return None
        try:
            if expr.startswith("outside:"):
                _, lo, hi = expr.split(":")
                return float(value) < float(lo) or float(value) > float(hi)
            if expr.startswith("between:"):
                _, lo, hi = expr.split(":")
                return float(lo) <= float(value) <= float(hi)
            if expr.startswith(">="):
                return float(value) >= float(expr[2:])
            if expr.startswith("<="):
                return float(value) <= float(expr[2:])
            if expr.startswith(">"):
                return float(value) > float(expr[1:])
            if expr.startswith("<"):
                return float(value) < float(expr[1:])
            if expr.startswith("=="):
                return str(value).upper() == expr[2:].upper()
        except (ValueError, TypeError):
            return None
        return None

    @staticmethod
    def _band(value, bands, descending=False):
        """
        Points from a banded threshold.

        bands ascending:  [[45, 0], [65, 1], [999, 2]]  -> under 45 scores 0
        bands descending: [[91, 3], [93, 2], [100, 0]]  -> under 91 scores 3
        """
        if value is None:
            return None
        v = float(value)
        for threshold, points in bands:
            if descending:
                if v <= float(threshold):
                    return points
            else:
                if v <= float(threshold):
                    return points
        return bands[-1][1]

    # -----------------------------------------------------------
    # Resolve one input
    # -----------------------------------------------------------
    def _resolve_input(self, spec, provided):
        key = spec["key"]
        label = spec["label"]
        kind = spec["kind"]

        # A value the clinician supplied overrides anything found.
        if key in provided:
            v = provided[key]
            pts = self._points_from(spec, v, supplied=True)
            return InputValue(key, label, kind, True, v, pts,
                              note="supplied by clinician")

        if kind == "demo":
            v = self.age if spec["field"] == "age" else self.sex
            pts = self._points_from(spec, v)
            return InputValue(key, label, kind, True, v, pts or 0)

        if kind == "vital":
            v, src = self._vital(spec["field"], spec.get("aggregate", "latest"))
            if v is None:
                return InputValue(key, label, kind, False,
                                  ask=f"What is the {label.lower()}?")
            return InputValue(key, label, kind, True, v,
                              self._points_from(spec, v) or 0, src)

        if kind == "lab":
            v, src = self._lab(spec["field"], spec.get("aggregate", "latest"))
            if v is None:
                return InputValue(key, label, kind, False,
                                  ask=f"What is the {spec['field']}?")
            return InputValue(key, label, kind, True, v,
                              self._points_from(spec, v) or 0, src)

        if kind == "dx":
            if "count_codes" in spec:
                n, srcs = self._count_diagnoses(spec["count_codes"])
                pts = self._band(n, spec["bands_from_count"])
                return InputValue(key, label, kind, True, f"{n} present",
                                  pts or 0, srcs[0] if srcs else None)
            has, src, title = self._has_diagnosis(
                spec.get("codes_9"), spec.get("codes_10"))
            return InputValue(key, label, kind, True,
                              title if has else "not coded",
                              spec.get("points", 0) if has else 0, src)

        if kind == "med":
            has, src, drug = self._has_med(spec.get("drug_class"),
                                           spec.get("drugs"))
            return InputValue(key, label, kind, True,
                              drug if has else "not prescribed",
                              spec.get("points", 0) if has else 0, src)

        # clinical — examination or history. Never in the record.
        return InputValue(key, label, kind, False,
                          ask=spec.get("ask", f"{label}?"))

    def _points_from(self, spec, value, supplied=False):
        """Points for a value, by whichever weighting the spec declares."""
        if spec.get("raw"):
            return 0

        if "scale" in spec:
            return spec["scale"].get(str(value).lower(), 0)
        if "news_bands" in spec:
            return self._band(value, spec["news_bands"])
        if "news_bands_desc" in spec:
            return self._band(value, spec["news_bands_desc"], descending=True)
        if "bands" in spec:
            return self._band(value, spec["bands"])
        if "bands_desc" in spec:
            return self._band(value, spec["bands_desc"], descending=True)
        if "test" in spec:
            r = self._test(value, spec["test"])
            if r is None:
                return None
            return spec.get("points", 1) if r else 0
        if supplied:
            # A boolean answer from the clinician.
            truthy = str(value).lower() in ("yes", "true", "1", "y", "present")
            return spec.get("points", 1) if truthy else 0
        return 0

    # -----------------------------------------------------------
    # Special cases
    # -----------------------------------------------------------
    def _kdigo(self, found):
        """
        KDIGO staging needs a baseline, and the baseline is the value the
        patient arrived with — not the lowest ever recorded.

        Using the running minimum looks reasonable until a patient
        recovers: their creatinine falls back, the minimum becomes the
        final value, and comparing the latest against it gives a ratio of
        1.0. A patient whose creatinine went 1.4 to 3.3 and back would be
        reported as having no kidney injury at all.

        So: baseline is the first value, and staging is reported against
        the peak as well as the current value. An AKI that has resolved
        still happened, and it still changes what is safe to prescribe.
        """
        vals = {i.key: i.value for i in found}
        now, base = vals.get("creat_now"), vals.get("creat_baseline")
        peak = vals.get("creat_peak")

        if now is None or base is None or base == 0:
            return None, "Needs a current and an admission creatinine."

        def stage(value):
            if value is None:
                return 0
            if value / base >= 3 or value >= 4.0:
                return 3
            if value / base >= 2:
                return 2
            if value / base >= 1.5 or value - base >= 0.3:
                return 1
            return 0

        current_stage = stage(now)
        peak_stage = stage(peak)

        # Report the worse of the two. A resolved injury is still an
        # injury, and a doctor prescribing contrast needs to know.
        return max(current_stage, peak_stage), None

    def _shock_index(self, found):
        vals = {i.key: i.value for i in found}
        hr, sbp = vals.get("hr"), vals.get("sbp")
        if not hr or not sbp:
            return None, "Needs both heart rate and systolic pressure."
        return round(hr / sbp, 2), None

    def _anion_gap(self, found):
        vals = {i.key: i.value for i in found}
        na, cl, hco3 = (vals.get("sodium"), vals.get("chloride"),
                        vals.get("bicarbonate"))
        if None in (na, cl, hco3):
            return None, "Needs sodium, chloride and bicarbonate."
        return round(na - (cl + hco3), 1), None

    # -----------------------------------------------------------
    # Compute
    # -----------------------------------------------------------
    def compute(self, score_key, provided=None):
        key = resolve(score_key)
        if not key:
            return None

        spec = SCORES[key]
        provided = provided or {}

        found, missing, sources = [], [], []
        for inp in spec["inputs"]:
            v = self._resolve_input(inp, provided)
            if v.found:
                found.append(v)
                if v.source and v.source not in sources:
                    sources.append(v.source)
            else:
                missing.append(v)

        result = ScoreResult(
            key=key, name=spec["name"], purpose=spec["purpose"],
            citation=spec["citation"],
            complete=not missing,
            found=found, missing=missing, sources=sources,
            caution=spec.get("caution"),
        )

        custom = spec.get("custom")
        if custom:
            fn = {"kdigo": self._kdigo, "shock_index": self._shock_index,
                  "anion_gap": self._anion_gap}[custom]
            total, err = fn(found)
            if total is None:
                result.note = err
                return result
            result.total = total
        else:
            if missing:
                # Partial totals invite being read as the real score.
                # PERC in particular assumes every criterion was checked;
                # reporting a running total with three unknowns would turn
                # 'not assessed' into 'rules out PE'.
                result.note = (
                    f"{len(missing)} of {len(spec['inputs'])} criteria are "
                    f"not in the record. They need examination or history. "
                    f"No total is given — a partial score reads as a real "
                    f"one, and for a rule-out score that is dangerous.")
                result.max_possible = sum(
                    i.get("points", 2) for i in spec["inputs"])
                return result

            result.total = sum(i.points for i in found)
            result.max_possible = sum(
                i.get("points", 2) for i in spec["inputs"])

        for band in spec["interpretation"]:
            lo = band.get("min", float("-inf"))
            hi = band.get("max", float("inf"))
            if lo <= result.total <= hi:
                result.risk = band["risk"]
                result.interpretation = band["text"]
                break

        return result

    def suggest(self, presentation=None):
        """Which scores are worth running for this patient."""
        if presentation:
            p = presentation.lower()
            for k, scores in BY_PRESENTATION.items():
                if k in p or p in k:
                    return scores

        # Otherwise suggest from what the patient actually has.
        out = []
        if self._lab("INR(PT)")[0]:
            out += ["HAS_BLED", "SHOCK_INDEX"]
        if self._vital("hr")[0] and self._vital("sbp")[0]:
            out += ["SHOCK_INDEX", "NEWS2", "QSOFA"]
        if self._lab("Creatinine")[0]:
            out.append("KDIGO_AKI")
        if self._lab("White Blood Cells")[0]:
            out.append("SIRS")
        seen, uniq = set(), []
        for s in out:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        return uniq[:6]


def render(r: ScoreResult, width=72):
    """A doctor's-eye view of one score."""
    if r is None:
        return "No such score."

    L = ["─" * width, r.name.upper(), r.purpose, "─" * width, ""]

    if r.total is not None:
        band = f"{r.risk.upper()}" if r.risk else ""
        mx = f" / {r.max_possible}" if r.max_possible else ""
        L.append(f"  SCORE  {r.total}{mx}    {band}")
        if r.interpretation:
            L.append("")
            for line in _wrap(r.interpretation, width - 4):
                L.append(f"  {line}")
        L.append("")

    if r.found:
        L.append("  FROM THE RECORD")
        for i in r.found:
            src = f"[{i.source['table']}#{i.source['id']}]" if i.source else ""
            pts = f"+{i.points}" if i.points else " 0"
            note = f"  ({i.note})" if i.note else ""
            L.append(f"    {pts}  {i.label}: {i.value} {src}{note}")
        L.append("")

    if r.missing:
        L.append(f"  NOT IN THE RECORD — {len(r.missing)} item(s)")
        for i in r.missing:
            L.append(f"    ?   {i.label}")
            L.append(f"        {i.ask}")
        L.append("")

    if r.note:
        for line in _wrap(r.note, width - 4):
            L.append(f"  {line}")
        L.append("")

    if r.caution:
        L.append("  CAUTION")
        for line in _wrap(r.caution, width - 4):
            L.append(f"  {line}")
        L.append("")

    L.append(f"  {r.citation}")
    L.append("─" * width)
    return "\n".join(L)


def _wrap(text, width):
    words, lines, cur = (text or "").split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


if __name__ == "__main__":
    from psycopg.rows import tuple_row

    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus", user="grusadmin",
        password=PWD, sslmode="require",
        keepalives=1, keepalives_idle=30, row_factory=tuple_row)
    conn.execute("SET search_path TO grus, public")

    print(f"{len(SCORES)} scores encoded\n")

    HADM = 27180495          # the cardiac patient, full record
    eng = ScoreEngine(conn, HADM)

    print(f"suggested for this patient: {eng.suggest()}\n")

    for name in ["SHOCK_INDEX", "HAS_BLED", "KDIGO_AKI", "PERC"]:
        print(render(eng.compute(name)))
        print()

    # The same score once the clinician answers what the record cannot.
    print("PERC with the clinical findings supplied:\n")
    print(render(eng.compute("PERC", provided={
        "leg_swelling": "no", "haemoptysis": "no",
        "recent_surgery": "no", "hormone_use": "no"})))

    conn.close()