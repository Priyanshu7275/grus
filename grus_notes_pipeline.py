"""
GRUS — Notes Pipeline
MIMIC-IV-Note discharge summaries -> section chunks -> Aurora

Two stages:
  1. extract + chunk   (this file, run first)
  2. embed + index     (grus_embed.py, run second)

Chunking is by clinical section, never fixed token count.
Fixed-size chunking splits a medication list in half and loses drugs.
"""

import os
import re
import duckdb
import psycopg
from dotenv import load_dotenv



NOTES = 'C:/Users/Hp/OneDrive/Desktop/MIMICS ETL/note/discharge.csv.gz'
COHORT_CSV = 'C:/Users/Hp/OneDrive/Desktop/Grus/cohort_300.csv'

from grus_config import DB
HOST = DB.HOST
PWD = DB.PASSWORD

DEMO_HADM = 28173870


# ---------------------------------------------------------------
# Section headers, taken from the actual note text.
# Order matters — longer/more specific patterns first so that
# "Past Medical History" doesn't get caught by "History".
# ---------------------------------------------------------------
SECTIONS = [
    ("Discharge Medications",        r"Discharge Medications?:"),
    ("Medications on Admission",     r"Medications? on Admission:"),
    ("Major Surgical Procedure",     r"Major Surgical or Invasive Procedure:"),
    ("History of Present Illness",   r"History of Present Illness:"),
    ("Past Medical History",         r"Past Medical History:"),
    ("Physical Exam",                r"Physical Exam(?:ination)?:"),
    ("Pertinent Results",            r"Pertinent Results:"),
    ("Brief Hospital Course",        r"Brief Hospital Course:"),
    ("Discharge Diagnosis",          r"Discharge Diagnos[ei]s:"),
    ("Discharge Condition",          r"Discharge Condition:"),
    ("Discharge Instructions",       r"Discharge Instructions:"),
    ("Discharge Disposition",        r"Discharge Disposition:"),
    ("Followup Instructions",        r"Followup Instructions?:"),
    ("Social History",               r"Social History:"),
    ("Family History",               r"Family History:"),
    ("Chief Complaint",              r"Chief Complaint:"),
    ("Allergies",                    r"Allergies:"),
    ("Service",                      r"Service:"),
]

MAX_CHUNK_CHARS = 4000   # split very long sections, but only at paragraph breaks


def split_sections(text):
    """
    Find every section header in the note, then slice the text between them.
    Returns [(section_name, body), ...] in document order.
    """
    hits = []
    for name, pattern in SECTIONS:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            hits.append((m.start(), m.end(), name))

    if not hits:
        return [("Full Note", text.strip())]

    hits.sort(key=lambda h: h[0])

    # Drop overlapping matches (keeps the first, which is the more specific
    # pattern because SECTIONS is ordered that way)
    kept = []
    last_end = -1
    for start, end, name in hits:
        if start >= last_end:
            kept.append((start, end, name))
            last_end = end

    out = []
    # Anything before the first header is the note preamble
    if kept[0][0] > 0:
        pre = text[:kept[0][0]].strip()
        if pre:
            out.append(("Header", pre))

    for i, (start, end, name) in enumerate(kept):
        body_start = end
        body_end = kept[i + 1][0] if i + 1 < len(kept) else len(text)
        body = text[body_start:body_end].strip()
        if body:
            out.append((name, body))

    return out


def split_long(section, body):
    """
    Split an oversized section at paragraph boundaries.
    Never mid-sentence, never mid-list.
    """
    if len(body) <= MAX_CHUNK_CHARS:
        return [body]

    paras = re.split(r"\n\s*\n", body)
    chunks, current = [], ""
    for p in paras:
        if len(current) + len(p) + 2 > MAX_CHUNK_CHARS and current:
            chunks.append(current.strip())
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current.strip():
        chunks.append(current.strip())
    return chunks


