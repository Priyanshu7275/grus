"""
GRUS — Scenario replay Lambda

Reads a scenario file from S3 and writes one hour of observations into
Aurora each time it runs. EventBridge invokes it on a schedule.

Paste this whole file into the Lambda console editor. It uses pg8000
rather than psycopg because pg8000 is pure Python — no compiled binaries,
so no layer and no Docker build. Slightly slower, which does not matter
for one insert a minute.

The scenario file is plain text you edit in the S3 console. Overwrite it
and the next tick restarts from hour 0 with the new numbers.

Environment variables to set on the function:

    DB_HOST          grus-db.cluster-xxxx.ap-south-1.rds.amazonaws.com
    DB_NAME          grus
    DB_USER          grusadmin
    DB_SECRET_ARN    arn:aws:secretsmanager:...:secret:grus/db-password-xxxxx
    SCENARIO_BUCKET  grus-mimic-data-etl
    SCENARIO_KEY     scenarios/active.txt
    STATE_KEY        scenarios/state.json

Timeout 60s. VPC: the same subnets and security group as Aurora.
"""

import os
import re
import ssl
import json
import boto3
import pg8000.native as pg

s3 = boto3.client("s3")
secrets = boto3.client("secretsmanager")

BUCKET = os.environ.get("SCENARIO_BUCKET", "grus-mimic-data-etl")
SCENARIO_KEY = os.environ.get("SCENARIO_KEY", "scenarios/active.txt")
STATE_KEY = os.environ.get("STATE_KEY", "scenarios/state.json")

DB_HOST = os.environ.get("DB_HOST")
DB_NAME = os.environ.get("DB_NAME", "grus")
DB_USER = os.environ.get("DB_USER", "grusadmin")
SECRET_ARN = os.environ.get("DB_SECRET_ARN")

_password = None


def _db():
    global _password
    if _password is None:
        v = secrets.get_secret_value(SecretId=SECRET_ARN)["SecretString"]
        try:
            _password = json.loads(v)["password"]
        except (json.JSONDecodeError, KeyError, TypeError):
            _password = v

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    conn = pg.Connection(user=DB_USER, host=DB_HOST, database=DB_NAME,
                         password=_password, port=5432,
                         ssl_context=ctx, timeout=10)
    conn.run("SET search_path TO grus, public")
    return conn


# ---------------------------------------------------------------
# Parsing
#
# The format is meant to be typed by a person, so the parser is
# forgiving: case, spacing and punctuation vary, blood pressure may be
# written 118/72, and a line may carry vitals, labs or both.
# ---------------------------------------------------------------

VITAL_ALIASES = {
    "hr": "hr", "pulse": "hr", "heartrate": "hr",
    "sbp": "sbp", "systolic": "sbp",
    "dbp": "dbp", "diastolic": "dbp",
    "map": "map",
    "spo2": "spo2", "sats": "spo2", "o2": "spo2", "sat": "spo2",
    "rr": "rr", "resp": "rr", "resprate": "rr",
    "temp": "temp_c", "tempc": "temp_c", "temperature": "temp_c",
    "gcse": "gcs_eye", "gcseye": "gcs_eye",
    "gcsv": "gcs_verbal", "gcsverbal": "gcs_verbal",
    "gcsm": "gcs_motor", "gcsmotor": "gcs_motor",
}

LAB_ALIASES = {
    "hgb": "Hemoglobin", "hb": "Hemoglobin", "hemoglobin": "Hemoglobin",
    "haemoglobin": "Hemoglobin",
    "hct": "Hematocrit", "hematocrit": "Hematocrit",
    "plt": "Platelet Count", "platelets": "Platelet Count",
    "inr": "INR(PT)", "inrpt": "INR(PT)",
    "pt": "PT", "ptt": "PTT",
    "creat": "Creatinine", "creatinine": "Creatinine",
    "lactate": "Lactate", "lac": "Lactate",
    "k": "Potassium", "potassium": "Potassium",
    "na": "Sodium", "sodium": "Sodium",
    "wbc": "White Blood Cells",
    "bun": "Urea Nitrogen", "urea": "Urea Nitrogen",
    "bicarb": "Bicarbonate", "hco3": "Bicarbonate",
    "be": "Base Excess", "baseexcess": "Base Excess",
    "trop": "Troponin T", "troponin": "Troponin T",
    "glucose": "Glucose", "mg": "Magnesium", "albumin": "Albumin",
    "ph": "pH",
}

