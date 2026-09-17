"""
GRUS — API

FastAPI over the agent graph, the rule engine, and Aurora.

Everything the frontend needs, shaped to the contract already sent to the
frontend developer. Run it and the OpenAPI docs are at /docs.

    uvicorn grus_api:app --reload --port 8000

Design notes worth knowing before reading further:

  as_of_hours is honoured on every patient endpoint. Omit it and you get
  the full record; pass 1 and you get the record as it stood one hour
  after arrival. This is what stops the system reading the answer key.

  Briefs are generated live, not precomputed. A real patient arrives
  without a cached anything, and a demo that pretends otherwise is
  demonstrating the wrong thing. Results are cached after the first call
  so dragging the timeline slider is not thirty model invocations.

  Registering a patient triggers the whole pipeline — ETL, chunking,
  embedding, rules, brief — in the background. The response returns
  immediately with an id to poll.
"""

import os
import json
import time
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any

import psycopg
from psycopg.rows import dict_row, tuple_row
from grus_config import DB
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field



HOST = DB.HOST
PWD = DB.PASSWORD

app = FastAPI(
    title="GRUS",
    description="Emergency-medicine decision support. Every claim carries "
                "the database row it came from. Decision support, not "
                "diagnosis.",
    version="1.0.0",
)

# The frontend runs on Vercel; during development it runs on localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten to the Vercel domain before submission
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------
# Database
# ---------------------------------------------------------------
def get_conn():
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus", user="grusadmin",
        password=PWD, sslmode="require",
        keepalives=1, keepalives_idle=30, connect_timeout=15,
        row_factory=dict_row,
    )
    conn.execute("SET search_path TO grus, public")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()

def get_conn_tuple():
    """
    Same connection, but tuple rows.

    grus_rules.py and grus_score_engine.py index results positionally
    (row[0], row[1]) rather than by column name. Any endpoint that calls
    evaluate() or ScoreEngine must use this, not get_conn — a dict-row
    connection makes positional indexing raise KeyError.
    """
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus", user="grusadmin",
        password=PWD, sslmode="require",
        keepalives=1, keepalives_idle=30, connect_timeout=15,
        row_factory=tuple_row,
    )
    conn.execute("SET search_path TO grus, public")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()

def _num(v):
    """Decimal and NUMERIC come back as Decimal; JSON cannot hold them."""
    if v is None:
        return None
    f = float(v)
    return int(f) if f == int(f) else round(f, 3)


# ---------------------------------------------------------------
# Response shapes
# ---------------------------------------------------------------
class PatientCard(BaseModel):
    hadm_id: int
    subject_id: int
    stay_id: Optional[int] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    cohort: Optional[str] = None
    arrival_unit: Optional[str] = None
    admission_type: Optional[str] = None
    hours_since_arrival: Optional[float] = None
    risk_level: str = "unknown"
    alert_count: int = 0
    unknown_count: int = 0
    headline: str = ""
    prior_admissions: int = 0


class NewAdmission(BaseModel):
    """A patient arriving now."""
    age: int = Field(..., ge=0, le=120)
    gender: str = Field(..., pattern="^[MF]$")
    admission_type: str = "EW EMER."
    arrival_unit: str = "Emergency Department"
    chief_complaint: Optional[str] = None
    subject_id: Optional[int] = Field(
        None, description="Existing patient id. Omit for a first "
                          "presentation — a new one is allocated.")
    simulated: bool = Field(
        True, description="Marks the record as not from MIMIC. Synthetic "
                          "records are labelled everywhere they appear.")


class NewLab(BaseModel):
    label: str
    value: float
    unit: Optional[str] = None
    hours_since_admit: float = 0.0
    flag: Optional[str] = None


class NewVital(BaseModel):
    vital_code: str
    value: float
    unit: Optional[str] = None
    hours_since_admit: float = 0.0


class NewNote(BaseModel):
    section: str
    text: str
    note_type: str = "discharge"


class ChatRequest(BaseModel):
    hadm_id: int
    message: str
    as_of_hours: Optional[float] = None
    trace_id: Optional[int] = None
    history: List[Dict[str, str]] = []


# ---------------------------------------------------------------
# Brief cache
#
# A brief takes several seconds to generate. Dragging the timeline
# slider must not mean thirty model invocations, so results are cached
# by (patient, cutoff). Registering or amending a record clears it.
# ---------------------------------------------------------------
_brief_cache: Dict[str, Any] = {}
_pipeline_status: Dict[int, Dict] = {}


def _cache_key(hadm_id, as_of):
    return f"{hadm_id}:{as_of}"


def _invalidate(hadm_id):
    for k in list(_brief_cache):
        if k.startswith(f"{hadm_id}:"):
            del _brief_cache[k]


