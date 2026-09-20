"""
GRUS — Risk scoring

Builds features for a patient at a point in time and calls the SageMaker
endpoints for a prediction.


"""

import os
import json
import boto3
import psycopg
from grus_config import DB, AWS, Paths



REGION = AWS.REGION
MODEL_DIR = Paths.MODEL_DIR

runtime = boto3.client("sagemaker-runtime", region_name=REGION)

# Same vitals the training pipeline used, same short codes.
VITAL_SET = ["hr", "sbp", "dbp", "map", "spo2", "rr", "temp_c",
             "gcs_eye", "gcs_verbal", "gcs_motor"]

LAB_SET = [
    "Hemoglobin", "Hematocrit", "Platelet Count", "INR(PT)",
    "Creatinine", "Urea Nitrogen", "Sodium", "Potassium",
    "Bicarbonate", "White Blood Cells", "Lactate", "Glucose",
    "pH", "Base Excess", "Albumin", "Magnesium", "Calcium, Total",
]

# What each score means and what a clinician does about it. The model
# outputs a probability; this is what makes it actionable.
MEANING = {
    "needs_transfusion_12h": {
        "label": "Transfusion likely within 12h",
        "action": "Cross-match now rather than when it is needed.",
        "basis": "Haemoglobin trajectory, shock index, lactate.",
    },
    "aki_risk_24h": {
        "label": "Acute kidney injury within 24h",
        "action": "Hold contrast. Review nephrotoxic drugs.",
        "basis": "Creatinine trend against this admission's baseline.",
    },
    "electrolyte_crisis_12h": {
        "label": "Potassium or sodium crisis within 12h",
        "action": "Recheck electrolytes. Consider an ECG.",
        "basis": "Potassium and sodium trajectory.",
    },
}


import boto3

_s3 = boto3.client("s3", region_name=AWS.REGION)
_S3_BUCKET = AWS.BUCKET
_S3_PREFIX = "model-config"


def _load(name, default=None):
    """
    Load a small model-config file from S3.

    These are tiny lookup tables (endpoint names, thresholds), not
    training data — fetched at import time so the server always has the
    latest values without needing them bundled into the deployment.
    """
    key = f"{_S3_PREFIX}/{name}"
    try:
        obj = _s3.get_object(Bucket=_S3_BUCKET, Key=key)
        return json.loads(obj["Body"].read())
    except Exception as e:
        print(f"DEBUG: failed to load {key} from S3: {e}", flush=True)
        return default


FEATURES = _load("features.json", [])
ENDPOINTS = _load("endpoints.json", {})
THRESHOLDS = _load("thresholds.json", {})
REPORT = _load("report.json", {"labels": {}})


