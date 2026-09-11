"""
GRUS — Tier 1 ETL
MIMIC-IV CSVs -> transforms -> Aurora PostgreSQL

Loads the 301-patient cohort for the application tier.
Every transform here implements a Phase 1 finding; see comments.

Run from the notebook or as a script. Idempotent: truncates before load.
"""

import os
import duckdb
import psycopg
from dotenv import load_dotenv

load_dotenv()

BASE = 'C:/Users/Hp/OneDrive/Desktop/MIMICS ETL/mimic-iv-2.1'
COHORT_CSV = 'C:/Users/Hp/OneDrive/Desktop/Grus/cohort_300.csv'

from grus_config import DB
HOST = DB.HOST
PWD = DB.PASSWORD

DEMO_HADM = 28173870


# ---------------------------------------------------------------
# Drug normalization — finding #4
# MIMIC drug names are messy: '*NF* Warfarin', 'inv-apixaban',
# 'Warfarin (Coumadin) Brand Name'. Rules need a clean name and a class.
# ---------------------------------------------------------------
DRUG_CLASSES = {
    'anticoagulant': ['warfarin', 'coumadin', 'apixaban', 'eliquis', 'rivaroxaban',
                      'xarelto', 'dabigatran', 'pradaxa', 'heparin', 'enoxaparin',
                      'lovenox', 'fondaparinux', 'argatroban', 'bivalirudin'],
    'antiplatelet': ['aspirin', 'clopidogrel', 'plavix', 'ticagrelor', 'prasugrel'],
    'nsaid': ['ibuprofen', 'naproxen', 'ketorolac', 'diclofenac', 'indomethacin'],
    'opioid': ['morphine', 'hydromorphone', 'dilaudid', 'fentanyl', 'oxycodone',
               'hydrocodone', 'tramadol', 'codeine'],
    'reversal_agent': ['phytonadione', 'vitamin k', 'protamine', 'idarucizumab',
                       'praxbind', 'andexanet', 'kcentra', 'prothrombin complex'],
    'beta_blocker': ['metoprolol', 'atenolol', 'labetalol', 'esmolol', 'carvedilol'],
    'antibiotic': ['vancomycin', 'ceftriaxone', 'piperacillin', 'meropenem',
                   'ciprofloxacin', 'levofloxacin', 'azithromycin', 'cefepime'],
    'vasopressor': ['norepinephrine', 'epinephrine', 'phenylephrine', 'vasopressin',
                    'dopamine', 'dobutamine'],
    'insulin': ['insulin'],
    'diuretic': ['furosemide', 'lasix', 'bumetanide', 'spironolactone'],
}


def build_drug_sql():
    """Generate SQL CASE expressions for normalization and classification."""
    norm_parts, class_parts = [], []
    for cls, drugs in DRUG_CLASSES.items():
        for d in drugs:
            norm_parts.append(
                f"WHEN LOWER(drug) LIKE '%{d}%' THEN '{d}'"
            )
            class_parts.append(
                f"WHEN LOWER(drug) LIKE '%{d}%' THEN '{cls}'"
            )
    norm = "CASE " + " ".join(norm_parts) + \
           " ELSE LOWER(TRIM(REGEXP_REPLACE(drug, '\\*[A-Z]+\\*|\\(.*\\)|^inv[- ]', '', 'g'))) END"
    klass = "CASE " + " ".join(class_parts) + " ELSE NULL END"
    return norm, klass


DRUG_NORM_SQL, DRUG_CLASS_SQL = build_drug_sql()


# ---------------------------------------------------------------
# Vital code mapping — same map as tier 2, so training and serving agree
# ---------------------------------------------------------------
VITAL_MAP = {
    220045: 'hr',
    220050: 'sbp', 220179: 'sbp',
    220051: 'dbp', 220180: 'dbp',
    220052: 'map', 220181: 'map', 225312: 'map',
    220210: 'rr', 224690: 'rr',
    220277: 'spo2',
    223761: 'temp_f', 223762: 'temp_c',
    220739: 'gcs_eye', 223900: 'gcs_verbal', 223901: 'gcs_motor',
    224639: 'weight_kg', 226512: 'weight_kg',
}

VITAL_CASE = "CASE " + " ".join(
    f"WHEN ce.itemid = {k} THEN '{v}'" for k, v in VITAL_MAP.items()
) + " ELSE NULL END"

VITAL_IDS = ",".join(str(k) for k in VITAL_MAP)