# ---------------------------------------------------------------
# 1. Cohort board
# ---------------------------------------------------------------
@app.get("/patients", response_model=Dict[str, Any], tags=["patients"])
def list_patients(
    cohort: Optional[str] = None,
    search: Optional[str] = None,
    risk: Optional[str] = Query(None, pattern="^(critical|high|moderate|low|unknown)$"),
    limit: int = Query(300, le=500),
    conn=Depends(get_conn_tuple),
):
    """
    The cohort board.

    Risk level here comes from the rule engine's worst active alert, not
    from a model. It is cheap to compute and does not require generating
    a brief for all 300 patients on page load.
    """
    where, params = ["a.is_current = TRUE"], []
    if cohort:
        where.append("a.cohort = %s")
        params.append(cohort)
    if search:
        where.append("(CAST(a.hadm_id AS TEXT) LIKE %s "
                     "OR CAST(a.subject_id AS TEXT) LIKE %s)")
        params += [f"%{search}%", f"%{search}%"]
    params.append(limit)

    rows = conn.execute(f"""
        SELECT a.hadm_id, a.subject_id, a.cohort, a.arrival_unit,
               a.admission_type, a.hosp_days, a.hospital_expire_flag,
               p.anchor_age, p.gender,
               (SELECT COUNT(*) FROM admissions pa
                 WHERE pa.subject_id = a.subject_id AND pa.is_current = FALSE)
                 AS prior_admissions,
               (SELECT stay_id FROM icu_stays s
                 WHERE s.hadm_id = a.hadm_id ORDER BY stay_rank LIMIT 1)
                 AS stay_id,
               (SELECT string_agg(d.long_title, '; ')
                  FROM (SELECT long_title FROM diagnoses
                         WHERE hadm_id = a.hadm_id AND long_title IS NOT NULL
                         ORDER BY seq_num NULLS LAST LIMIT 2) d)
                 AS top_dx
        FROM admissions a
        JOIN patients p ON p.subject_id = a.subject_id
        WHERE {' AND '.join(where)}
        ORDER BY a.hadm_id
        LIMIT %s
    """, params).fetchall()

    from grus_rules import evaluate
    SEV_RANK = {"critical": 0, "warning": 1, "unknown": 2, "info": 3}
    LEVEL = {"critical": "critical", "warning": "high",
             "info": "low", "unknown": "unknown"}

    out = []
    for r in rows:
        try:
            alerts = evaluate(conn, r["hadm_id"], None, persist=False)
        except Exception:
            alerts = []

        worst = min((a.severity for a in alerts),
                    key=lambda s: SEV_RANK.get(s, 9), default="unknown")
        level = LEVEL.get(worst, "unknown")
        if risk and level != risk:
            continue

        age, sex = r["anchor_age"], r["gender"]
        dx = (r["top_dx"] or "").split(";")[0].strip()
        crit = alerts[0].title if alerts else "no alerts"

        out.append(PatientCard(
            hadm_id=r["hadm_id"], subject_id=r["subject_id"],
            stay_id=r["stay_id"], age=age, gender=sex,
            cohort=r["cohort"], arrival_unit=r["arrival_unit"],
            admission_type=r["admission_type"],
            hours_since_arrival=_num(r["hosp_days"]) * 24
                if r["hosp_days"] else None,
            risk_level=level,
            alert_count=sum(1 for a in alerts
                            if a.severity in ("critical", "warning")),
            unknown_count=sum(1 for a in alerts if a.severity == "unknown"),
            headline=f"{age}{sex}, {dx}. {crit}"[:140],
            prior_admissions=r["prior_admissions"],
        ))

    return {"count": len(out), "patients": [p.model_dump() for p in out]}


# ---------------------------------------------------------------
# 2. Brief — the hero screen
# ---------------------------------------------------------------
@app.get("/patients/{hadm_id}/brief", tags=["patients"])
def get_brief(
    hadm_id: int,
    as_of_hours: Optional[float] = Query(
        None, description="Cap the record at this many hours after "
                          "arrival. Omit for the full record."),
    refresh: bool = False,
    conn=Depends(get_conn_tuple),
):
    """
    Generate the brief through the Strands agent graph.

    Live, not precomputed: a real patient arrives without a cached
    anything. Cached after the first call so moving the timeline slider
    is not thirty model invocations.
    """
    key = _cache_key(hadm_id, as_of_hours)
    if not refresh and key in _brief_cache:
        cached = dict(_brief_cache[key])
        cached["cached"] = True
        return cached

    exists = conn.execute(
        "SELECT 1 FROM admissions WHERE hadm_id = %s", (hadm_id,)).fetchone()
    if not exists:
        raise HTTPException(404, {"code": "PATIENT_NOT_FOUND",
                                  "message": f"No admission {hadm_id}."})

    t0 = time.time()
    try:
        from grus_agents import generate_brief, check_citations, Session
        result = generate_brief(conn, hadm_id, as_of_hours, verbose=False)
        node = result.results.get("composer")
        text = str(node.result) if node else ""
        valid, invalid, pct = check_citations(text)
        agents_run = list(result.results.keys())
    except Exception as e:
        # A brief that cannot be generated must not leave a blank screen.
        # A doctor with unformatted data is better off than one with a
        # spinner, so fall back to the rule engine alone.
        from grus_rules import evaluate
        alerts = evaluate(conn, hadm_id, as_of_hours, persist=False)
        return {
            "hadm_id": hadm_id, "as_of_hours": as_of_hours,
            "degraded": True,
            "error": str(e)[:200],
            "message": "Agent graph unavailable. Rule engine output only.",
            "alerts": [{"severity": a.severity, "title": a.title,
                        "detail": a.body, "action": a.action,
                        "sources": a.sources} for a in alerts],
            "disclaimer": "Decision support, not diagnosis.",
        }

    payload = {
        "hadm_id": hadm_id,
        "as_of_hours": as_of_hours,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "generation_ms": int((time.time() - t0) * 1000),
        "brief": text,
        "agents_run": agents_run,
        "trust": {
            "citations_valid": len(valid),
            "citations_invalid": len(invalid),
            "traceable_pct": pct,
            "rejected": sorted(set(invalid))[:10],
        },
        "cached": False,
        "disclaimer": "Decision support, not diagnosis.",
    }
    _brief_cache[key] = payload
    return payload


