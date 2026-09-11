"""
GRUS — Scenario replay

Drives a simulated patient hour by hour so the brief updates as their
state changes.

THE VITALS IN THIS FILE ARE SYNTHETIC. They were written to be
clinically coherent, not sampled from a real admission. Every patient
created here carries simulated=True, a 9-prefixed id, and a SIMULATED
badge wherever they appear. The 301 MIMIC patients are real and are
never mixed with these.

Two scenarios, both starting from the same patient record, so a demo can
show the same system reaching different conclusions from different
physiology.

    python grus_scenario.py create haemorrhage
    python grus_scenario.py run <hadm_id>          replay in real time
    python grus_scenario.py step <hadm_id>         advance one hour
"""

import os
import sys
import json
import time
import psycopg
from psycopg.rows import dict_row
from grus_config import DB



HOST = DB.HOST
PWD = DB.PASSWORD


# ---------------------------------------------------------------
# Scenarios
#
# Written to follow the physiology rather than to look dramatic:
#
#   In haemorrhage the heart rate rises BEFORE the pressure falls. A
#   young patient compensates for a long time and then decompensates
#   quickly, which is exactly what makes it dangerous. Lactate follows
#   perfusion, so it lags the pulse.
#
#   In cardiac failure the pressure falls with a heart rate that cannot
#   compensate, and the lungs fill — so saturation falls early while
#   lactate rises late.
# ---------------------------------------------------------------

HAEMORRHAGE = {
    "name": "Post-traumatic haemorrhage",
    "description": "34M, road traffic collision, on warfarin. Compensates "
                   "for four hours, then decompensates.",
    "patient": {"age": 34, "gender": "M", "cohort": "trauma",
                "chief_complaint": "Road traffic collision, restrained "
                                   "driver. Alert on arrival. Reports "
                                   "abdominal and left chest pain."},
    "history_note": {
        "section": "Medications on Admission",
        "text": "Warfarin 5mg daily (mechanical mitral valve, replaced "
                "2149)\nBisoprolol 2.5mg daily\nAtorvastatin 20mg nocte",
    },
    "hours": [
        # hour, vitals, labs arriving that hour
        {"hour": 0,  "hr": 96,  "sbp": 124, "dbp": 78, "spo2": 98, "rr": 18,
         "temp_c": 36.6, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6,
         "labs": {"INR(PT)": 3.8, "Hemoglobin": 13.1, "Hematocrit": 39.2,
                  "Platelet Count": 244, "Creatinine": 0.9, "Lactate": 1.8}},

        {"hour": 1,  "hr": 104, "sbp": 122, "dbp": 76, "spo2": 97, "rr": 20,
         "temp_c": 36.5, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6},

        # Pulse climbing, pressure held. This is compensation, and it is
        # the window where a bleed is easiest to miss.
        {"hour": 2,  "hr": 112, "sbp": 118, "dbp": 72, "spo2": 97, "rr": 22,
         "temp_c": 36.4, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6,
         "labs": {"Hemoglobin": 11.4, "Hematocrit": 34.1, "Lactate": 2.4}},

        {"hour": 3,  "hr": 121, "sbp": 112, "dbp": 68, "spo2": 96, "rr": 24,
         "temp_c": 36.3, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6},

        # Pressure starting to give. Shock index crosses 1.0 here.
        {"hour": 4,  "hr": 128, "sbp": 104, "dbp": 62, "spo2": 95, "rr": 26,
         "temp_c": 36.1, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6,
         "labs": {"Hemoglobin": 9.8, "Hematocrit": 29.4, "Lactate": 3.6,
                  "Platelet Count": 198}},

        # Decompensation. Fast, as it is in the young.
        {"hour": 5,  "hr": 138, "sbp": 92,  "dbp": 54, "spo2": 93, "rr": 28,
         "temp_c": 35.9, "gcs_eye": 4, "gcs_verbal": 4, "gcs_motor": 6,
         "labs": {"Hemoglobin": 8.4, "Hematocrit": 25.2, "Lactate": 5.1}},

        {"hour": 6,  "hr": 142, "sbp": 84,  "dbp": 48, "spo2": 92, "rr": 30,
         "temp_c": 35.7, "gcs_eye": 3, "gcs_verbal": 4, "gcs_motor": 5,
         "labs": {"Hemoglobin": 7.6, "Hematocrit": 22.8, "Lactate": 6.4,
                  "Creatinine": 1.3}},

        # Transfused and reversed. Recovery is slower than the fall.
        {"hour": 7,  "hr": 128, "sbp": 96,  "dbp": 58, "spo2": 95, "rr": 26,
         "temp_c": 36.0, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6,
         "labs": {"Hemoglobin": 9.1, "Hematocrit": 27.3, "INR(PT)": 1.6,
                  "Lactate": 4.2}},

        {"hour": 8,  "hr": 112, "sbp": 108, "dbp": 66, "spo2": 96, "rr": 22,
         "temp_c": 36.2, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6,
         "labs": {"Hemoglobin": 9.6, "Lactate": 2.8}},
    ],
}


