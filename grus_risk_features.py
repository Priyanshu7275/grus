"""
GRUS — Risk model features and labels

Builds the training table from tier 2 (20,000 patients in S3).

One row per patient-hour. Features describe what was known AT that hour.
Labels describe what happened AFTER it.

The whole file exists to keep those two apart. Every feature is computed
from data at or before hour T; every label from data strictly after T.
Get that wrong and the AUC looks superb and the model is worthless — it
would be reading the answer key, the same failure the rules engine's
as_of cutoff exists to prevent.

Seven candidate labels. Each predicts a DECISION a clinician makes in the
next few hours, not an outcome they cannot control. 'Will this patient
die' is not actionable; 'will they need blood in twelve hours' is.
"""

import os
import duckdb
import pandas as pd
from grus_config import Paths



TIER2 = Paths.TIER2
OUT = str(Paths.MODEL_DIR)
os.makedirs(OUT, exist_ok=True)

# Labs the models read. Anything not here is ignored, which keeps the
# feature table narrow enough to train quickly.
LAB_SET = [
    "Hemoglobin", "Hematocrit", "Platelet Count", "INR(PT)",
    "Creatinine", "Urea Nitrogen", "Sodium", "Potassium",
    "Bicarbonate", "White Blood Cells", "Lactate", "Glucose",
    "pH", "Base Excess", "Albumin", "Magnesium", "Calcium, Total",
]

VITAL_SET = ["hr", "sbp", "dbp", "map", "spo2", "rr", "temp_c",
             "gcs_eye", "gcs_verbal", "gcs_motor"]


def connect():
    con = duckdb.connect()
    con.execute("SET memory_limit='6GB'")
    for name in ["stay_summary", "labs_clean", "vitals_hourly", "vitals_clean"]:
        con.execute(f"""
            CREATE OR REPLACE VIEW {name} AS
            SELECT * FROM read_parquet('{TIER2}/{name}/*.parquet')
        """)
    return con


# ---------------------------------------------------------------
# Labs on an hourly grid
#
# Labs are drawn irregularly — a creatinine at 3h, the next at 27h. The
# model needs a value at every hour, so each lab is carried forward from
# its last result. Carrying forward is safe; interpolating between a
# past and a FUTURE value would leak.
# ---------------------------------------------------------------
def build_hourly_labs(con):
    lab_list = ",".join(f"'{l}'" for l in LAB_SET)

    con.execute(f"""
        CREATE OR REPLACE TABLE lab_hourly AS
        WITH binned AS (
            SELECT hadm_id,
                   CAST(FLOOR(hours_since_admit) AS INT) AS hour_bin,
                   lab_label,
                   AVG(valuenum) AS value
            FROM labs_clean
            WHERE lab_label IN ({lab_list})
              AND valuenum IS NOT NULL
              AND hours_since_admit IS NOT NULL
              AND hours_since_admit BETWEEN -12 AND 336
            GROUP BY 1,2,3
        )
        SELECT * FROM binned
    """)

    n = con.execute("SELECT COUNT(*) FROM lab_hourly").fetchone()[0]
    print(f"  lab observations: {n:,}")


def pivot_labs(con):
    """Long to wide, then forward-fill within each admission."""
    cases = ",\n".join(
        f"       MAX(CASE WHEN lab_label = '{l}' THEN value END) "
        f'AS "lab_{l.replace(" ", "_").replace("(", "").replace(")", "").replace(",", "")}"'
        for l in LAB_SET
    )
    con.execute(f"""
        CREATE OR REPLACE TABLE lab_wide AS
        SELECT hadm_id, hour_bin,
{cases}
        FROM lab_hourly GROUP BY hadm_id, hour_bin
    """)


# ---------------------------------------------------------------
# The hourly grid: one row per stay per hour
# ---------------------------------------------------------------
def build_grid(con, max_hours=168):
    con.execute(f"""
        CREATE OR REPLACE TABLE grid AS
        WITH bounds AS (
            SELECT stay_id, hadm_id, subject_id,
                   CAST(FLOOR(icu_offset_hours) AS INT) AS start_h,
                   CAST(FLOOR(icu_offset_hours + icu_days * 24) AS INT) AS end_h,
                   gender, anchor_age, admission_type, died_in_hospital
            FROM stay_summary
            WHERE icu_days IS NOT NULL AND icu_days > 0.25
        )
        SELECT b.stay_id, b.hadm_id, b.subject_id, h.hour_bin,
               b.gender, b.anchor_age, b.admission_type, b.died_in_hospital,
               b.end_h
        FROM bounds b
        CROSS JOIN (SELECT UNNEST(RANGE(0, {max_hours})) AS hour_bin) h
        WHERE h.hour_bin >= b.start_h
          AND h.hour_bin <= LEAST(b.end_h, b.start_h + {max_hours})
    """)
    n = con.execute("SELECT COUNT(*) FROM grid").fetchone()[0]
    s = con.execute("SELECT COUNT(DISTINCT stay_id) FROM grid").fetchone()[0]
    print(f"  grid: {n:,} patient-hours across {s:,} stays")


