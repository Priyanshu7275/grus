"""
GRUS — Prior Admissions ETL

Loads the patient's HISTORY: up to 5 previous admissions per cohort patient.

This is tier 1 of the three-tier model:
  1. prior admissions   <- this file      (history, static)
  2. current admission  <- grus_etl_tier1 (this visit, has hours_since_admit)
  3. latest reading     <- the newest row before the as_of cutoff

History gets diagnoses, medications, procedures, key labs, and discharge
summaries. It does NOT get vitals or outputs — nobody reviews hour-by-hour
vitals from three years ago, and they are the bulk of the data.

Run AFTER grus_etl_tier1.py. Appends; does not truncate the current admission.
"""

import os
import duckdb
import psycopg
from dotenv import load_dotenv



BASE = 'C:/Users/Hp/OneDrive/Desktop/MIMICS ETL/mimic-iv-2.1'
NOTES = 'C:/Users/Hp/OneDrive/Desktop/MIMICS ETL/note/discharge.csv.gz'
COHORT_CSV = 'C:/Users/Hp/OneDrive/Desktop/Grus/cohort_300.csv'

from grus_config import DB
HOST = DB.HOST
PWD = DB.PASSWORD

MAX_PRIOR = 5

# Labs worth keeping from history. The full set would be millions of rows
# and no clinician reviews a potassium from three years ago.
HISTORY_LABS = [
    'INR(PT)', 'Hemoglobin', 'Hematocrit', 'Creatinine', 'Platelet Count',
    'Sodium', 'Potassium', 'White Blood Cells', 'Glucose', 'Urea Nitrogen',
    'Bicarbonate', 'Albumin', 'Bilirubin, Total', 'Troponin T', 'Lactate',
]

from grus_etl_tier1 import DRUG_NORM_SQL, DRUG_CLASS_SQL