CARDIAC = {
    "name": "Cardiogenic decompensation",
    "description": "71F, chest pain, known heart failure. Pressure falls "
                   "with a rate that cannot compensate; lungs fill early.",
    "patient": {"age": 71, "gender": "F", "cohort": "cardiac",
                "chief_complaint": "Central chest pain since morning, "
                                   "worse on exertion. Increasing "
                                   "breathlessness over two days."},
    "history_note": {
        "section": "Medications on Admission",
        "text": "Apixaban 5mg twice daily (atrial fibrillation)\n"
                "Furosemide 40mg daily\nRamipril 5mg daily\n"
                "Bisoprolol 5mg daily\nAtorvastatin 40mg nocte",
    },
    "hours": [
        {"hour": 0,  "hr": 88,  "sbp": 138, "dbp": 82, "spo2": 94, "rr": 20,
         "temp_c": 36.7, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6,
         "labs": {"Troponin T": 0.08, "INR(PT)": 1.2, "Hemoglobin": 12.4,
                  "Creatinine": 1.2, "Potassium": 4.4, "Lactate": 1.6,
                  "Urea Nitrogen": 28}},

        # Saturation drops early — the lungs fill before the pressure goes.
        {"hour": 1,  "hr": 92,  "sbp": 132, "dbp": 78, "spo2": 92, "rr": 24,
         "temp_c": 36.6, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6,
         "labs": {"Troponin T": 0.34}},

        {"hour": 2,  "hr": 98,  "sbp": 124, "dbp": 74, "spo2": 90, "rr": 26,
         "temp_c": 36.5, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6,
         "labs": {"Troponin T": 1.12, "Lactate": 2.2}},

        # A beta-blocked heart cannot raise its rate much. Pressure falls
        # with only a modest tachycardia, which is why shock index reads
        # lower here than the patient's condition warrants.
        {"hour": 3,  "hr": 104, "sbp": 112, "dbp": 68, "spo2": 88, "rr": 28,
         "temp_c": 36.3, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6,
         "labs": {"Troponin T": 2.86, "Creatinine": 1.5, "Lactate": 3.4,
                  "Potassium": 5.1}},

        {"hour": 4,  "hr": 110, "sbp": 102, "dbp": 62, "spo2": 86, "rr": 30,
         "temp_c": 36.1, "gcs_eye": 4, "gcs_verbal": 4, "gcs_motor": 6,
         "labs": {"Lactate": 4.6, "Creatinine": 1.8, "Potassium": 5.6,
                  "Urea Nitrogen": 41}},

        {"hour": 5,  "hr": 116, "sbp": 94,  "dbp": 56, "spo2": 84, "rr": 32,
         "temp_c": 35.9, "gcs_eye": 3, "gcs_verbal": 4, "gcs_motor": 5,
         "labs": {"Lactate": 6.2, "Creatinine": 2.1, "Potassium": 6.0}},

        # Supported. Kidneys lag behind the pressure recovering.
        {"hour": 6,  "hr": 108, "sbp": 106, "dbp": 64, "spo2": 91, "rr": 26,
         "temp_c": 36.1, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6,
         "labs": {"Lactate": 4.1, "Potassium": 5.2}},

        {"hour": 7,  "hr": 98,  "sbp": 118, "dbp": 70, "spo2": 94, "rr": 22,
         "temp_c": 36.4, "gcs_eye": 4, "gcs_verbal": 5, "gcs_motor": 6,
         "labs": {"Lactate": 2.4, "Creatinine": 2.0, "Potassium": 4.8}},
    ],
}