# ---------------------------------------------------------------
# 3. Alerts — cheap, no model involved
# ---------------------------------------------------------------
@app.get("/patients/{hadm_id}/alerts", tags=["patients"])
def get_alerts(hadm_id: int, as_of_hours: Optional[float] = None,
               conn=Depends(get_conn_tuple)):
    """
    Rule engine output only. Milliseconds, no model call.

    Use this for the board and for anything that needs to be instant;
    use /brief when the doctor opens the patient.
    """
    from grus_rules import evaluate
    alerts = evaluate(conn, hadm_id, as_of_hours, persist=False)
    return {
        "hadm_id": hadm_id,
        "as_of_hours": as_of_hours,
        "alerts": [{"code": a.code, "severity": a.severity, "title": a.title,
                    "detail": a.body, "action": a.action,
                    "inputs": a.inputs, "sources": a.sources}
                   for a in alerts],
        "counts": {
            "critical": sum(1 for a in alerts if a.severity == "critical"),
            "warning": sum(1 for a in alerts if a.severity == "warning"),
            "unknown": sum(1 for a in alerts if a.severity == "unknown"),
            "info": sum(1 for a in alerts if a.severity == "info"),
        },
    }
@app.get("/patients/{hadm_id}/scores", tags=["patients"])
def list_patient_scores(hadm_id: int, presentation: Optional[str] = None,
                        as_of_hours: Optional[float] = None,
                        conn=Depends(get_conn_tuple)):
    """
    Which validated scores are worth running for this patient.

    Fifteen are encoded. Which apply depends on what the record holds —
    a patient with no white cell count cannot have SIRS computed.
    """
    from grus_score_engine import ScoreEngine
    from grus_scores import SCORES, list_scores
    eng = ScoreEngine(conn, hadm_id, as_of_hours)
    return {
        "hadm_id": hadm_id,
        "suggested": [{"key": k, "name": SCORES[k]["name"],
                       "purpose": SCORES[k]["purpose"]}
                      for k in eng.suggest(presentation)],
        "all_available": list_scores(),
    }


@app.get("/patients/{hadm_id}/scores/{score_name}", tags=["patients"])
def get_patient_score(hadm_id: int, score_name: str,
                      as_of_hours: Optional[float] = None,
                      conn=Depends(get_conn_tuple)):
    """
    Compute one score. The record fills what it can.

    Criteria the record cannot supply come back under
    components.missing, each with the question to ask. When anything is
    missing there is no total — render the questions, not a partial
    number.
    """
    from grus_score_engine import ScoreEngine
    eng = ScoreEngine(conn, hadm_id, as_of_hours)
    r = eng.compute(score_name)
    if r is None:
        raise HTTPException(404, {"code": "SCORE_NOT_FOUND",
                                  "message": f"No score '{score_name}'."})
    return r.to_dict()


@app.post("/patients/{hadm_id}/scores/{score_name}", tags=["patients"])
def compute_patient_score(hadm_id: int, score_name: str,
                          provided: Dict[str, Any],
                          as_of_hours: Optional[float] = None,
                          conn=Depends(get_conn_tuple)):
    """
    Compute a score with the clinician's answers filled in.

    Body is a map of criterion key to answer:
        {"leg_swelling": "no", "haemoptysis": "no"}

    The keys come from components.missing on the GET.
    """
    from grus_score_engine import ScoreEngine
    eng = ScoreEngine(conn, hadm_id, as_of_hours)
    r = eng.compute(score_name, provided)
    if r is None:
        raise HTTPException(404, {"code": "SCORE_NOT_FOUND",
                                  "message": f"No score '{score_name}'."})
    return r.to_dict()

@app.get("/patients/{hadm_id}/risk", tags=["patients"])
def get_risk(hadm_id: int, as_of_hours: Optional[float] = None,
             conn=Depends(get_conn_tuple)):
    """
    Model predictions from the SageMaker endpoints.

    Separate from /alerts on purpose. Rules fire on thresholds; models
    weigh everything together. Showing them apart lets a clinician see
    when they disagree, which is the interesting case.

    Returns available=false when there is too little data to score.
    Render that as unknown, not as low risk.
    """
    try:
        from grus_risk_score import score
        return score(conn, hadm_id, as_of_hours)
    except ImportError:
        return {"available": False,
                "reason": "Risk scoring module not deployed."}


