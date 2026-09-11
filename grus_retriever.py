"""
GRUS — Retriever

Hybrid retrieval. SQL for anything with a number or a date, vector search
for anything about narrative or history, keyword search as a safety net.

Why hybrid: "what is his latest creatinine" is a SQL question — embeddings
lose exact values and ordering, and a wrong lab value on screen is
unacceptable. "Any history of bleeding disorder?" is a vector question —
no code captures it and the wording varies.

Everything returned carries its source row id, so the Verifier can check
that every downstream claim traces back to something real.
"""

import os
import json
import boto3
import psycopg
from dataclasses import dataclass, field
from typing import Optional
from grus_config import DB



HOST = DB.HOST
PWD = DB.PASSWORD

REGION = "ap-south-1"
EMBED_MODEL = "amazon.titan-embed-text-v2:0"
EMBED_DIM = 1024

# Titan is a general-purpose embedder on clinical prose. Scores run low —
# the trauma patient's best hit scored 0.185. A 0.5 threshold would return
# nothing. Set low and rely on reranking plus keyword search.
MIN_SIMILARITY = 0.05

bedrock = boto3.client("bedrock-runtime", region_name=REGION)


# Labs a clinician looks at first, in the order they look at them.
# Alphabetical ordering buries INR under anion gap and basophils.
LAB_PRIORITY = [
    "INR(PT)", "PT", "PTT",
    "Hemoglobin", "Hematocrit", "Platelet Count",
    "Creatinine", "Urea Nitrogen",
    "Lactate", "pH", "Base Excess",
    "Sodium", "Potassium", "Bicarbonate",
    "White Blood Cells", "Glucose",
    "Troponin T", "Bilirubin, Total", "Albumin",
]
LAB_RANK = {label: i for i, label in enumerate(LAB_PRIORITY)}


@dataclass
class Evidence:
    """One retrieved fact, with where it came from."""
    table: str
    row_id: int
    label: str
    value: Optional[str] = None
    unit: Optional[str] = None
    hours: Optional[float] = None
    text: Optional[str] = None
    section: Optional[str] = None
    similarity: Optional[float] = None
    is_current_admission: bool = True
    is_abnormal: bool = False
    pre_arrival: bool = False        # drawn before this hospital admitted them

    def source(self):
        return {"table": self.table, "id": self.row_id}

    def when(self):
        """
        Human-readable timing. Negative hours are real: bloods drawn at
        a referring hospital before transfer. Saying '-3.9h' is confusing;
        saying 'pre-arrival' is what a clinician means.
        """
        if self.hours is None:
            return "time unknown"
        if self.hours < 0:
            return f"pre-arrival ({abs(self.hours):.1f}h before)"
        return f"{self.hours:.1f}h"


@dataclass
class RetrievalResult:
    structured: list = field(default_factory=list)
    narrative: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def all_sources(self):
        return [e.source() for e in self.structured + self.narrative]


def embed(text):
    r = bedrock.invoke_model(
        modelId=EMBED_MODEL,
        body=json.dumps({"inputText": text[:8000],
                         "dimensions": EMBED_DIM, "normalize": True}),
    )
    return json.loads(r["body"].read())["embedding"]