SCENARIOS = {"haemorrhage": HAEMORRHAGE, "cardiac": CARDIAC}

VITAL_UNITS = {"hr": "bpm", "sbp": "mmHg", "dbp": "mmHg", "spo2": "%",
               "rr": "insp/min", "temp_c": "C", "gcs_eye": None,
               "gcs_verbal": None, "gcs_motor": None}

LAB_UNITS = {"INR(PT)": None, "Hemoglobin": "g/dL", "Hematocrit": "%",
             "Platelet Count": "K/uL", "Creatinine": "mg/dL",
             "Lactate": "mmol/L", "Troponin T": "ng/mL",
             "Potassium": "mEq/L", "Urea Nitrogen": "mg/dL"}


def connect():
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus", user="grusadmin",
        password=PWD, sslmode="require",
        keepalives=1, keepalives_idle=30, row_factory=dict_row)
    conn.execute("SET search_path TO grus, public")
    return conn


def create(conn, key):
    """
    Create the patient record and their history. No vitals yet — those
    arrive hour by hour, which is the point.
    """
    sc = SCENARIOS[key]
    p = sc["patient"]

    subject_id = conn.execute(
        "SELECT COALESCE(MAX(subject_id), 90000000) + 1 AS n FROM patients "
        "WHERE subject_id >= 90000000").fetchone()["n"]
    hadm_id = conn.execute(
        "SELECT COALESCE(MAX(hadm_id), 90000000) + 1 AS n FROM admissions "
        "WHERE hadm_id >= 90000000").fetchone()["n"]
    stay_id = conn.execute(
        "SELECT COALESCE(MAX(stay_id), 90000000) + 1 AS n FROM icu_stays "
        "WHERE stay_id >= 90000000").fetchone()["n"]

    conn.execute("""
        INSERT INTO patients (subject_id, gender, anchor_age)
        VALUES (%s, %s, %s)
    """, (subject_id, p["gender"], p["age"]))

    conn.execute("""
        INSERT INTO admissions (hadm_id, subject_id, admittime,
                                admission_type, admission_location,
                                arrival_unit, cohort, is_current)
        VALUES (%s, %s, now(), 'EW EMER.', 'EMERGENCY ROOM',
                'Emergency Department', %s, TRUE)
    """, (hadm_id, subject_id, p["cohort"]))

    conn.execute("""
        INSERT INTO icu_stays (stay_id, hadm_id, subject_id,
                               first_careunit, intime, stay_rank)
        VALUES (%s, %s, %s, 'Emergency Department', now(), 1)
    """, (stay_id, hadm_id, subject_id))

    conn.execute("""
        INSERT INTO transfers (hadm_id, subject_id, careunit, intime,
                               hours_since_admit, seq_num)
        VALUES (%s, %s, 'Emergency Department', now(), 0, 1)
    """, (hadm_id, subject_id))

    # The medication history. This is the fact that makes the demo work:
    # the anticoagulant exists only here, in a note, exactly as it did
    # for the real trauma patient.
    note_id = f"SIM-{hadm_id}-HX"
    hx = sc["history_note"]
    conn.execute("""
        INSERT INTO notes (note_id, subject_id, hadm_id, note_type,
                           charttime, hours_since_admit, text)
        VALUES (%s, %s, %s, 'admission', now(), 0, %s)
    """, (note_id, subject_id, hadm_id, hx["text"]))
    conn.execute("""
        INSERT INTO note_chunks (note_id, hadm_id, subject_id, note_type,
                                 section, chunk_index, charttime, text)
        VALUES (%s, %s, %s, 'admission', %s, 0, now(), %s)
    """, (note_id, hadm_id, subject_id, hx["section"], hx["text"]))

    cc_id = f"SIM-{hadm_id}-CC"
    conn.execute("""
        INSERT INTO notes (note_id, subject_id, hadm_id, note_type,
                           charttime, hours_since_admit, text)
        VALUES (%s, %s, %s, 'admission', now(), 0, %s)
    """, (cc_id, subject_id, hadm_id, p["chief_complaint"]))
    conn.execute("""
        INSERT INTO note_chunks (note_id, hadm_id, subject_id, note_type,
                                 section, chunk_index, charttime, text)
        VALUES (%s, %s, %s, 'admission', 'Chief Complaint', 0, now(), %s)
    """, (cc_id, hadm_id, subject_id, p["chief_complaint"]))

    conn.commit()

    try:
        from grus_embed import embed
        for cid in conn.execute("""
            SELECT chunk_id FROM note_chunks
            WHERE hadm_id = %s AND embedding IS NULL
        """, (hadm_id,)).fetchall():
            txt = conn.execute("SELECT text FROM note_chunks WHERE chunk_id = %s",
                               (cid["chunk_id"],)).fetchone()["text"]
            conn.execute("UPDATE note_chunks SET embedding = %s WHERE chunk_id = %s",
                         (str(embed(txt)), cid["chunk_id"]))
        conn.commit()
        embedded = True
    except Exception as e:
        print(f"  embedding skipped: {str(e)[:80]}")
        embedded = False

    print(f"\n{sc['name']}")
    print(f"  {sc['description']}")
    print(f"  hadm_id   {hadm_id}   <- use this")
    print(f"  subject   {subject_id}")
    print(f"  notes embedded: {embedded}")
    print(f"  {len(sc['hours'])} hours of vitals waiting")
    print(f"\n  advance:  python grus_scenario.py step {hadm_id}")
    print(f"  replay:   python grus_scenario.py run {hadm_id}")

    _save_state(hadm_id, key, 0)
    return hadm_id