VITAL_UNITS = {"hr": "bpm", "sbp": "mmHg", "dbp": "mmHg", "map": "mmHg",
               "spo2": "%", "rr": "insp/min", "temp_c": "C"}

LAB_UNITS = {"Hemoglobin": "g/dL", "Hematocrit": "%",
             "Platelet Count": "K/uL", "Creatinine": "mg/dL",
             "Lactate": "mmol/L", "Potassium": "mEq/L", "Sodium": "mEq/L",
             "White Blood Cells": "K/uL", "Urea Nitrogen": "mg/dL",
             "Bicarbonate": "mEq/L", "Troponin T": "ng/mL",
             "Glucose": "mg/dL", "PT": "sec", "PTT": "sec"}

HOUR_RE = re.compile(r"^\s*(?:hour|hr|h|t)\s*[:=]?\s*(\d+)\s*[:.]?\s*(.*)$", re.I)
BP_RE = re.compile(r"\b(?:bp|blood\s*pressure)\s*[:=]?\s*(\d+)\s*/\s*(\d+)", re.I)
PAIR_RE = re.compile(r"([A-Za-z][A-Za-z0-9\s]*?)\s*[:=]\s*(-?\d+(?:\.\d+)?)")


def parse_scenario(text):
    """
    Text to a list of hours. A line with no hour marker continues the
    previous hour, so labs can sit under the vitals they belong with.
    """
    hours, current, hadm_id, warnings = [], None, None, []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if line.startswith("#"):
            m = re.search(r"(\d{6,})", line)
            if m and hadm_id is None:
                hadm_id = int(m.group(1))
            continue

        m = HOUR_RE.match(line)
        if m:
            current = {"hour": int(m.group(1)), "vitals": {}, "labs": {}}
            hours.append(current)
            line = m.group(2)
            if not line.strip():
                continue

        if current is None:
            continue

        bp = BP_RE.search(line)
        if bp:
            current["vitals"]["sbp"] = float(bp.group(1))
            current["vitals"]["dbp"] = float(bp.group(2))
            line = BP_RE.sub("", line)

        for key, value in PAIR_RE.findall(line):
            k = re.sub(r"[^a-z0-9]", "", key.lower())
            v = float(value)
            if k in VITAL_ALIASES:
                current["vitals"][VITAL_ALIASES[k]] = v
            elif k in LAB_ALIASES:
                current["labs"][LAB_ALIASES[k]] = v
            else:
                warnings.append(f"hour {current['hour']}: unknown '{key.strip()}'")

    hours.sort(key=lambda h: h["hour"])
    return {"hadm_id": hadm_id, "hours": hours, "warnings": warnings}


# ---------------------------------------------------------------
# State
#
# Which hour to write next, and a fingerprint of the file that produced
# it. Replacing the scenario resets the cursor — otherwise pasting a new
# case would silently resume halfway through it.
# ---------------------------------------------------------------
def _load_state():
    try:
        return json.loads(s3.get_object(Bucket=BUCKET,
                                        Key=STATE_KEY)["Body"].read())
    except Exception:
        return {}


def _save_state(state):
    s3.put_object(Bucket=BUCKET, Key=STATE_KEY,
                  Body=json.dumps(state, indent=2).encode())


def _write_hour(conn, hadm_id, row):
    """
    One hour of observations into Aurora.

    Everything downstream — rules, models, agents, the API — reads the
    vitals and labs tables. Writing here rather than holding the scenario
    in memory is what lets the rest of the system react without knowing a
    simulation is running.
    """
    ids = conn.run("""
        SELECT a.subject_id,
               (SELECT stay_id FROM icu_stays WHERE hadm_id = a.hadm_id
                 ORDER BY stay_rank LIMIT 1)
        FROM admissions a WHERE a.hadm_id = :h
    """, h=hadm_id)

    if not ids:
        raise ValueError(f"no admission {hadm_id}")

    subject_id, stay_id = ids[0][0], ids[0][1]

    if stay_id is None:
        stay_id = conn.run("""
            INSERT INTO icu_stays (stay_id, hadm_id, subject_id,
                                   first_careunit, intime, stay_rank)
            VALUES ((SELECT COALESCE(MAX(stay_id), 90000000) + 1
                       FROM icu_stays WHERE stay_id >= 90000000),
                    :h, :s, 'Emergency Department', now(), 1)
            RETURNING stay_id
        """, h=hadm_id, s=subject_id)[0][0]

    written = []
    hour = row["hour"]

    for code, value in row["vitals"].items():
        conn.run("""
            INSERT INTO vitals (stay_id, hadm_id, subject_id, vital_code,
                                label, valuenum, valueuom, charttime,
                                hours_since_admit)
            VALUES (:st, :h, :s, :c, :l, :v, :u, now(), :hr)
        """, st=stay_id, h=hadm_id, s=subject_id, c=code,
             l=code.upper(), v=value, u=VITAL_UNITS.get(code), hr=hour)
        written.append(f"{code}={value}")

    for label, value in row["labs"].items():
        seen = conn.run("SELECT 1 FROM labs WHERE hadm_id = :h "
                        "AND label = :l LIMIT 1", h=hadm_id, l=label)
        conn.run("""
            INSERT INTO labs (subject_id, hadm_id, label, valuenum, valueuom,
                              charttime, hours_since_admit, is_first_of_stay)
            VALUES (:s, :h, :l, :v, :u, now(), :hr, :f)
        """, s=subject_id, h=hadm_id, l=label, v=value,
             u=LAB_UNITS.get(label), hr=hour, f=not seen)
        written.append(f"{label}={value}")

    return written