# ---------------------------------------------------------------
# FEATURES — data at or before hour T
#
# Every window here looks BACKWARD. A 6-hour delta compares hour T with
# hour T-6, never T+6.
# ---------------------------------------------------------------
def build_features(con):
    vital_cols = ",\n".join(f"           v.{c}" for c in VITAL_SET)

    deltas = []
    for c in ["hr", "sbp", "map", "spo2", "rr", "shock_index"]:
        deltas.append(
            f"           {c} - LAG({c}, 1) OVER w AS {c}_d1h,\n"
            f"           {c} - LAG({c}, 6) OVER w AS {c}_d6h"
        )
    delta_sql = ",\n".join(deltas)

    lab_cols = [f'lab_{l.replace(" ", "_").replace("(", "").replace(")", "").replace(",", "")}'
                for l in LAB_SET]

    # Forward-fill: last known value at or before this hour.
    ffill = ",\n".join(
        f'           LAST_VALUE("{c}" IGNORE NULLS) OVER '
        f"(PARTITION BY g.hadm_id ORDER BY g.hour_bin "
        f"ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS {c}"
        for c in lab_cols
    )

    con.execute(f"""
        CREATE OR REPLACE TABLE feat_base AS
        SELECT g.stay_id, g.hadm_id, g.subject_id, g.hour_bin,
               g.anchor_age,
               CASE WHEN g.gender = 'M' THEN 1 ELSE 0 END AS is_male,
               CASE WHEN g.admission_type LIKE '%EMER%' THEN 1 ELSE 0 END AS is_emergency,
               g.died_in_hospital, g.end_h,
{vital_cols},
               v.shock_index, v.gcs_total,
{ffill}
        FROM grid g
        LEFT JOIN vitals_hourly v
               ON v.stay_id = g.stay_id AND v.hour_bin = g.hour_bin
        LEFT JOIN lab_wide l
               ON l.hadm_id = g.hadm_id AND l.hour_bin = g.hour_bin
    """)

    con.execute(f"""
        CREATE OR REPLACE TABLE features AS
        SELECT *,
{delta_sql},
               lab_Hemoglobin - LAG(lab_Hemoglobin, 6) OVER w AS hgb_d6h,
               lab_Creatinine - LAG(lab_Creatinine, 24) OVER w AS creat_d24h,
               lab_Lactate - LAG(lab_Lactate, 6) OVER w AS lactate_d6h,
               MIN(lab_Hemoglobin) OVER w_all AS hgb_min_so_far,
               MIN(lab_Creatinine) OVER w_all AS creat_baseline,
               MIN(gcs_total) OVER w_all AS gcs_min_so_far
        FROM feat_base
        WINDOW
            w AS (PARTITION BY stay_id ORDER BY hour_bin),
            w_all AS (PARTITION BY stay_id ORDER BY hour_bin
                      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
    """)

    n = con.execute("SELECT COUNT(*) FROM features").fetchone()[0]
    print(f"  features: {n:,} rows")