STATE_FILE = "C:/Users/Hp/OneDrive/Desktop/Grus/scenario_state.json"


def _load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_state(hadm_id, key, next_hour):
    st = _load_state()
    st[str(hadm_id)] = {"scenario": key, "next_hour": next_hour}
    with open(STATE_FILE, "w") as f:
        json.dump(st, f, indent=2)


def step(conn, hadm_id):
    """
    Write one hour of vitals and labs, then report what changed.

    This is what a Lambda triggered by EventBridge would do: read the
    next row, write it, let the rules re-evaluate.
    """
    st = _load_state().get(str(hadm_id))
    if not st:
        print(f"no scenario running for {hadm_id}")
        return None

    sc = SCENARIOS[st["scenario"]]
    idx = st["next_hour"]
    if idx >= len(sc["hours"]):
        print(f"scenario complete ({len(sc['hours'])} hours written)")
        return None

    row = sc["hours"][idx]
    hour = row["hour"]

    ids = conn.execute("""
        SELECT a.subject_id,
               (SELECT stay_id FROM icu_stays WHERE hadm_id = a.hadm_id
                 ORDER BY stay_rank LIMIT 1) AS stay_id
        FROM admissions a WHERE a.hadm_id = %s
    """, (hadm_id,)).fetchone()

    written = []
    for code, unit in VITAL_UNITS.items():
        if code not in row:
            continue
        conn.execute("""
            INSERT INTO vitals (stay_id, hadm_id, subject_id, vital_code,
                                label, valuenum, valueuom, charttime,
                                hours_since_admit)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s)
        """, (ids["stay_id"], hadm_id, ids["subject_id"], code,
              code.upper(), row[code], unit, hour))
        written.append(f"{code}={row[code]}")

    for label, value in (row.get("labs") or {}).items():
        first = conn.execute("""
            SELECT 1 FROM labs WHERE hadm_id = %s AND label = %s LIMIT 1
        """, (hadm_id, label)).fetchone() is None
        conn.execute("""
            INSERT INTO labs (subject_id, hadm_id, label, valuenum, valueuom,
                              charttime, hours_since_admit, is_first_of_stay)
            VALUES (%s, %s, %s, %s, %s, now(), %s, %s)
        """, (ids["subject_id"], hadm_id, label, value,
              LAB_UNITS.get(label), hour, first))
        written.append(f"{label}={value}")

    conn.commit()
    _save_state(hadm_id, st["scenario"], idx + 1)

    from grus_rules import evaluate
    alerts = evaluate(conn, hadm_id, None, persist=True)

    si = None
    if "hr" in row and "sbp" in row:
        si = round(row["hr"] / row["sbp"], 2)

    print(f"\nhour {hour}")
    print(f"  written: {', '.join(written[:6])}"
          + (f" (+{len(written)-6} more)" if len(written) > 6 else ""))
    if si:
        flag = " ELEVATED" if si >= 0.9 else ""
        print(f"  shock index {si}{flag}")

    crit = [a for a in alerts if a.severity == "critical"]
    warn = [a for a in alerts if a.severity == "warning"]
    print(f"  alerts: {len(crit)} critical, {len(warn)} warning")
    for a in crit:
        print(f"    [!!] {a.title}")
    for a in warn[:2]:
        print(f"    [! ] {a.title}")

    return {"hour": hour, "alerts": len(alerts), "shock_index": si}