# ---------------------------------------------------------------
# 4. Vitals — chart data
# ---------------------------------------------------------------
NORMAL_RANGE = {
    "hr": [60, 100], "sbp": [90, 140], "dbp": [60, 90], "map": [70, 100],
    "spo2": [95, 100], "rr": [12, 20], "temp_c": [36.1, 37.5],
}


@app.get("/patients/{hadm_id}/vitals", tags=["patients"])
def get_vitals(
    hadm_id: int,
    codes: Optional[str] = Query(None, description="Comma separated: hr,sbp,spo2"),
    as_of_hours: Optional[float] = None,
    conn=Depends(get_conn),
):
    wanted = [c.strip() for c in codes.split(",")] if codes else None
    where = ["hadm_id = %s", "valuenum IS NOT NULL", "vital_code IS NOT NULL"]
    params = [hadm_id]
    if wanted:
        where.append("vital_code = ANY(%s)")
        params.append(wanted)
    if as_of_hours is not None:
        where.append("hours_since_admit <= %s")
        params.append(as_of_hours)

    rows = conn.execute(f"""
        SELECT vital_code, label, valuenum, valueuom, hours_since_admit
        FROM vitals WHERE {' AND '.join(where)}
        ORDER BY vital_code, hours_since_admit
    """, params).fetchall()

    series: Dict[str, Dict] = {}
    for r in rows:
        s = series.setdefault(r["vital_code"], {
            "code": r["vital_code"], "label": r["label"],
            "unit": r["valueuom"],
            "normal_range": NORMAL_RANGE.get(r["vital_code"]),
            "points": [],
        })
        s["points"].append({"hours": _num(r["hours_since_admit"]),
                            "value": _num(r["valuenum"])})

    # Shock index is derived, so it is computed here rather than stored.
    hr = {p["hours"]: p["value"] for p in series.get("hr", {}).get("points", [])}
    sbp = {p["hours"]: p["value"] for p in series.get("sbp", {}).get("points", [])}
    shock = [{"hours": h, "value": round(hr[h] / sbp[h], 3)}
             for h in sorted(set(hr) & set(sbp)) if sbp[h]]

    derived = []
    if shock:
        derived.append({
            "code": "shock_index", "label": "Shock Index",
            "thresholds": {"concern": 0.9, "severe": 1.3},
            "points": shock,
        })

    return {
        "hadm_id": hadm_id,
        "sampling": "hourly",
        "sampling_note": "ICU charting is hourly, not continuous. Emergency "
                         "department vitals are often absent entirely.",
        "series": list(series.values()),
        "derived": derived,
    }


# ---------------------------------------------------------------
# 5. Source drawer
# ---------------------------------------------------------------
ALLOWED_TABLES = {"labs", "vitals", "medications", "diagnoses",
                  "procedures", "outputs", "note_chunks", "transfers"}
PK = {"labs": "lab_id", "vitals": "vital_id", "medications": "medication_id",
      "diagnoses": "diagnosis_id", "procedures": "procedure_id",
      "outputs": "output_id", "note_chunks": "chunk_id",
      "transfers": "transfer_id"}


@app.get("/sources/{table}/{row_id}", tags=["sources"])
def get_source(table: str, row_id: int, conn=Depends(get_conn)):
    """
    One row, exactly as stored.

    This is what makes a citation clickable. Table names are whitelisted
    — the path is user-supplied and must never reach a query unchecked.
    """
    if table not in ALLOWED_TABLES:
        raise HTTPException(400, {"code": "INVALID_PARAM",
                                  "message": f"Unknown table '{table}'."})

    row = conn.execute(
        f"SELECT * FROM {table} WHERE {PK[table]} = %s", (row_id,)).fetchone()
    if not row:
        raise HTTPException(404, {"code": "SOURCE_NOT_FOUND",
                                  "message": f"No {table} row {row_id}."})

    clean = {k: (_num(v) if hasattr(v, "as_tuple") else
                 v.isoformat() if hasattr(v, "isoformat") else v)
             for k, v in row.items()}

    return {
        "table": table, "id": row_id, "row": clean,
        "provenance": {
            "source_dataset": "MIMIC-IV v2.1"
                if not clean.get("simulated") else "simulated",
            "source_table": table,
        },
    }


# ---------------------------------------------------------------
# 6. Chat
# ---------------------------------------------------------------
@app.post("/chat", tags=["chat"])
def chat(req: ChatRequest, conn=Depends(get_conn_tuple)):
    """
    The doctor asks a question.

    The model answers from stored traces and tool calls, never from
    memory. When it cannot answer it says so rather than guessing — a
    plausible wrong answer about a patient is worse than no answer.
    """
    exists = conn.execute(
        "SELECT 1 FROM admissions WHERE hadm_id = %s", (req.hadm_id,)).fetchone()
    if not exists:
        raise HTTPException(404, {"code": "PATIENT_NOT_FOUND",
                                  "message": f"No admission {req.hadm_id}."})

    try:
        from grus_chat import answer
        return answer(conn, req.hadm_id, req.message,
                      as_of_hours=req.as_of_hours,
                      trace_id=req.trace_id, history=req.history)
    except ImportError as e:
        raise HTTPException(503, {"code": "CHAT_UNAVAILABLE",
       "message": f"Chat agent not deployed: {e}"})