def clean(text):
    """MIMIC de-identifies with ___ placeholders. Collapse runs of whitespace."""
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------
# Extract
# ---------------------------------------------------------------
def extract_notes():
    con = duckdb.connect()
    con.execute("SET memory_limit='6GB'")

    con.execute(f"""
        CREATE OR REPLACE TABLE cohort AS
        SELECT DISTINCT hadm_id, subject_id FROM read_csv_auto('{COHORT_CSV}')
    """)

    df = con.execute(f"""
        SELECT n.note_id, n.subject_id, n.hadm_id, n.charttime, n.text,
               ROUND(DATE_DIFF('minute', a.admittime, n.charttime)/60.0, 2) AS hours_since_admit
        FROM read_csv_auto('{NOTES}') n
        JOIN cohort c ON c.hadm_id = n.hadm_id
        LEFT JOIN read_csv_auto(
            'C:/Users/Hp/OneDrive/Desktop/MIMICS ETL/mimic-iv-2.1/hosp/admissions.csv'
        ) a ON a.hadm_id = n.hadm_id
    """).df()

    con.close()
    print(f"notes extracted: {len(df)} for {df.hadm_id.nunique()} admissions")
    return df


# ---------------------------------------------------------------
# Chunk
# ---------------------------------------------------------------
def chunk_notes(df):
    rows = []
    for r in df.itertuples(index=False):
        text = clean(r.text)
        idx = 0
        for section, body in split_sections(text):
            for piece in split_long(section, body):
                rows.append({
                    "note_id": r.note_id,
                    "hadm_id": r.hadm_id,
                    "subject_id": r.subject_id,
                    "note_type": "discharge",
                    "section": section,
                    "chunk_index": idx,
                    "charttime": r.charttime,
                    "text": piece,
                })
                idx += 1
    print(f"chunks created: {len(rows)}")

    from collections import Counter
    top = Counter(x["section"] for x in rows).most_common(10)
    print("  most common sections:")
    for s, n in top:
        print(f"    {s:32} {n:>5}")
    return rows


# ---------------------------------------------------------------
# Load
# ---------------------------------------------------------------
def load(df, chunks):
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus",
        user="grusadmin", password=PWD, sslmode="require",
        keepalives=1, keepalives_idle=30, connect_timeout=30
    )
    conn.execute("SET search_path TO grus, public")

    conn.execute("TRUNCATE TABLE note_chunks CASCADE")
    conn.execute("TRUNCATE TABLE notes CASCADE")
    conn.commit()

    note_cols = ["note_id", "subject_id", "hadm_id", "note_type",
                 "charttime", "hours_since_admit", "text"]
    nd = df.copy()
    nd["note_type"] = "discharge"
    nd = nd[note_cols].astype(object).where(nd[note_cols].notna(), None)

    with conn.cursor() as cur:
        with cur.copy(f"COPY notes ({','.join(note_cols)}) FROM STDIN") as cp:
            for row in nd.itertuples(index=False, name=None):
                cp.write_row(row)
    conn.commit()
    print(f"  loaded notes         {len(nd):>6} rows")

    chunk_cols = ["note_id", "hadm_id", "subject_id", "note_type",
                  "section", "chunk_index", "charttime", "text"]
    with conn.cursor() as cur:
        with cur.copy(f"COPY note_chunks ({','.join(chunk_cols)}) FROM STDIN") as cp:
            for c in chunks:
                cp.write_row(tuple(c[k] for k in chunk_cols))
    conn.commit()
    print(f"  loaded note_chunks   {len(chunks):>6} rows")

    # --- verification: does the demo patient's note contain the reversal? ---
    print(f"\nverification — {DEMO_HADM}")
    print("  sections found:")
    for s, n in conn.execute(f"""
        SELECT section, COUNT(*) FROM note_chunks
        WHERE hadm_id = {DEMO_HADM} GROUP BY 1 ORDER BY 2 DESC
    """).fetchall():
        print(f"    {s:32} {n}")

    hits = conn.execute(f"""
        SELECT section, chunk_id FROM note_chunks
        WHERE hadm_id = {DEMO_HADM}
          AND (text ILIKE '%vitamin k%' OR text ILIKE '%FFP%'
               OR text ILIKE '%coumadin%')
    """).fetchall()
    print(f"  reversal/anticoagulant mentions: {hits}")

    conn.close()


if __name__ == "__main__":
    print("extracting...")
    df = extract_notes()
    print("\nchunking...")
    chunks = chunk_notes(df)
    print("\nloading...")
    load(df, chunks)
    print("\ndone")