class FeatureBuilder:
    """
    Reconstructs the training feature vector for one patient-hour.

    Mirrors grus_risk_features.py deliberately. If that file changes,
    this one must change with it — the two are a pair, and drift between
    them produces confident nonsense rather than an error.
    """

    def __init__(self, conn, hadm_id, as_of_hours=None):
        self.conn = conn
        self.hadm_id = hadm_id
        self.as_of = as_of_hours

        row = conn.execute("""
            SELECT p.anchor_age, p.gender, a.admission_type,
                   (SELECT stay_id FROM icu_stays
                     WHERE hadm_id = a.hadm_id ORDER BY stay_rank LIMIT 1)
                     AS stay_id
            FROM admissions a JOIN patients p ON p.subject_id = a.subject_id
            WHERE a.hadm_id = %s
        """, (hadm_id,)).fetchone()
        if not row:
            raise ValueError(f"no admission {hadm_id}")

        self.age, self.gender, self.adm_type, self.stay_id = row

    def _cap(self, col="hours_since_admit"):
        if self.as_of is None:
            return ""
        return f" AND ({col} IS NULL OR {col} <= {self.as_of}) "

    def _hour(self):
        """
        The hour being scored: the as_of cutoff, or the latest hour with
        any data when scoring the full record.
        """
        if self.as_of is not None:
            return int(self.as_of)
        r = self.conn.execute(f"""
            SELECT MAX(hours_since_admit) FROM vitals WHERE hadm_id = %s
        """, (self.hadm_id,)).fetchone()
        return int(float(r[0])) if r and r[0] is not None else 0

    def _vitals_at(self, hour):
        """Latest value of each vital at or before this hour."""
        rows = self.conn.execute(f"""
            SELECT DISTINCT ON (vital_code) vital_code, valuenum
            FROM vitals
            WHERE hadm_id = %s AND valuenum IS NOT NULL
              AND vital_code IS NOT NULL AND hours_since_admit <= %s
              {self._cap()}
            ORDER BY vital_code, hours_since_admit DESC
        """, (self.hadm_id, hour)).fetchall()
        return {r[0]: float(r[1]) for r in rows}

    def _labs_at(self, hour):
        """
        Forward-filled labs: the last result at or before this hour.

        Carrying forward is safe. Interpolating between a past and a
        future value would leak.
        """
        rows = self.conn.execute(f"""
            SELECT DISTINCT ON (label) label, valuenum
            FROM labs
            WHERE hadm_id = %s AND valuenum IS NOT NULL
              AND label = ANY(%s) AND hours_since_admit <= %s
              {self._cap()}
            ORDER BY label, hours_since_admit DESC
        """, (self.hadm_id, LAB_SET, hour)).fetchall()
        return {r[0]: float(r[1]) for r in rows}

    @staticmethod
    def _lab_key(label):
        return "lab_" + (label.replace(" ", "_").replace("(", "")
                         .replace(")", "").replace(",", ""))

    def build(self):
        hour = self._hour()

        now_v = self._vitals_at(hour)
        prev1_v = self._vitals_at(max(hour - 1, 0))
        prev6_v = self._vitals_at(max(hour - 6, 0))

        now_l = self._labs_at(hour)
        prev6_l = self._labs_at(max(hour - 6, 0))
        prev24_l = self._labs_at(max(hour - 24, 0))

        def si(v):
            hr, sbp = v.get("hr"), v.get("sbp")
            return round(hr / sbp, 4) if hr and sbp else None

        def gcs(v):
            parts = [v.get(f"gcs_{p}") for p in ("eye", "verbal", "motor")]
            return sum(parts) if all(p is not None for p in parts) else None

        # Running minima — the nadir matters more than the current value
        # for anything measuring bleeding risk.
        mins = self.conn.execute(f"""
            SELECT MIN(CASE WHEN label = 'Hemoglobin' THEN valuenum END),
                   MIN(CASE WHEN label = 'Creatinine' THEN valuenum END)
            FROM labs
            WHERE hadm_id = %s AND valuenum IS NOT NULL
              AND hours_since_admit <= %s {self._cap()}
        """, (self.hadm_id, hour)).fetchone()
        hgb_min = float(mins[0]) if mins and mins[0] is not None else None
        creat_base = float(mins[1]) if mins and mins[1] is not None else None

        gcs_min = self.conn.execute(f"""
            SELECT MIN(total) FROM (
                SELECT hours_since_admit,
                       SUM(valuenum) FILTER (
                         WHERE vital_code IN ('gcs_eye','gcs_verbal','gcs_motor')
                       ) AS total
                FROM vitals
                WHERE hadm_id = %s AND hours_since_admit <= %s {self._cap()}
                GROUP BY hours_since_admit
                HAVING COUNT(*) FILTER (
                  WHERE vital_code IN ('gcs_eye','gcs_verbal','gcs_motor')) = 3
            ) t
        """, (self.hadm_id, hour)).fetchone()

        f = {
            "anchor_age": self.age,
            "is_male": 1 if self.gender == "M" else 0,
            "is_emergency": 1 if "EMER" in (self.adm_type or "") else 0,
            "hour_bin": hour,
            "shock_index": si(now_v),
            "gcs_total": gcs(now_v),
            "hgb_min_so_far": hgb_min,
            "creat_baseline": creat_base,
            "gcs_min_so_far": float(gcs_min[0]) if gcs_min and gcs_min[0] else None,
        }

        for c in VITAL_SET:
            f[c] = now_v.get(c)

        for c in ["hr", "sbp", "map", "spo2", "rr"]:
            cur, p1, p6 = now_v.get(c), prev1_v.get(c), prev6_v.get(c)
            f[f"{c}_d1h"] = round(cur - p1, 3) if cur is not None and p1 is not None else None
            f[f"{c}_d6h"] = round(cur - p6, 3) if cur is not None and p6 is not None else None

        si_now, si_1, si_6 = si(now_v), si(prev1_v), si(prev6_v)
        f["shock_index_d1h"] = round(si_now - si_1, 4) if si_now and si_1 else None
        f["shock_index_d6h"] = round(si_now - si_6, 4) if si_now and si_6 else None

        for lab in LAB_SET:
            f[self._lab_key(lab)] = now_l.get(lab)

        hgb, hgb6 = now_l.get("Hemoglobin"), prev6_l.get("Hemoglobin")
        f["hgb_d6h"] = round(hgb - hgb6, 3) if hgb and hgb6 else None

        cr, cr24 = now_l.get("Creatinine"), prev24_l.get("Creatinine")
        f["creat_d24h"] = round(cr - cr24, 3) if cr and cr24 else None

        lac, lac6 = now_l.get("Lactate"), prev6_l.get("Lactate")
        f["lactate_d6h"] = round(lac - lac6, 3) if lac and lac6 else None

        # -999 is what the training pipeline used for missing. The model
        # learned that convention; anything else changes its meaning.
        vector = [f.get(c) if f.get(c) is not None else -999 for c in FEATURES]

        present = sum(1 for c in FEATURES if f.get(c) is not None)
        return vector, {
            "hour": hour,
            "features_present": present,
            "features_total": len(FEATURES),
            "coverage": round(present / len(FEATURES), 3) if FEATURES else 0,
        }