@app.get("/patients/{hadm_id}/questions", tags=["chat"])
def get_suggested_questions(hadm_id: int,
                            as_of_hours: Optional[float] = None,
                            conn=Depends(get_conn_tuple)):
    """
    Questions worth asking about this patient, derived from what the
    rules found.

    A blank chat box is a poor interface for someone with ninety seconds.
    """
    try:
        from grus_chat import suggested_questions
        return {"hadm_id": hadm_id,
                "questions": suggested_questions(conn, hadm_id, as_of_hours)}
    except Exception as e:
        return {"hadm_id": hadm_id, "questions": [
            "What is missing from this record?",
            "What is the patient taking at home?",
            "Any history of bleeding?",
        ], "note": str(e)[:120]}


# ---------------------------------------------------------------
# 7. Registration — a patient arrives
# ---------------------------------------------------------------
@app.post("/admissions", status_code=202, tags=["ingest"])
def register_admission(adm: NewAdmission, bg: BackgroundTasks,
                       conn=Depends(get_conn)):
    """
    Register a patient and start the pipeline.

    Returns immediately with an id. The rule engine and brief generation
    run in the background, because a registration clerk should not wait
    on a language model.

    Poll /admissions/{hadm_id}/status for progress.
    """
    subject_id = adm.subject_id
    if subject_id is None:
        # 9-prefixed ids keep synthetic patients visibly apart from MIMIC's
        # Alias the expression. Without one Postgres names the column
        # unpredictably and the dict lookup fails.
        subject_id = conn.execute(
            "SELECT COALESCE(MAX(subject_id), 90000000) + 1 AS next_id "
            "FROM patients WHERE subject_id >= 90000000"
        ).fetchone()["next_id"]
        conn.execute("""
            INSERT INTO patients (subject_id, gender, anchor_age)
            VALUES (%s, %s, %s)
        """, (subject_id, adm.gender, adm.age))
    else:
        known = conn.execute("SELECT 1 FROM patients WHERE subject_id = %s",
                             (subject_id,)).fetchone()
        if not known:
            raise HTTPException(404, {"code": "PATIENT_NOT_FOUND",
                                      "message": f"No patient {subject_id}. "
                                                 f"Omit subject_id to create."})

    hadm_id = conn.execute(
        "SELECT COALESCE(MAX(hadm_id), 90000000) + 1 AS next_id "
        "FROM admissions WHERE hadm_id >= 90000000"
    ).fetchone()["next_id"]

    now = datetime.utcnow()
    conn.execute("""
        INSERT INTO admissions
            (hadm_id, subject_id, admittime, admission_type, arrival_unit,
             cohort, is_current)
        VALUES (%s, %s, %s, %s, %s, %s, TRUE)
    """, (hadm_id, subject_id, now, adm.admission_type, adm.arrival_unit,
          "unclassified"))

    conn.execute("""
        INSERT INTO transfers
            (hadm_id, subject_id, careunit, intime, hours_since_admit, seq_num)
        VALUES (%s, %s, %s, %s, 0, 1)
    """, (hadm_id, subject_id, adm.arrival_unit, now))

    if adm.chief_complaint:
        note_id = f"SIM-{hadm_id}-CC"
        conn.execute("""
            INSERT INTO notes (note_id, subject_id, hadm_id, note_type,
                               charttime, hours_since_admit, text)
            VALUES (%s, %s, %s, 'admission', %s, 0, %s)
        """, (note_id, subject_id, hadm_id, now, adm.chief_complaint))
        conn.execute("""
            INSERT INTO note_chunks (note_id, hadm_id, subject_id, note_type,
                                     section, chunk_index, charttime, text)
            VALUES (%s, %s, %s, 'admission', 'Chief Complaint', 0, %s, %s)
        """, (note_id, hadm_id, subject_id, now, adm.chief_complaint))

    conn.commit()

    _pipeline_status[hadm_id] = {"stage": "registered", "started": time.time()}
    bg.add_task(_run_pipeline, hadm_id)

    return {
        "hadm_id": hadm_id,
        "subject_id": subject_id,
        "status": "registered",
        "simulated": adm.simulated,
        "pipeline": f"/admissions/{hadm_id}/status",
        "note": "Rules and brief are generating. Poll the pipeline url.",
    }