# ---------------------------------------------------------------
# Extract with DuckDB
# ---------------------------------------------------------------
def extract():
    con = duckdb.connect()
    con.execute("SET memory_limit='6GB'")

    con.execute(f"""
        CREATE OR REPLACE TABLE cohort AS
        SELECT DISTINCT subject_id, hadm_id, stay_id
        FROM read_csv_auto('{COHORT_CSV}')
    """)
    n = con.execute("SELECT COUNT(*) FROM cohort").fetchone()[0]
    print(f"cohort: {n} stays")

    out = {}

    # --- patients ---
    out['patients'] = con.execute(f"""
        SELECT DISTINCT p.subject_id, p.gender, p.anchor_age, p.dod
        FROM read_csv_auto('{BASE}/hosp/patients.csv') p
        WHERE p.subject_id IN (SELECT subject_id FROM cohort)
    """).df()

    # --- admissions: both durations (#6), arrival unit from transfers (#7) ---
    out['admissions'] = con.execute(f"""
        WITH first_unit AS (
            SELECT hadm_id, careunit AS arrival_unit,
                   ROW_NUMBER() OVER (PARTITION BY hadm_id ORDER BY intime) AS rn
            FROM read_csv_auto('{BASE}/hosp/transfers.csv')
        ),
        icu_tot AS (
            SELECT hadm_id, ROUND(SUM(los),2) AS icu_days
            FROM read_csv_auto('{BASE}/icu/icustays.csv') GROUP BY 1
        ),
        coh AS (SELECT DISTINCT hadm_id FROM cohort)
        SELECT a.hadm_id, a.subject_id, a.admittime, a.dischtime,
               a.admission_type, a.admission_location, a.discharge_location,
               a.insurance, a.hospital_expire_flag,
               ROUND(DATE_DIFF('hour', a.admittime, a.dischtime)/24.0, 2) AS hosp_days,
               it.icu_days,
               fu.arrival_unit,
               c.cohort
        FROM read_csv_auto('{BASE}/hosp/admissions.csv') a
        JOIN coh ON coh.hadm_id = a.hadm_id
        LEFT JOIN first_unit fu ON fu.hadm_id = a.hadm_id AND fu.rn = 1
        LEFT JOIN icu_tot it ON it.hadm_id = a.hadm_id
        LEFT JOIN read_csv_auto('{COHORT_CSV}') c ON c.hadm_id = a.hadm_id
    """).df()

    # --- icu_stays: third ID level (#2) ---
    out['icu_stays'] = con.execute(f"""
        SELECT i.stay_id, i.hadm_id, i.subject_id,
               i.first_careunit, i.last_careunit, i.intime, i.outtime,
               ROUND(i.los,2) AS los_days,
               ROW_NUMBER() OVER (PARTITION BY i.hadm_id ORDER BY i.intime) AS stay_rank
        FROM read_csv_auto('{BASE}/icu/icustays.csv') i
        WHERE i.hadm_id IN (SELECT DISTINCT hadm_id FROM cohort)
    """).df()

    # --- transfers: hours_since_admit (#3), nullable careunit (#8) ---
    out['transfers'] = con.execute(f"""
        SELECT t.hadm_id, t.subject_id, t.careunit, t.intime, t.outtime,
               ROUND(DATE_DIFF('minute', a.admittime, t.intime)/60.0, 2) AS hours_since_admit,
               ROW_NUMBER() OVER (PARTITION BY t.hadm_id ORDER BY t.intime) AS seq_num
        FROM read_csv_auto('{BASE}/hosp/transfers.csv') t
        JOIN read_csv_auto('{BASE}/hosp/admissions.csv') a ON a.hadm_id = t.hadm_id
        WHERE t.hadm_id IN (SELECT DISTINCT hadm_id FROM cohort)
    """).df()

    # --- diagnoses: titles resolved at load, external cause flagged ---
    out['diagnoses'] = con.execute(f"""
        SELECT d.hadm_id, d.subject_id, d.seq_num, d.icd_code, d.icd_version,
               dd.long_title,
               (d.icd_code LIKE 'E8%' OR d.icd_code LIKE 'V4%'
                OR d.icd_code LIKE 'W0%' OR d.icd_code LIKE 'V0%') AS is_external_cause
        FROM read_csv_auto('{BASE}/hosp/diagnoses_icd.csv') d
        LEFT JOIN read_csv_auto('{BASE}/hosp/d_icd_diagnoses.csv') dd
          ON dd.icd_code = d.icd_code AND dd.icd_version = d.icd_version
        WHERE d.hadm_id IN (SELECT DISTINCT hadm_id FROM cohort)
    """).df()

    # --- procedures ---
    out['procedures'] = con.execute(f"""
        SELECT p.hadm_id, p.subject_id, p.seq_num, p.icd_code, p.icd_version,
               pd.long_title, p.chartdate,
               ROUND(DATE_DIFF('hour', a.admittime, CAST(p.chartdate AS TIMESTAMP)), 2) AS hours_since_admit
        FROM read_csv_auto('{BASE}/hosp/procedures_icd.csv') p
        LEFT JOIN read_csv_auto('{BASE}/hosp/d_icd_procedures.csv') pd
          ON pd.icd_code = p.icd_code AND pd.icd_version = p.icd_version
        JOIN read_csv_auto('{BASE}/hosp/admissions.csv') a ON a.hadm_id = p.hadm_id
        WHERE p.hadm_id IN (SELECT DISTINCT hadm_id FROM cohort)
    """).df()

    # --- medications ---
    # #1 collapse repeated dose rows, #4 normalize, #9 validate times,
    # #10 status from pharmacy, #5 record source table
    out['medications'] = con.execute(f"""
        WITH rx AS (
            SELECT p.hadm_id, p.subject_id, p.drug,
                   {DRUG_NORM_SQL} AS drug_normalized,
                   {DRUG_CLASS_SQL} AS drug_class,
                   p.route, p.dose_val_rx, p.dose_unit_rx,
                   p.starttime, p.stoptime
            FROM read_csv_auto('{BASE}/hosp/prescriptions.csv') p
            WHERE p.hadm_id IN (SELECT DISTINCT hadm_id FROM cohort)
        ),
        ph AS (
            SELECT hadm_id, medication, starttime, status
            FROM read_csv_auto('{BASE}/hosp/pharmacy.csv')
            WHERE hadm_id IN (SELECT DISTINCT hadm_id FROM cohort)
        ),
        collapsed AS (
            SELECT hadm_id, subject_id,
                   ANY_VALUE(drug) AS drug_raw,
                   drug_normalized, ANY_VALUE(drug_class) AS drug_class,
                   ANY_VALUE(route) AS route,
                   ANY_VALUE(dose_val_rx) AS dose_val_rx,
                   ANY_VALUE(dose_unit_rx) AS dose_unit_rx,
                   MIN(starttime) AS starttime,
                   MAX(stoptime) AS stoptime,
                   COUNT(*) AS n_orders
            FROM rx
            GROUP BY hadm_id, subject_id, drug_normalized
        )
        SELECT c.hadm_id, c.subject_id, c.drug_raw, c.drug_normalized, c.drug_class,
               c.route, c.dose_val_rx, c.dose_unit_rx, c.starttime, c.stoptime,
               ROUND(DATE_DIFF('minute', a.admittime, c.starttime)/60.0, 2) AS start_hours,
               ROUND(DATE_DIFF('minute', a.admittime, c.stoptime)/60.0, 2) AS stop_hours,
               COALESCE(ANY_VALUE(ph.status), 'unknown') AS status,
               -- Finding #9 + abstention: a status read off a row with a
               -- corrupt timestamp is not a fact. Corrupt check wins first.
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
        JOIN read_csv_auto('{BASE}/hosp/admissions.csv') a ON a.hadm_id = c.hadm_id
        LEFT JOIN ph ON ph.hadm_id = c.hadm_id
                    AND LOWER(ph.medication) LIKE '%' || c.drug_normalized || '%'
        GROUP BY c.hadm_id, c.subject_id, c.drug_raw, c.drug_normalized, c.drug_class,
                 c.route, c.dose_val_rx, c.dose_unit_rx, c.starttime, c.stoptime,
                 a.admittime, c.n_orders
    """).df()

    # --- labs: hours (#3), first-of-stay flag ---
    out['labs'] = con.execute(f"""
        SELECT l.subject_id, l.hadm_id, l.itemid,
               dl.label, dl.category,
               l.charttime,
               ROUND(DATE_DIFF('minute', a.admittime, l.charttime)/60.0, 2) AS hours_since_admit,
               l.valuenum, l.valueuom, l.ref_range_lower, l.ref_range_upper, l.flag,
               (ROW_NUMBER() OVER (PARTITION BY l.hadm_id, dl.label ORDER BY l.charttime) = 1)
                   AS is_first_of_stay
        FROM read_csv_auto('{BASE}/hosp/labevents.csv') l
        JOIN read_csv_auto('{BASE}/hosp/d_labitems.csv') dl ON dl.itemid = l.itemid
        JOIN read_csv_auto('{BASE}/hosp/admissions.csv') a ON a.hadm_id = l.hadm_id
        WHERE l.hadm_id IN (SELECT DISTINCT hadm_id FROM cohort)
          AND dl.label IS NOT NULL
    """).df()

    # --- vitals: keyed on stay_id (#2), short codes ---
    out['vitals'] = con.execute(f"""
        SELECT ce.stay_id, ce.hadm_id, ce.subject_id, ce.itemid,
               di.label,
               {VITAL_CASE} AS vital_code,
               ce.charttime,
               ROUND(DATE_DIFF('minute', a.admittime, ce.charttime)/60.0, 2) AS hours_since_admit,
               ce.valuenum, ce.valueuom
        FROM read_csv_auto('{BASE}/icu/chartevents.csv') ce
        JOIN read_csv_auto('{BASE}/icu/d_items.csv') di ON di.itemid = ce.itemid
        JOIN read_csv_auto('{BASE}/hosp/admissions.csv') a ON a.hadm_id = ce.hadm_id
        WHERE ce.stay_id IN (SELECT stay_id FROM cohort)
          AND ce.itemid IN ({VITAL_IDS})
          AND ce.valuenum IS NOT NULL
    """).df()

    # --- outputs: nearly missed, holds the 1000ml drain (#11) ---
    out['outputs'] = con.execute(f"""
        SELECT oe.stay_id, oe.hadm_id, oe.subject_id,
               di.label, oe.charttime,
               ROUND(DATE_DIFF('minute', a.admittime, oe.charttime)/60.0, 2) AS hours_since_admit,
               oe.value, oe.valueuom
        FROM read_csv_auto('{BASE}/icu/outputevents.csv') oe
        JOIN read_csv_auto('{BASE}/icu/d_items.csv') di ON di.itemid = oe.itemid
        JOIN read_csv_auto('{BASE}/hosp/admissions.csv') a ON a.hadm_id = oe.hadm_id
        WHERE oe.hadm_id IN (SELECT DISTINCT hadm_id FROM cohort)
    """).df()

    con.close()
    for k, v in out.items():
        print(f"  {k:15} {len(v):>8} rows")
    return out