def extract():
    con = duckdb.connect()
    con.execute("SET memory_limit='6GB'")

    con.execute(f"""
        CREATE OR REPLACE TABLE current AS
        SELECT DISTINCT subject_id, hadm_id FROM read_csv_auto('{COHORT_CSV}')
    """)

    # Most recent MAX_PRIOR admissions per patient, excluding the current one
    con.execute(f"""
        CREATE OR REPLACE TABLE prior AS
        SELECT a.subject_id, a.hadm_id, a.admittime
        FROM read_csv_auto('{BASE}/hosp/admissions.csv') a
        JOIN (SELECT DISTINCT subject_id FROM current) c ON c.subject_id = a.subject_id
        WHERE a.hadm_id NOT IN (SELECT hadm_id FROM current)
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY a.subject_id ORDER BY a.admittime DESC
        ) <= {MAX_PRIOR}
    """)

    n = con.execute("SELECT COUNT(*) FROM prior").fetchone()[0]
    pts = con.execute("SELECT COUNT(DISTINCT subject_id) FROM prior").fetchone()[0]
    print(f"prior admissions: {n} across {pts} patients")

    out = {}

    # --- admissions ---
    out['admissions'] = con.execute(f"""
        WITH first_unit AS (
            SELECT hadm_id, careunit AS arrival_unit,
                   ROW_NUMBER() OVER (PARTITION BY hadm_id ORDER BY intime) AS rn
            FROM read_csv_auto('{BASE}/hosp/transfers.csv')
        ),
        icu_tot AS (
            SELECT hadm_id, ROUND(SUM(los),2) AS icu_days
            FROM read_csv_auto('{BASE}/icu/icustays.csv') GROUP BY 1
        )
        SELECT a.hadm_id, a.subject_id, a.admittime, a.dischtime,
               a.admission_type, a.admission_location, a.discharge_location,
               a.insurance, a.hospital_expire_flag,
               ROUND(DATE_DIFF('hour', a.admittime, a.dischtime)/24.0, 2) AS hosp_days,
               it.icu_days, fu.arrival_unit,
               NULL AS cohort,
               FALSE AS is_current
        FROM read_csv_auto('{BASE}/hosp/admissions.csv') a
        JOIN prior p ON p.hadm_id = a.hadm_id
        LEFT JOIN first_unit fu ON fu.hadm_id = a.hadm_id AND fu.rn = 1
        LEFT JOIN icu_tot it ON it.hadm_id = a.hadm_id
    """).df()

    # --- diagnoses: the core of what history is for ---
    out['diagnoses'] = con.execute(f"""
        SELECT d.hadm_id, d.subject_id, d.seq_num, d.icd_code, d.icd_version,
               dd.long_title,
               (d.icd_code LIKE 'E8%' OR d.icd_code LIKE 'V4%'
                OR d.icd_code LIKE 'W0%' OR d.icd_code LIKE 'V0%') AS is_external_cause
        FROM read_csv_auto('{BASE}/hosp/diagnoses_icd.csv') d
        JOIN prior p ON p.hadm_id = d.hadm_id
        LEFT JOIN read_csv_auto('{BASE}/hosp/d_icd_diagnoses.csv') dd
          ON dd.icd_code = d.icd_code AND dd.icd_version = d.icd_version
    """).df()

    # --- procedures ---
    out['procedures'] = con.execute(f"""
        SELECT pr.hadm_id, pr.subject_id, pr.seq_num, pr.icd_code, pr.icd_version,
               pd.long_title, pr.chartdate,
               NULL AS hours_since_admit
        FROM read_csv_auto('{BASE}/hosp/procedures_icd.csv') pr
        JOIN prior p ON p.hadm_id = pr.hadm_id
        LEFT JOIN read_csv_auto('{BASE}/hosp/d_icd_procedures.csv') pd
          ON pd.icd_code = pr.icd_code AND pd.icd_version = pr.icd_version
    """).df()

    # --- medications: same transforms as the current admission ---
    out['medications'] = con.execute(f"""
        WITH rx AS (
            SELECT p.hadm_id, p.subject_id, p.drug,
                   {DRUG_NORM_SQL} AS drug_normalized,
                   {DRUG_CLASS_SQL} AS drug_class,
                   p.route, p.dose_val_rx, p.dose_unit_rx,
                   p.starttime, p.stoptime
            FROM read_csv_auto('{BASE}/hosp/prescriptions.csv') p
            JOIN prior pr ON pr.hadm_id = p.hadm_id
            WHERE LOWER(p.drug) NOT IN ('bag','vial','sw','lr','ns','syringe','soln')
              AND LOWER(p.drug) NOT LIKE '%sodium chloride%'
              AND LOWER(p.drug) NOT LIKE '%dextrose%'
              AND LOWER(p.drug) NOT LIKE '%iso-osmotic%'
              AND LOWER(p.drug) NOT LIKE '%1/2 ns%'
              AND LOWER(p.drug) NOT LIKE '%mini bag%'
              AND LOWER(p.drug) NOT LIKE '%flush%'
              AND LOWER(p.drug) NOT LIKE '%lactated ringer%'
              AND LOWER(p.drug) NOT LIKE '%sterile water%'
              AND LOWER(p.drug) NOT LIKE '%d5%'
        ),
        ph AS (
            SELECT h.hadm_id, h.medication, h.status
            FROM read_csv_auto('{BASE}/hosp/pharmacy.csv') h
            JOIN prior pr ON pr.hadm_id = h.hadm_id
        ),
        collapsed AS (
            SELECT hadm_id, subject_id,
                   ANY_VALUE(drug) AS drug_raw,
                   drug_normalized, ANY_VALUE(drug_class) AS drug_class,
                   ANY_VALUE(route) AS route,
                   ANY_VALUE(dose_val_rx) AS dose_val_rx,
                   ANY_VALUE(dose_unit_rx) AS dose_unit_rx,
                   MIN(starttime) AS starttime, MAX(stoptime) AS stoptime,
                   COUNT(*) AS n_orders
            FROM rx GROUP BY hadm_id, subject_id, drug_normalized
        )
        SELECT c.hadm_id, c.subject_id, c.drug_raw, c.drug_normalized, c.drug_class,
               c.route, c.dose_val_rx, c.dose_unit_rx, c.starttime, c.stoptime,
               NULL AS start_hours, NULL AS stop_hours,
               COALESCE(ANY_VALUE(ph.status), 'unknown') AS status,
               CASE
                 WHEN NOT (c.stoptime IS NULL OR c.stoptime >= c.starttime) THEN 'unknown'
                 WHEN ANY_VALUE(ph.status) IS NULL THEN 'unknown'
                 WHEN LOWER(ANY_VALUE(ph.status)) LIKE '%discontinued%' THEN 'stopped'
                 WHEN LOWER(ANY_VALUE(ph.status)) LIKE '%expired%' THEN 'stopped'
                 ELSE 'active'
               END AS is_active,
               'prescriptions' AS source_table,
               (c.stoptime IS NULL OR c.stoptime >= c.starttime) AS time_valid,
               c.n_orders
        FROM collapsed c
        LEFT JOIN ph ON ph.hadm_id = c.hadm_id
                    AND LOWER(TRIM(ph.medication)) = LOWER(TRIM(c.drug_raw))
        GROUP BY c.hadm_id, c.subject_id, c.drug_raw, c.drug_normalized, c.drug_class,
                 c.route, c.dose_val_rx, c.dose_unit_rx, c.starttime, c.stoptime,
                 c.n_orders
    """).df()

    # --- labs: key panel only ---
    lab_list = ",".join(f"'{x}'" for x in HISTORY_LABS)
    out['labs'] = con.execute(f"""
        SELECT l.subject_id, l.hadm_id, l.itemid, dl.label, dl.category,
               l.charttime, NULL AS hours_since_admit,
               l.valuenum, l.valueuom,
               l.ref_range_lower, l.ref_range_upper, l.flag,
               (ROW_NUMBER() OVER (PARTITION BY l.hadm_id, dl.label
                                   ORDER BY l.charttime) = 1) AS is_first_of_stay
        FROM read_csv_auto('{BASE}/hosp/labevents.csv') l
        JOIN prior p ON p.hadm_id = l.hadm_id
        JOIN read_csv_auto('{BASE}/hosp/d_labitems.csv') dl ON dl.itemid = l.itemid
        WHERE dl.label IN ({lab_list})
    """).df()

    # --- notes ---
    out['notes'] = con.execute(f"""
        SELECT n.note_id, n.subject_id, n.hadm_id, 'discharge' AS note_type,
               n.charttime, NULL AS hours_since_admit, n.text
        FROM read_csv_auto('{NOTES}') n
        JOIN prior p ON p.hadm_id = n.hadm_id
    """).df()

    con.close()
    for k, v in out.items():
        print(f"  {k:15} {len(v):>8} rows")
    return out