def _run_pipeline(hadm_id: int):
    """
    Everything that happens when a patient arrives: embed any notes, run
    the rules, generate the brief.

    Runs out of band. Registration must not block on a model.
    """
    st = _pipeline_status.setdefault(hadm_id, {})
    conn = None
    try:
        conn = psycopg.connect(
            host=HOST, port=5432, dbname="grus", user="grusadmin",
                        password=PWD, sslmode="require", row_factory=tuple_row)
        conn.execute("SET search_path TO grus, public")
        conn.commit()

        st["stage"] = "embedding"
        try:
            from grus_embed import embed as embed_text
            rows = conn.execute("""
                SELECT chunk_id, text FROM note_chunks
                WHERE hadm_id = %s AND embedding IS NULL
            """, (hadm_id,)).fetchall()
            for r in rows:
                conn.execute(
                    "UPDATE note_chunks SET embedding = %s WHERE chunk_id = %s",
                    (str(embed_text(r[1])), r[0]))
            conn.commit()
            st["chunks_embedded"] = len(rows)
        except Exception as e:
            st["embedding_error"] = str(e)[:120]

        st["stage"] = "rules"
        from grus_rules import evaluate
        alerts = evaluate(conn, hadm_id, None, persist=True)
        st["alerts"] = len(alerts)

        st["stage"] = "brief"
        try:
            from grus_agents import generate_brief
            result = generate_brief(conn, hadm_id, None, verbose=False)
            node = result.results.get("composer")
            _brief_cache[_cache_key(hadm_id, None)] = {
                "hadm_id": hadm_id, "as_of_hours": None,
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "brief": str(node.result) if node else "",
                "cached": True,
                "disclaimer": "Decision support, not diagnosis.",
            }
        except Exception as e:
            st["brief_error"] = str(e)[:120]

        st["stage"] = "ready"
        st["elapsed_s"] = round(time.time() - st.get("started", time.time()), 1)

    except Exception as e:
        st["stage"] = "failed"
        st["error"] = str(e)[:200]
    finally:
        if conn:
            conn.close()


@app.get("/admissions/{hadm_id}/status", tags=["ingest"])
def pipeline_status(hadm_id: int):
    st = _pipeline_status.get(hadm_id)
    if not st:
        return {"hadm_id": hadm_id, "stage": "unknown",
                "note": "No pipeline run recorded for this admission."}
    return {"hadm_id": hadm_id, **st}


# ---------------------------------------------------------------
# 8. Adding results to an existing admission
# ---------------------------------------------------------------
@app.post("/admissions/{hadm_id}/labs", status_code=201, tags=["ingest"])
def add_lab(hadm_id: int, lab: NewLab, conn=Depends(get_conn)):
    """
    A result arrives. Rules re-evaluate on the next read.

    This is the seam a hospital's lab interface or a Lambda would write
    to. Nothing about the rest of the system knows or cares whether a
    value came from here or from the ETL.
    """
    subj = conn.execute("SELECT subject_id FROM admissions WHERE hadm_id = %s",
                        (hadm_id,)).fetchone()
    if not subj:
        raise HTTPException(404, {"code": "PATIENT_NOT_FOUND",
                                  "message": f"No admission {hadm_id}."})

    first = conn.execute("""
        SELECT 1 FROM labs WHERE hadm_id = %s AND label = %s LIMIT 1
    """, (hadm_id, lab.label)).fetchone() is None

    row = conn.execute("""
        INSERT INTO labs (subject_id, hadm_id, label, valuenum, valueuom,
                          charttime, hours_since_admit, flag, is_first_of_stay)
        VALUES (%s, %s, %s, %s, %s, now(), %s, %s, %s)
        RETURNING lab_id
    """, (subj["subject_id"], hadm_id, lab.label, lab.value, lab.unit,
          lab.hours_since_admit, lab.flag, first)).fetchone()
    conn.commit()
    _invalidate(hadm_id)

    from grus_rules import evaluate
    alerts = evaluate(conn, hadm_id, None, persist=False)
    new_critical = [a.title for a in alerts if a.severity == "critical"]

    return {"lab_id": row["lab_id"], "source": f"labs#{row['lab_id']}",
            "is_first_of_stay": first,
            "alerts_now": len(alerts),
            "critical": new_critical}


@app.post("/admissions/{hadm_id}/vitals", status_code=201, tags=["ingest"])
def add_vital(hadm_id: int, v: NewVital, conn=Depends(get_conn)):
    """
    A monitor reading. This endpoint is what a bedside device, a Lambda,
    or a Kinesis consumer would post to.
    """
    row = conn.execute("""
        SELECT a.subject_id,
               (SELECT stay_id FROM icu_stays WHERE hadm_id = a.hadm_id
                 ORDER BY stay_rank LIMIT 1) AS stay_id
        FROM admissions a WHERE a.hadm_id = %s
    """, (hadm_id,)).fetchone()
    if not row:
        raise HTTPException(404, {"code": "PATIENT_NOT_FOUND",
                                  "message": f"No admission {hadm_id}."})

    stay_id = row["stay_id"]
    if stay_id is None:
        stay_id = conn.execute("""
            INSERT INTO icu_stays (stay_id, hadm_id, subject_id,
                                   first_careunit, intime, stay_rank)
            VALUES ((SELECT COALESCE(MAX(stay_id), 90000000) + 1
                       FROM icu_stays WHERE stay_id >= 90000000),
                    %s, %s, 'Emergency Department', now(), 1)
            RETURNING stay_id
        """, (hadm_id, row["subject_id"])).fetchone()["stay_id"]

    ins = conn.execute("""
        INSERT INTO vitals (stay_id, hadm_id, subject_id, vital_code, label,
                            valuenum, valueuom, charttime, hours_since_admit)
        VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s)
        RETURNING vital_id
    """, (stay_id, hadm_id, row["subject_id"], v.vital_code,
          v.vital_code.upper(), v.value, v.unit,
          v.hours_since_admit)).fetchone()
    conn.commit()
    _invalidate(hadm_id)

    from grus_rules import evaluate
    alerts = evaluate(conn, hadm_id, None, persist=False)
    return {"vital_id": ins["vital_id"], "source": f"vitals#{ins['vital_id']}",
            "alerts_now": len(alerts),
            "critical": [a.title for a in alerts if a.severity == "critical"]}