def score(conn, hadm_id, as_of_hours=None):
    """
    Risk predictions for one patient at one moment.

    Returns every shipped model's probability, whether it crosses that
    model's threshold, and how much of the feature vector was actually
    available — a score built from four values out of fifty is not the
    same claim as one built from forty.
    """
    if not ENDPOINTS:
        return {"available": False,
                "reason": "No endpoints deployed. Run grus_sagemaker.py deploy."}

    try:
        vector, meta = FeatureBuilder(conn, hadm_id, as_of_hours).build()
    except Exception as e:
        return {"available": False, "reason": f"feature build failed: {e}"}

    # Too little data is a reason to abstain, not to guess. A model given
    # mostly -999 will still return a confident-looking number.
    if meta["coverage"] < 0.25:
        return {
            "available": False,
            "reason": "insufficient data to score",
            "detail": f"only {meta['features_present']} of "
                      f"{meta['features_total']} features available at "
                      f"hour {meta['hour']}",
            "note": "Emergency department hours are often too sparse to "
                    "score. This is not a low-risk result.",
            **meta,
        }

    payload = ",".join(str(v) for v in vector)
    predictions = []

    for label, info in ENDPOINTS.items():
        try:
            r = runtime.invoke_endpoint(
                EndpointName=info["endpoint"],
                ContentType="text/csv",
                Body=payload.encode())
            p = float(r["Body"].read().decode().strip())
        except Exception as e:
            predictions.append({
                "label": label, "available": False,
                "reason": str(e)[:120],
                **MEANING.get(label, {}),
            })
            continue

        thr = THRESHOLDS.get(label, {}).get("threshold",
                                            info.get("threshold", 0.5))
        m = REPORT.get("labels", {}).get(label, {}).get("metrics", {})

        predictions.append({
            "label": label,
            "available": True,
            "probability": round(p, 4),
            "threshold": round(thr, 4),
            "alert": p >= thr,
            "confidence": "high" if meta["coverage"] > 0.6 else "limited",
            **MEANING.get(label, {}),
            "model_performance": {
                "auc": round(m.get("auc", 0), 3),
                "precision": round(m.get("precision", 0), 2),
                "recall": round(m.get("recall", 0), 2),
                "note": f"At this threshold the model is right "
                        f"{m.get('precision', 0):.0%} of the time it fires "
                        f"and catches {m.get('recall', 0):.0%} of events.",
            },
        })

    firing = [p for p in predictions if p.get("alert")]

    return {
        "available": True,
        "hadm_id": hadm_id,
        "as_of_hours": as_of_hours,
        "scored_at_hour": meta["hour"],
        "feature_coverage": meta["coverage"],
        "coverage_note": (
            None if meta["coverage"] > 0.6 else
            f"Only {meta['features_present']} of {meta['features_total']} "
            f"features available. Treat these scores as provisional."),
        "predictions": predictions,
        "alerts_firing": len(firing),
        "disclaimer": "Predicted risk, not diagnosis. These models were "
                      "trained on 20,000 MIMIC-IV admissions and are one "
                      "signal beside nine deterministic rules.",
    }


def summarise(result):
    """A line per prediction, for the agent to read."""
    if not result.get("available"):
        return f"Risk scoring unavailable: {result.get('reason')}"

    lines = [f"Risk at hour {result['scored_at_hour']} "
             f"(feature coverage {result['feature_coverage']:.0%}):"]
    for p in result["predictions"]:
        if not p.get("available"):
            lines.append(f"  {p['label']}: unavailable")
            continue
        mark = "ALERT" if p["alert"] else "  ok "
        lines.append(f"  [{mark}] {p.get('label_text', p['label'])}: "
                     f"{p['probability']:.2f} (fires above {p['threshold']:.2f})")
        if p["alert"]:
            lines.append(f"           -> {p.get('action','')}")
    if result.get("coverage_note"):
        lines.append(f"  {result['coverage_note']}")
    return "\n".join(lines)


if __name__ == "__main__":
    from psycopg.rows import tuple_row

    HOST = "grus-db.cluster-ch02wk02ky83.ap-south-1.rds.amazonaws.com"
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus", user="grusadmin",
        password=os.getenv("DB_PASSWORD"), sslmode="require",
        keepalives=1, keepalives_idle=30, row_factory=tuple_row)
    conn.execute("SET search_path TO grus, public")

    print(f"features: {len(FEATURES)}")
    print(f"endpoints: {list(ENDPOINTS)}\n")

    for label, hadm, as_of in [
        ("28173870 hour 1",  28173870, 1),
        ("28173870 full",    28173870, None),
        ("27180495 full",    27180495, None),
    ]:
        print("=" * 66)
        print(label)
        print("=" * 66)
        r = score(conn, hadm, as_of)
        print(summarise(r))
        print()

    conn.close()