def run(conn, hadm_id, delay=8):
    """
    Replay the whole scenario. Watch the alerts appear as the patient
    deteriorates and clear as they recover.
    """
    st = _load_state().get(str(hadm_id))
    if not st:
        print(f"no scenario for {hadm_id}")
        return

    total = len(SCENARIOS[st["scenario"]]["hours"])
    print(f"replaying {total - st['next_hour']} hours, {delay}s apart")
    print("(this is the loop a Lambda would run on an EventBridge schedule)")

    while True:
        r = step(conn, hadm_id)
        if r is None:
            break
        time.sleep(delay)

    print("\nscenario complete")
    print(f"  brief:  GET /patients/{hadm_id}/brief")
    print(f"  replay: GET /patients/{hadm_id}/brief?as_of_hours=4")


def reset(conn, hadm_id):
    """Clear the vitals and labs so a scenario can be replayed."""
    conn.execute("DELETE FROM vitals WHERE hadm_id = %s", (hadm_id,))
    conn.execute("DELETE FROM labs WHERE hadm_id = %s", (hadm_id,))
    conn.execute("DELETE FROM alert_traces WHERE hadm_id = %s", (hadm_id,))
    conn.commit()

    st = _load_state().get(str(hadm_id))
    if st:
        _save_state(hadm_id, st["scenario"], 0)
    print(f"reset {hadm_id}")


def status(conn):
    st = _load_state()
    if not st:
        print("no scenarios created")
        return
    for hadm_id, s in st.items():
        sc = SCENARIOS[s["scenario"]]
        print(f"  {hadm_id}  {sc['name']}  "
              f"hour {s['next_hour']}/{len(sc['hours'])}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    arg = sys.argv[2] if len(sys.argv) > 2 else None

    conn = connect()
    try:
        if cmd == "create":
            if arg not in SCENARIOS:
                print(f"scenarios: {list(SCENARIOS)}")
            else:
                create(conn, arg)
        elif cmd == "step":
            step(conn, int(arg))
        elif cmd == "run":
            run(conn, int(arg))
        elif cmd == "reset":
            reset(conn, int(arg))
        else:
            status(conn)
    finally:
        conn.close()