def _clear_patient(conn, hadm_id):
    """Wipe observations so a replaced scenario starts clean."""
    for table in ("vitals", "labs", "alert_traces"):
        conn.run(f"DELETE FROM {table} WHERE hadm_id = :h", h=hadm_id)


def _shock_index(conn, hadm_id):
    """
    Latest shock index, computed in the database.

    The full rule engine is not available here — it imports too much for
    a console-pasted function. This is the one number worth reporting
    back so the invocation log shows the patient deteriorating.
    """
    r = conn.run("""
        SELECT
          (SELECT valuenum FROM vitals WHERE hadm_id = :h AND vital_code='hr'
            ORDER BY hours_since_admit DESC LIMIT 1),
          (SELECT valuenum FROM vitals WHERE hadm_id = :h AND vital_code='sbp'
            ORDER BY hours_since_admit DESC LIMIT 1)
    """, h=hadm_id)
    if r and r[0][0] and r[0][1]:
        return round(float(r[0][0]) / float(r[0][1]), 2)
    return None


def handler(event, context):
    """
    One tick. Read the scenario, write the next unwritten hour.
    """
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=SCENARIO_KEY)
        text = obj["Body"].read().decode()
        etag = obj["ETag"]
    except Exception as e:
        return {"statusCode": 404,
                "body": json.dumps({"error": f"no scenario file: {e}"})}

    parsed = parse_scenario(text)
    if not parsed["hours"]:
        return {"statusCode": 400,
                "body": json.dumps({"error": "no hours found",
                                    "warnings": parsed["warnings"]})}

    hadm_id = parsed["hadm_id"] or event.get("hadm_id")
    if not hadm_id:
        return {"statusCode": 400, "body": json.dumps({
            "error": "no hadm_id. Put '# patient 90000001' at the top "
                     "of the scenario file."})}

    state = _load_state()
    replaced = state.get("etag") != etag

    if replaced:
        # The file changed. Start the new scenario from its first hour
        # rather than resuming wherever the old one reached.
        state = {"etag": etag, "hadm_id": hadm_id, "index": 0,
                 "total": len(parsed["hours"])}

    idx = state.get("index", 0)

    if idx >= len(parsed["hours"]):
        return {"statusCode": 200, "body": json.dumps({
            "status": "complete", "hadm_id": hadm_id, "hours_written": idx,
            "note": "Replace the scenario file to start another."})}

    conn = _db()
    try:
        if replaced and event.get("reset", True):
            _clear_patient(conn, hadm_id)

        row = parsed["hours"][idx]
        written = _write_hour(conn, hadm_id, row)
        si = _shock_index(conn, hadm_id)

        state["index"] = idx + 1
        _save_state(state)

        return {"statusCode": 200, "body": json.dumps({
            "status": "written",
            "hadm_id": hadm_id,
            "hour": row["hour"],
            "progress": f"{idx + 1}/{len(parsed['hours'])}",
            "scenario_replaced": replaced,
            "written": written,
            "shock_index": si,
            "shock_index_note": (
                "above 1.3 suggests significant volume loss" if si and si >= 1.3
                else "above 0.9 warrants attention" if si and si >= 0.9
                else None),
            "warnings": parsed["warnings"][:5],
        }, default=str)}

    except Exception as e:
        return {"statusCode": 500, "body": json.dumps({"error": str(e)[:300]})}
    finally:
        try:
            conn.close()
        except Exception:
            pass