@app.post("/admissions/{hadm_id}/notes", status_code=201, tags=["ingest"])
def add_note(hadm_id: int, note: NewNote, bg: BackgroundTasks,
             conn=Depends(get_conn)):
    """
    A clinical note. Split by section, chunked, and embedded.

    Paste a whole discharge summary and it splits on clinical headings.
    That boundary matters: a fixed-size splitter cuts a medication list
    in half, leaving 'Warfarin 5mg' in one chunk and 'daily' in another,
    and neither retrieves when someone searches for the anticoagulant.

    The trauma demo patient's anticoagulant appears only in a note —
    this is the path that makes such a fact findable.
    """
    subj = conn.execute("SELECT subject_id FROM admissions WHERE hadm_id = %s",
                        (hadm_id,)).fetchone()
    if not subj:
        raise HTTPException(404, {"code": "PATIENT_NOT_FOUND",
                                  "message": f"No admission {hadm_id}."})

    from grus_note_split import split_note, summarise_split
    chunks = split_note(note.text, default_section=note.section)
    if not chunks:
        raise HTTPException(400, {"code": "INVALID_PARAM",
                                  "message": "Note is empty."})

    note_id = f"SIM-{hadm_id}-{int(time.time())}"
    conn.execute("""
        INSERT INTO notes (note_id, subject_id, hadm_id, note_type,
                           charttime, hours_since_admit, text)
        VALUES (%s, %s, %s, %s, now(), 0, %s)
    """, (note_id, subj["subject_id"], hadm_id, note.note_type, note.text))

    created = []
    for i, (section, body) in enumerate(chunks):
        row = conn.execute("""
            INSERT INTO note_chunks (note_id, hadm_id, subject_id, note_type,
                                     section, chunk_index, charttime, text)
            VALUES (%s, %s, %s, %s, %s, %s, now(), %s)
            RETURNING chunk_id
        """, (note_id, hadm_id, subj["subject_id"], note.note_type,
              section, i, body)).fetchone()
        created.append({"chunk_id": row["chunk_id"], "section": section,
                        "source": f"note_chunks#{row['chunk_id']}",
                        "chars": len(body)})

    conn.commit()
    _invalidate(hadm_id)

    for c in created:
        bg.add_task(_embed_chunk, c["chunk_id"])

    return {"note_id": note_id, "chunks": created,
            "summary": summarise_split(chunks),
            "embedding": "queued"}


def _embed_chunk(chunk_id: int):
    try:
        from grus_embed import embed as embed_text
        conn = psycopg.connect(
            host=HOST, port=5432, dbname="grus", user="grusadmin",
            password=PWD, sslmode="require", row_factory=tuple_row)
        conn.execute("SET search_path TO grus, public")
        r = conn.execute("SELECT text FROM note_chunks WHERE chunk_id = %s",
                         (chunk_id,)).fetchone()
        conn.execute("UPDATE note_chunks SET embedding = %s WHERE chunk_id = %s",
                     (str(embed_text(r["text"])), chunk_id))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------
# 9. History
# ---------------------------------------------------------------
@app.get("/patients/{subject_id}/history", tags=["patients"])
def get_history(subject_id: int, conn=Depends(get_conn)):
    """
    Everything from previous visits.

    'Fifth presentation with heart failure' is the kind of pattern nobody
    looks up by hand, and it is the reason prior admissions are loaded at
    all.
    """
    admissions = conn.execute("""
        SELECT hadm_id, admittime, admission_type, hosp_days,
               hospital_expire_flag, is_current
        FROM admissions WHERE subject_id = %s ORDER BY admittime DESC
    """, (subject_id,)).fetchall()

    if not admissions:
        raise HTTPException(404, {"code": "PATIENT_NOT_FOUND",
                                  "message": f"No patient {subject_id}."})

    recurring = conn.execute("""
        SELECT d.long_title, COUNT(DISTINCT d.hadm_id) AS visits,
               MIN(d.diagnosis_id) AS source_id
        FROM diagnoses d
        JOIN admissions a ON a.hadm_id = d.hadm_id AND a.is_current = FALSE
        WHERE d.subject_id = %s AND d.long_title IS NOT NULL
        GROUP BY d.long_title HAVING COUNT(DISTINCT d.hadm_id) >= 2
        ORDER BY 2 DESC LIMIT 10
    """, (subject_id,)).fetchall()

    out = []
    for a in admissions:
        dx = conn.execute("""
            SELECT diagnosis_id, long_title FROM diagnoses
            WHERE hadm_id = %s ORDER BY seq_num NULLS LAST LIMIT 3
        """, (a["hadm_id"],)).fetchall()
        out.append({
            "hadm_id": a["hadm_id"],
            "is_current": a["is_current"],
            "admission_type": a["admission_type"],
            "length_of_stay_days": _num(a["hosp_days"]),
            "diagnoses": [{"title": d["long_title"],
                           "source": f"diagnoses#{d['diagnosis_id']}"}
                          for d in dx if d["long_title"]],
        })

    prior = sum(1 for a in admissions if not a["is_current"])
    return {
        "subject_id": subject_id,
        "prior_admissions": prior,
        "admissions": out,
        "recurring_conditions": [
            {"condition": r["long_title"], "visits": r["visits"],
             "source": f"diagnoses#{r['source_id']}"} for r in recurring],
        "note": None if prior else
                "First presentation at this facility. No baseline vitals, "
                "no home medication list, no allergy history. Records may "
                "exist elsewhere.",
    }