# ---------------------------------------------------------------
# LABELS — data strictly AFTER hour T
#
# Each is a decision a clinician makes in the next few hours, not an
# outcome they cannot influence.
# ---------------------------------------------------------------
def build_labels(con):
    con.execute("""
        CREATE OR REPLACE TABLE labels AS
        WITH fwd AS (
            SELECT stay_id, hour_bin,

                   -- HAEMATOLOGY, next 12h
                   MIN(lab_Hemoglobin) OVER w12 AS hgb_min_12h,
                   MIN(lab_Platelet_Count) OVER w12 AS plt_min_12h,

                   -- next 6h
                   MIN(lab_Hemoglobin) OVER w6 AS hgb_min_6h,
                   MAX(hr) OVER w6 AS hr_max_6h,
                   MIN(sbp) OVER w6 AS sbp_min_6h,
                   MAX(shock_index) OVER w6 AS si_max_6h,
                   MIN(gcs_total) OVER w6 AS gcs_min_6h,
                   MIN(spo2) OVER w6 AS spo2_min_6h,
                   MAX(rr) OVER w6 AS rr_max_6h,
                   MAX(lab_Lactate) OVER w6 AS lactate_max_6h,

                   -- next 12h chemistry
                   MAX(lab_Potassium) OVER w12 AS k_max_12h,
                   MIN(lab_Potassium) OVER w12 AS k_min_12h,
                   MAX(lab_Sodium) OVER w12 AS na_max_12h,
                   MIN(lab_Sodium) OVER w12 AS na_min_12h,
                   MAX(lab_White_Blood_Cells) OVER w12 AS wbc_max_12h,
                   MAX(lab_Lactate) OVER w12 AS lactate_max_12h,

                   -- next 24h renal
                   MAX(lab_Creatinine) OVER w24 AS creat_max_24h,

                   -- current values, for comparison
                   lab_Hemoglobin AS hgb_now,
                   lab_Creatinine AS creat_now,
                   creat_baseline,
                   gcs_total AS gcs_now,
                   spo2 AS spo2_now,
                   rr AS rr_now,
                   hr AS hr_now,
                   sbp AS sbp_now,
                   lab_Lactate AS lactate_now,
                   lab_Potassium AS k_now,
                   lab_Sodium AS na_now
            FROM features
            WINDOW
                w6  AS (PARTITION BY stay_id ORDER BY hour_bin
                        ROWS BETWEEN 1 FOLLOWING AND 6 FOLLOWING),
                w12 AS (PARTITION BY stay_id ORDER BY hour_bin
                        ROWS BETWEEN 1 FOLLOWING AND 12 FOLLOWING),
                w24 AS (PARTITION BY stay_id ORDER BY hour_bin
                        ROWS BETWEEN 1 FOLLOWING AND 24 FOLLOWING)
        )
        SELECT stay_id, hour_bin,

            -- 1. Needs transfusion within 12h
            --    Hgb falls 2 g/dL, or drops below the transfusion threshold.
            CASE WHEN hgb_now IS NOT NULL AND hgb_min_12h IS NOT NULL
                      AND (hgb_now - hgb_min_12h >= 2.0 OR hgb_min_12h < 7.0)
                 THEN 1 ELSE 0 END AS needs_transfusion_12h,

            -- 2. Bleeding progression within 6h
            --    Falling haemoglobin WITH rising heart rate or falling
            --    pressure. No single threshold catches this; the
            --    combination is the signal.
            CASE WHEN hgb_now IS NOT NULL AND hgb_min_6h IS NOT NULL
                      AND hgb_now - hgb_min_6h >= 1.0
                      AND ((hr_now IS NOT NULL AND hr_max_6h - hr_now >= 15)
                        OR (sbp_now IS NOT NULL AND sbp_now - sbp_min_6h >= 15)
                        OR si_max_6h >= 1.0)
                 THEN 1 ELSE 0 END AS bleeding_progression_6h,

            -- 3. AKI within 24h — KDIGO: +0.3 absolute or 1.5x baseline
            CASE WHEN creat_now IS NOT NULL AND creat_max_24h IS NOT NULL
                      AND (creat_max_24h - creat_now >= 0.3
                        OR (creat_baseline > 0
                            AND creat_max_24h >= 1.5 * creat_baseline))
                 THEN 1 ELSE 0 END AS aki_risk_24h,

            -- 4. Neurological decline within 6h — GCS drops 2 points
            CASE WHEN gcs_now IS NOT NULL AND gcs_min_6h IS NOT NULL
                      AND gcs_now - gcs_min_6h >= 2
                 THEN 1 ELSE 0 END AS neuro_decline_6h,

            -- 5. Sepsis progression within 12h
            --    Lactate climbing, or a white count moving to either extreme.
            CASE WHEN (lactate_max_12h >= 4.0
                       AND (lactate_now IS NULL OR lactate_max_12h - lactate_now >= 1.0))
                   OR (wbc_max_12h >= 20 OR wbc_max_12h < 4)
                 THEN 1 ELSE 0 END AS sepsis_progression_12h,

            -- 6. Electrolyte crisis within 12h
            --    Potassium or sodium heading somewhere arrhythmogenic.
            CASE WHEN k_max_12h >= 6.0 OR k_min_12h <= 3.0
                   OR (na_now IS NOT NULL
                       AND (ABS(na_max_12h - na_now) >= 8
                         OR ABS(na_now - na_min_12h) >= 8))
                 THEN 1 ELSE 0 END AS electrolyte_crisis_12h,

            -- 7. Respiratory decline within 6h
            CASE WHEN (spo2_now IS NOT NULL AND spo2_min_6h IS NOT NULL
                       AND spo2_now - spo2_min_6h >= 4)
                   OR (rr_now IS NOT NULL AND rr_max_6h IS NOT NULL
                       AND rr_max_6h - rr_now >= 8)
                   OR spo2_min_6h < 88
                 THEN 1 ELSE 0 END AS respiratory_decline_6h

        FROM fwd
    """)
    print("  labels built")


LABEL_COLS = [
    "needs_transfusion_12h", "bleeding_progression_6h", "aki_risk_24h",
    "neuro_decline_6h", "sepsis_progression_12h",
    "electrolyte_crisis_12h", "respiratory_decline_6h",
]