class Retriever:
    def __init__(self, conn, hadm_id, as_of_hours=None):
        self.conn = conn
        self.hadm_id = hadm_id
        self.as_of = as_of_hours

        row = conn.execute(
            "SELECT subject_id FROM admissions WHERE hadm_id = %s", (hadm_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"no admission {hadm_id}")
        self.subject_id = row[0]

    # -----------------------------------------------------------
    # Structured — numbers and dates
    # -----------------------------------------------------------
    def _cap(self, col="hours_since_admit"):
        """
        The point-in-time boundary.

        Note the lower bound is open: negative hours are bloods drawn at a
        referring hospital before transfer. They existed before arrival, so
        an 'as of hour 1' view must include them.
        """
        if self.as_of is None:
            return ""
        return f" AND ({col} IS NULL OR {col} <= {self.as_of}) "

    def arrival_labs(self, labels=None, limit=None, priority_only=False):
        """
        First value of each lab this admission — what the patient walked in
        with, before the hospital changed anything.

        Two things this handles that a naive query does not:

        1. Negative hours. Bloods drawn at a referring hospital before
           transfer are real and clinically important, but the time cutoff
           must not exclude them and the display must not say '-3.9h'.

        2. Ordering. Alphabetical puts anion gap and basophils above INR.
           Sorted by clinical priority, then by abnormal flag.
        """
        filt = ""
        params = [self.hadm_id]
        if labels:
            filt = " AND label = ANY(%s) "
            params.append(labels)

        rows = self.conn.execute(f"""
            SELECT lab_id, label, valuenum, valueuom, hours_since_admit, flag
            FROM labs
            WHERE hadm_id = %s AND is_first_of_stay AND valuenum IS NOT NULL
              {filt} {self._cap()}
        """, params).fetchall()

        out = []
        for r in rows:
            hours = float(r[4]) if r[4] is not None else None
            e = Evidence("labs", r[0], r[1], str(r[2]), r[3], hours)
            e.is_abnormal = (r[5] or "").lower() == "abnormal"
            e.pre_arrival = hours is not None and hours < 0
            out.append(e)

        # priority first, then abnormals, then alphabetical
        out.sort(key=lambda e: (
            LAB_RANK.get(e.label, 999),
            not e.is_abnormal,
            e.label,
        ))

        if priority_only:
            out = [e for e in out if e.label in LAB_RANK]
        return out[:limit] if limit else out

    def lab_trend(self, label, limit=10):
        rows = self.conn.execute(f"""
            SELECT lab_id, label, valuenum, valueuom, hours_since_admit, flag
            FROM labs
            WHERE hadm_id = %s AND label = %s AND valuenum IS NOT NULL {self._cap()}
            ORDER BY hours_since_admit LIMIT {limit}
        """, (self.hadm_id, label)).fetchall()

        return [Evidence("labs", r[0], r[1], str(r[2]), r[3],
                         float(r[4]) if r[4] is not None else None)
                for r in rows]

    def current_vitals(self):
        """Latest reading of each vital, with its age."""
        rows = self.conn.execute(f"""
            SELECT DISTINCT ON (vital_code)
                   vital_id, vital_code, label, valuenum, valueuom, hours_since_admit
            FROM vitals
            WHERE hadm_id = %s AND valuenum IS NOT NULL AND vital_code IS NOT NULL
              {self._cap()}
            ORDER BY vital_code, hours_since_admit DESC
        """, (self.hadm_id,)).fetchall()

        out = []
        for r in rows:
            e = Evidence("vitals", r[0], r[2], str(r[3]), r[4],
                         float(r[5]) if r[5] is not None else None)
            e.value = str(r[3])
            out.append(e)
        return out

    def medications(self, drug_class=None, active_only=False):
        filt = ""
        params = [self.hadm_id]
        if drug_class:
            filt += " AND drug_class = %s "
            params.append(drug_class)
        if active_only:
            filt += " AND is_active = 'active' "

        rows = self.conn.execute(f"""
            SELECT medication_id, drug_normalized, drug_class, route,
                   start_hours, stop_hours, is_active, status
            FROM medications
            WHERE hadm_id = %s {filt} {self._cap("start_hours")}
            ORDER BY start_hours NULLS LAST
        """, params).fetchall()

        return [Evidence("medications", r[0], r[1], r[6], r[3],
                         float(r[4]) if r[4] is not None else None)
                for r in rows]

    def diagnoses(self):
        rows = self.conn.execute("""
            SELECT diagnosis_id, icd_code, long_title, seq_num
            FROM diagnoses WHERE hadm_id = %s
            ORDER BY seq_num NULLS LAST
        """, (self.hadm_id,)).fetchall()

        return [Evidence("diagnoses", r[0], r[2] or r[1], r[1]) for r in rows]

    def outputs(self):
        rows = self.conn.execute(f"""
            SELECT output_id, label, value, valueuom, hours_since_admit
            FROM outputs
            WHERE hadm_id = %s AND value IS NOT NULL {self._cap()}
            ORDER BY hours_since_admit
        """, (self.hadm_id,)).fetchall()

        return [Evidence("outputs", r[0], r[1], str(r[2]), r[3],
                         float(r[4]) if r[4] is not None else None)
                for r in rows]

    # -----------------------------------------------------------
    # History — previous admissions
    # -----------------------------------------------------------
    def prior_admissions(self):
        rows = self.conn.execute("""
            SELECT hadm_id, admittime, admission_type, hosp_days
            FROM admissions
            WHERE subject_id = %s AND is_current = FALSE
            ORDER BY admittime DESC
        """, (self.subject_id,)).fetchall()
        return rows

    def recurring_diagnoses(self, min_visits=2):
        """
        Conditions appearing across several previous admissions.
        'Fourth presentation with heart failure' is a signal nobody
        would look up manually.
        """
        rows = self.conn.execute("""
            SELECT MIN(d.diagnosis_id), d.long_title,
                   COUNT(DISTINCT d.hadm_id) AS n_visits
            FROM diagnoses d
            JOIN admissions a ON a.hadm_id = d.hadm_id AND a.is_current = FALSE
            WHERE d.subject_id = %s AND d.long_title IS NOT NULL
            GROUP BY d.long_title
            HAVING COUNT(DISTINCT d.hadm_id) >= %s
            ORDER BY 3 DESC
        """, (self.subject_id, min_visits)).fetchall()

        return [Evidence("diagnoses", r[0], r[1], f"{r[2]} visits",
                         is_current_admission=False) for r in rows]

    def prior_medications(self, drug_class=None):
        filt = " AND m.drug_class = %s " if drug_class else ""
        params = [self.subject_id] + ([drug_class] if drug_class else [])

        rows = self.conn.execute(f"""
            SELECT DISTINCT ON (m.drug_normalized)
                   m.medication_id, m.drug_normalized, m.drug_class, a.admittime
            FROM medications m
            JOIN admissions a ON a.hadm_id = m.hadm_id AND a.is_current = FALSE
            WHERE m.subject_id = %s {filt}
            ORDER BY m.drug_normalized, a.admittime DESC
        """, params).fetchall()

        return [Evidence("medications", r[0], r[1], r[2],
                         is_current_admission=False) for r in rows]

    # -----------------------------------------------------------
    # Narrative — vector and keyword
    # -----------------------------------------------------------
    def search_notes(self, question, k=5, include_current=True,
                     include_prior=True, sections=None):
        """
        Semantic search over this patient's notes.

        Metadata filters run BEFORE the vector comparison — searching
        every patient's embeddings and then discarding 300 of them is
        backwards.
        """
        qvec = str(embed(question))

        where = ["subject_id = %s", "embedding IS NOT NULL"]
        params = [qvec, self.subject_id]

        if not (include_current and include_prior):
            if include_current:
                where.append("hadm_id = %s")
                params.append(self.hadm_id)
            elif include_prior:
                where.append("hadm_id != %s")
                params.append(self.hadm_id)

        if sections:
            where.append("section = ANY(%s)")
            params.append(sections)

        params.extend([qvec, k])

        rows = self.conn.execute(f"""
            SELECT chunk_id, section, note_type, text, hadm_id,
                   1 - (embedding <=> %s) AS similarity
            FROM note_chunks
            WHERE {' AND '.join(where)}
            ORDER BY embedding <=> %s
            LIMIT %s
        """, params).fetchall()

        out = []
        for r in rows:
            if r[5] < MIN_SIMILARITY:
                continue
            e = Evidence("note_chunks", r[0], r[1], text=r[3], section=r[1],
                         similarity=round(float(r[5]), 3),
                         is_current_admission=(r[4] == self.hadm_id))
            out.append(e)
        return out

    def notes_by_section(self, *sections, current_only=False, k=5):
        """
        Fetch chunks by SECTION NAME, not by searching the body text.

        'Medications on Admission' is a section heading — the words do not
        appear inside the chunk, so keyword search over text finds nothing.
        This is how a home anticoagulant goes missing.
        """
        where = ["subject_id = %s", "section = ANY(%s)"]
        params = [self.subject_id, list(sections)]
        if current_only:
            where.append("hadm_id = %s")
            params.append(self.hadm_id)
        params.append(k)

        rows = self.conn.execute(f"""
            SELECT chunk_id, section, text, hadm_id
            FROM note_chunks
            WHERE {' AND '.join(where)}
            ORDER BY (hadm_id = {self.hadm_id}) DESC
            LIMIT %s
        """, params).fetchall()

        return [Evidence("note_chunks", r[0], r[1], text=r[2], section=r[1],
                         is_current_admission=(r[3] == self.hadm_id))
                for r in rows]

    def keyword_notes(self, *terms, k=5):
        """
        Exact-term search. Titan maps 'reversal agent' poorly onto
        'vitamin K', so drug names need a literal match as well.
        """
        clauses = " OR ".join(["text ILIKE %s"] * len(terms))
        params = [self.subject_id] + [f"%{t}%" for t in terms] + [k]

        rows = self.conn.execute(f"""
            SELECT chunk_id, section, text, hadm_id
            FROM note_chunks
            WHERE subject_id = %s AND ({clauses})
            LIMIT %s
        """, params).fetchall()

        return [Evidence("note_chunks", r[0], r[1], text=r[2], section=r[1],
                         is_current_admission=(r[3] == self.hadm_id))
                for r in rows]

    def find_in_notes(self, question, keywords=(), k=5):
        """
        Both methods, merged and deduplicated.

        Neither alone is sufficient. Vector search found the cardiac
        patient's heart failure history at 0.417; keyword search is what
        reliably finds 'vitamin K' in a wall of prose.
        """
        seen = {}
        for e in self.keyword_notes(*keywords, k=k) if keywords else []:
            seen[e.row_id] = e
        for e in self.search_notes(question, k=k):
            if e.row_id not in seen:
                seen[e.row_id] = e
        return list(seen.values())

    # -----------------------------------------------------------
    # The whole picture
    # -----------------------------------------------------------
    def gather(self):
        r = RetrievalResult()

        r.structured += self.arrival_labs()
        r.structured += self.current_vitals()
        r.structured += self.medications()
        r.structured += self.diagnoses()
        r.structured += self.outputs()
        r.structured += self.recurring_diagnoses()
        r.structured += self.prior_medications()

        r.narrative += self.find_in_notes(
            "medications the patient takes at home and why",
            keywords=("warfarin", "coumadin", "apixaban", "aspirin"),
            k=3,
        )
        r.narrative += self.find_in_notes(
            "allergies and adverse drug reactions",
            keywords=("allerg",), k=2,
        )

        n_prior = len(self.prior_admissions())
        r.stats = {
            "structured_rows": len(r.structured),
            "note_chunks": len(r.narrative),
            "prior_admissions": n_prior,
            "as_of_hours": self.as_of,
        }
        return r


if __name__ == "__main__":
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus", user="grusadmin",
        password=PWD, sslmode="require", keepalives=1, keepalives_idle=30,
    )
    conn.execute("SET search_path TO grus, public")

    for label, hadm, as_of in [
        ("TRAUMA — hour 1", 28173870, 1),
        ("TRAUMA — full",   28173870, None),
        ("CARDIAC — full",  27180495, None),
    ]:
        print("\n" + "=" * 64)
        print(label)
        print("=" * 64)

        rt = Retriever(conn, hadm, as_of)
        res = rt.gather()
        print(f"  {res.stats}")

        print("\n  priority labs (arrival):")
        for e in rt.arrival_labs(priority_only=True, limit=8):
            flag = " *" if e.is_abnormal else "  "
            print(f"    {e.label:22} {e.value:>8} {e.unit or '':6}{flag} {e.when():28} [{e.table}#{e.row_id}]")

        vit = rt.current_vitals()
        print(f"\n  current vitals ({len(vit)}):")
        for e in vit[:5]:
            print(f"    {e.label:38} {e.value:>8} @{e.when()}")

        rec = rt.recurring_diagnoses()
        if rec:
            print(f"\n  recurring across prior visits:")
            for e in rec[:5]:
                print(f"    {e.label[:52]:52} {e.value}")

        print("\n  note search — 'why is the patient anticoagulated':")
        for e in rt.find_in_notes("why is the patient anticoagulated",
                                  keywords=("warfarin", "coumadin"), k=3):
            tag = "current" if e.is_current_admission else "prior"
            sim = f"{e.similarity:.3f}" if e.similarity else "kw"
            print(f"    [{sim}] {e.section} ({tag})")
            print(f"          {e.text.strip()[:110]}...")

    conn.close()