# ---------------------------------------------------------------
# Load into Aurora
# ---------------------------------------------------------------
LOAD_ORDER = [
    ('patients',    ['subject_id', 'gender', 'anchor_age', 'dod']),
    ('admissions',  ['hadm_id', 'subject_id', 'admittime', 'dischtime',
                     'admission_type', 'admission_location', 'discharge_location',
                     'insurance', 'hospital_expire_flag', 'hosp_days', 'icu_days',
                     'arrival_unit', 'cohort']),
    ('icu_stays',   ['stay_id', 'hadm_id', 'subject_id', 'first_careunit',
                     'last_careunit', 'intime', 'outtime', 'los_days', 'stay_rank']),
    ('transfers',   ['hadm_id', 'subject_id', 'careunit', 'intime', 'outtime',
                     'hours_since_admit', 'seq_num']),
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
    ('vitals',      ['stay_id', 'hadm_id', 'subject_id', 'itemid', 'label',
                     'vital_code', 'charttime', 'hours_since_admit',
                     'valuenum', 'valueuom']),
    ('outputs',     ['stay_id', 'hadm_id', 'subject_id', 'label', 'charttime',
                     'hours_since_admit', 'value', 'valueuom']),
]


def load(data):
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus",
        user="grusadmin", password=PWD, sslmode="require"
    )
    conn.execute("SET search_path TO grus, public")

    # Reverse order for truncate: children before parents
    for table, _ in reversed(LOAD_ORDER):
        conn.execute(f"TRUNCATE TABLE {table} CASCADE")
    conn.commit()
    print("truncated")

    for table, cols in LOAD_ORDER:
        df = data[table][cols]
        df = df.where(df.notna(), None)
        placeholders = ",".join(["%s"] * len(cols))
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
        rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
        print(f"  loaded {table:15} {len(rows):>8} rows")

    # --- verify against the demo patient ---
    print("\nverification —", DEMO_HADM)
    checks = [
        ("INR values", f"""SELECT valuenum FROM labs
                           WHERE hadm_id={DEMO_HADM} AND label='INR(PT)'
                           ORDER BY hours_since_admit"""),
        ("anticoagulants", f"""SELECT drug_normalized, is_active, time_valid FROM medications
                               WHERE hadm_id={DEMO_HADM} AND drug_class='anticoagulant'"""),
        ("unknown status", """SELECT COUNT(*) FROM medications WHERE is_active='unknown'"""),
        ("pericardial drain", f"""SELECT value FROM outputs
                                  WHERE hadm_id={DEMO_HADM} AND label='Pericardial'
                                  ORDER BY hours_since_admit"""),
        ("invalid timestamps", """SELECT COUNT(*) FROM medications WHERE NOT time_valid"""),
    ]
    for name, q in checks:
        print(f"  {name:20} {conn.execute(q).fetchall()}")

    conn.close()


if __name__ == "__main__":
    print("extracting...")
    data = extract()
    print("\nloading...")
    load(data)
    print("\ndone")