def assemble(con):
    """
    Join features to labels and drop rows near the end of a stay.

    The last hours of a stay have no future to look at, so their labels
    would all be zero — not because nothing happened but because nothing
    was recorded. Training on those teaches the model that late hours are
    safe, which is exactly backwards.
    """
    label_sql = ",\n".join(f"               l.{c}" for c in LABEL_COLS)

    con.execute(f"""
        CREATE OR REPLACE TABLE training AS
        SELECT f.*,
{label_sql}
        FROM features f
        JOIN labels l ON l.stay_id = f.stay_id AND l.hour_bin = f.hour_bin
        WHERE f.hour_bin <= f.end_h - 24
          AND f.hour_bin >= 1
    """)

    n = con.execute("SELECT COUNT(*) FROM training").fetchone()[0]
    s = con.execute("SELECT COUNT(DISTINCT stay_id) FROM training").fetchone()[0]
    print(f"\ntraining set: {n:,} rows, {s:,} stays")

    print("\npositive rate per label:")
    print(f"  {'label':28} {'positives':>10} {'rate':>8}")
    keep, drop = [], []
    for c in LABEL_COLS:
        pos, tot = con.execute(
            f"SELECT SUM({c}), COUNT(*) FROM training").fetchone()
        rate = 100 * pos / tot if tot else 0
        flag = ""
        if rate < 3:
            flag = "  TOO RARE"
            drop.append(c)
        elif rate > 60:
            flag = "  TOO COMMON"
            drop.append(c)
        else:
            keep.append(c)
        print(f"  {c:28} {pos:>10,} {rate:>7.1f}%{flag}")

    print(f"\nkeeping {len(keep)}: {keep}")
    if drop:
        print(f"dropping {len(drop)}: {drop}")
        print("  a model that predicts a rare event badly is worse than no")
        print("  prediction; one that fires on most patients is not a signal")

    return keep, drop


def export(con, keep):
    """
    Split by STAY, not by row.

    Rows from the same stay are highly correlated — hour 12 and hour 13
    are nearly identical. Splitting by row puts a patient's hour 12 in
    training and hour 13 in test, so the model has effectively seen the
    answer. Split by stay and the test set is genuinely unseen patients.
    """
    con.execute("""
        CREATE OR REPLACE TABLE split AS
        SELECT stay_id,
               CASE WHEN hash(stay_id) % 10 < 7 THEN 'train'
                    WHEN hash(stay_id) % 10 < 8 THEN 'val'
                    ELSE 'test' END AS split
        FROM (SELECT DISTINCT stay_id FROM training)
    """)

    for name in ["train", "val", "test"]:
        con.execute(f"""
            COPY (
                SELECT t.* FROM training t
                JOIN split s ON s.stay_id = t.stay_id
                WHERE s.split = '{name}'
            ) TO '{OUT}/{name}.parquet' (FORMAT PARQUET)
        """)
        n = con.execute(f"""
            SELECT COUNT(*) FROM training t JOIN split s
            ON s.stay_id = t.stay_id WHERE s.split = '{name}'
        """).fetchone()[0]
        print(f"  {name:6} {n:>9,} rows -> {OUT}/{name}.parquet")

    with open(f"{OUT}/labels.txt", "w") as f:
        f.write("\n".join(keep))
    print(f"  kept labels written to {OUT}/labels.txt")


def sanity_check(con):
    """
    The check that matters: can a feature see the future?

    If a label is perfectly predicted by a single feature, something has
    leaked. Correlation near 1.0 means the feature IS the label.
    """
    print("\nleakage check — feature/label correlation")
    suspects = ["hgb_d6h", "creat_d24h", "shock_index", "gcs_total",
                "lab_Hemoglobin", "lab_Creatinine"]
    worst = 0
    for lab in LABEL_COLS:
        for feat in suspects:
            try:
                c = con.execute(
                    f"SELECT CORR({feat}, {lab}) FROM training").fetchone()[0]
            except Exception:
                continue
            if c is not None and abs(c) > 0.8:
                print(f"  SUSPICIOUS  {feat} vs {lab}: {c:.3f}")
                worst = max(worst, abs(c))
    if worst == 0:
        print("  no feature correlates above 0.8 with any label")


if __name__ == "__main__":
    print("building risk model training set")
    print("=" * 60)

    con = connect()

    print("\nlabs:")
    build_hourly_labs(con)
    pivot_labs(con)

    print("\ngrid:")
    build_grid(con)

    print("\nfeatures:")
    build_features(con)

    print("\nlabels:")
    build_labels(con)

    keep, drop = assemble(con)
    sanity_check(con)

    print("\nexporting:")
    export(con, keep)

    con.close()
    print("\ndone")