def chunk_notes(df):
    """Reuse the section splitter from the notes pipeline."""
    from grus_notes_pipeline import split_sections, split_long, clean

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
    print(f"  prior note chunks: {len(rows)}")
    return rows


LOAD_ORDER = [
    ('admissions',  ['hadm_id', 'subject_id', 'admittime', 'dischtime',
                     'admission_type', 'admission_location', 'discharge_location',
                     'insurance', 'hospital_expire_flag', 'hosp_days', 'icu_days',
                     'arrival_unit', 'cohort', 'is_current']),
    ('diagnoses',   ['hadm_id', 'subject_id', 'seq_num', 'icd_code', 'icd_version',
                     'long_title', 'is_external_cause']),
    ('procedures',  ['hadm_id', 'subject_id', 'seq_num', 'icd_code', 'icd_version',
                     'long_title', 'chartdate', 'hours_since_admit']),
    ('medications', ['hadm_id', 'subject_id', 'drug_raw', 'drug_normalized',
                     'drug_class', 'route', 'dose_val_rx', 'dose_unit_rx',
                     'starttime', 'stoptime', 'start_hours', 'stop_hours',
                     'status', 'is_active', 'source_table', 'time_valid', 'n_orders']),
    ('labs',        ['subject_id', 'hadm_id', 'itemid', 'label', 'category',
                     'charttime', 'hours_since_admit', 'valuenum', 'valueuom',
                     'ref_range_lower', 'ref_range_upper', 'flag', 'is_first_of_stay']),
    ('notes',       ['note_id', 'subject_id', 'hadm_id', 'note_type',
                     'charttime', 'hours_since_admit', 'text']),
]