# ---------------------------------------------------------------
# 10. Trust panel
# ---------------------------------------------------------------
@app.get("/patients/{hadm_id}/trust", tags=["patients"])
def get_trust(hadm_id: int, as_of_hours: Optional[float] = None):
    """
    Citation counts from the last generated brief.

    This is the number that goes on screen: not a promise that nothing is
    fabricated, but a count of how much was checked.
    """
    cached = _brief_cache.get(_cache_key(hadm_id, as_of_hours))
    if not cached or "trust" not in cached:
        return {"hadm_id": hadm_id, "available": False,
                "note": "Generate a brief first."}
    return {"hadm_id": hadm_id, "available": True, **cached["trust"]}


# ---------------------------------------------------------------
# 11. Governance
# ---------------------------------------------------------------
@app.get("/governance", tags=["governance"])
def governance():
    """
    Model registry state.

    Reads the local training report and, when reachable, the SageMaker
    Model Registry. Models registered as PendingManualApproval are shown
    as such — a model reaches a clinician when a person approves it, not
    when a script finishes.
    """
    out = {"models": [], "rejected": []}

    try:
        with open("C:/Users/Hp/OneDrive/Desktop/Grus/model/models/report.json") as f:
            report = json.load(f)
        for label, info in report["labels"].items():
            m, fair = info["metrics"], info["fairness"]
            entry = {
                "name": label,
                "auc": round(m["auc"], 3),
                "average_precision": round(m["average_precision"], 3),
                "precision": round(m["precision"], 3),
                "recall": round(m["recall"], 3),
                "threshold": round(m["threshold"], 3),
                "threshold_policy": m.get("threshold_policy"),
                "calibration_error": round(m.get("calibration_error") or 0, 4),
                "alert_rate": round(m["alert_rate"], 4),
                "positive_rate": round(m["positive_rate"], 4),
                "subgroup_auc_gap": fair["max_gap"],
                "subgroups": {k: v for k, v in fair.items() if k != "max_gap"},
                "top_features": info["top_features"][:5],
            }
            if info["gate_failures"]:
                entry["rejected_because"] = info["gate_failures"]
                out["rejected"].append(entry)
            else:
                entry["status"] = "shipped"
                out["models"].append(entry)
    except FileNotFoundError:
        out["note"] = "No training report found."

    try:
        import boto3
        sm = boto3.client("sagemaker", region_name="ap-south-1")
        pkgs = sm.list_model_packages(
            ModelPackageGroupName="grus-risk-models",
            SortBy="CreationTime", SortOrder="Descending",
        )["ModelPackageSummaryList"]
        out["registry"] = [{"version": p["ModelPackageVersion"],
                            "status": p["ModelApprovalStatus"]}
                           for p in pkgs[:10]]
    except Exception as e:
        out["registry_error"] = str(e)[:120]

    out["policy"] = ("Models are registered pending manual approval. "
                     "Seven labels were trained; two were too rare to learn, "
                     "one restated a current value rather than predicting, "
                     "and one alerted on 61% of hours. Three shipped.")
    return out


# ---------------------------------------------------------------
# Health
# ---------------------------------------------------------------
@app.get("/health", tags=["system"])
def health(conn=Depends(get_conn)):
    checks = {}
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM admissions WHERE is_current").fetchone()
        checks["database"] = {"ok": True, "current_admissions": n["n"]}
    except Exception as e:
        checks["database"] = {"ok": False, "error": str(e)[:120]}

    try:
        import boto3
        boto3.client("bedrock-runtime", region_name="ap-south-1")
        checks["bedrock"] = {"ok": True}
    except Exception as e:
        checks["bedrock"] = {"ok": False, "error": str(e)[:120]}

    checks["brief_cache"] = {"entries": len(_brief_cache)}
    return {"status": "ok" if all(v.get("ok", True) for v in checks.values())
                      else "degraded", "checks": checks}


@app.get("/", tags=["system"])
def root():
    return {
        "service": "GRUS",
        "docs": "/docs",
        "disclaimer": "Decision support, not diagnosis.",
    }