def load(data, chunks):
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus",
        user="grusadmin", password=PWD, sslmode="require",
        keepalives=1, keepalives_idle=30, connect_timeout=30
    )
    conn.execute("SET search_path TO grus, public")
    conn.execute("ALTER TABLE admissions ADD COLUMN IF NOT EXISTS is_current BOOLEAN DEFAULT TRUE")
    conn.commit()

    # Remove any previously loaded history, leave the current admission alone
    conn.execute("""
        DELETE FROM note_chunks WHERE hadm_id IN
            (SELECT hadm_id FROM admissions WHERE is_current = FALSE)
    """)
    conn.execute("""
        DELETE FROM notes WHERE hadm_id IN
            (SELECT hadm_id FROM admissions WHERE is_current = FALSE)
    """)
    for t in ['labs', 'medications', 'procedures', 'diagnoses']:
        conn.execute(f"""
            DELETE FROM {t} WHERE hadm_id IN
                (SELECT hadm_id FROM admissions WHERE is_current = FALSE)
        """)
    conn.execute("DELETE FROM admissions WHERE is_current = FALSE")
    conn.commit()
    print("cleared previous history load")

    for table, cols in LOAD_ORDER:
        df = data[table][cols].copy()
        df = df.astype(object).where(df.notna(), None)
        with conn.cursor() as cur:
            with cur.copy(f"COPY {table} ({','.join(cols)}) FROM STDIN") as cp:
                for row in df.itertuples(index=False, name=None):
                    cp.write_row(row)
        conn.commit()
        print(f"  loaded {table:15} {len(df):>8} rows")

    chunk_cols = ["note_id", "hadm_id", "subject_id", "note_type",
                  "section", "chunk_index", "charttime", "text"]
    with conn.cursor() as cur:
        with cur.copy(f"COPY note_chunks ({','.join(chunk_cols)}) FROM STDIN") as cp:
            for c in chunks:
                cp.write_row(tuple(c[k] for k in chunk_cols))
    conn.commit()
    print(f"  loaded note_chunks   {len(chunks):>8} rows")

    # --- verification ---
    print("\nverification")
    print("  admissions by tier:",
          conn.execute("SELECT is_current, COUNT(*) FROM admissions GROUP BY 1 ORDER BY 2 DESC").fetchall())

    print("\n  patients with the most history:")
    for row in conn.execute("""
        SELECT subject_id, COUNT(*) AS prior_visits
        FROM admissions WHERE is_current = FALSE
        GROUP BY 1 ORDER BY 2 DESC LIMIT 5
    """).fetchall():
        print(f"    {row[0]}  {row[1]} prior admissions")

    print("\n  example — recurring diagnoses across visits:")
    for row in conn.execute("""
        SELECT d.subject_id, d.long_title, COUNT(DISTINCT d.hadm_id) AS n_visits
        FROM diagnoses d
        JOIN admissions a ON a.hadm_id = d.hadm_id AND a.is_current = FALSE
        WHERE d.long_title IS NOT NULL
        GROUP BY 1,2 HAVING COUNT(DISTINCT d.hadm_id) >= 3
        ORDER BY 3 DESC LIMIT 5
    """).fetchall():
        print(f"    {row[0]}  {row[1][:50]:50} in {row[2]} visits")

    conn.close()


if __name__ == "__main__":
    print("extracting prior admissions...")
    data = extract()
    print("\nchunking prior notes...")
    chunks = chunk_notes(data['notes'])
    print("\nloading...")
    load(data, chunks)
    print("\ndone")