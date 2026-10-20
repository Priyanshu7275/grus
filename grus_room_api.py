"""
GRUS Room — website API for the room record and the main desk

Everything a doctor says to Alexa (log, ask the desk, remind me, scores,
handover) is written to the room record by the MCP tools. These endpoints
let the website SHOW it live and let the desk ACT on it:

  Room view (patient page)
    GET  /room/{hadm_id}/timeline      the black box, newest last
    GET  /room/{hadm_id}/verify        is the hash chain intact?
    GET  /room/{hadm_id}/reminders     pending and due reminders
    POST /room/{hadm_id}/log           log an entry from the screen

  Desk view
    GET  /desk/requests                open requests, all patients, stat first
    POST /desk/requests/{request_id}   acknowledge / done / cancel
    POST /desk/reminders/check         reminders now due (marks them fired)

Writes go through grus_room, so the website and Alexa produce the same
hash-chained entries. Times are real clock times (Asia/Kolkata by
default), because GRUS Room writes these rows itself.

Mount in grus_api.py, after app is created:
    from grus_room_api import router as room_router
    app.include_router(room_router)
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import grus_room as R
from grus_mcp import _clock, _event_line, _short_id

router = APIRouter(tags=["room"])


def _conn():
    from psycopg.rows import tuple_row
    from grus_config import connect
    conn = connect(row_factory=tuple_row)
    try:
        yield conn
    finally:
        conn.close()


def _admission(conn, hadm_id):
    if not conn.execute("SELECT 1 FROM admissions WHERE hadm_id = %s",
                        (hadm_id,)).fetchone():
        raise HTTPException(404, {"code": "PATIENT_NOT_FOUND",
                                  "message": f"No admission {hadm_id}."})


def _event_json(e):
    return {
        "seq": e["seq"],
        "event_id": e["event_id"],
        "tag": e["tag"],
        "kind": e["kind"],
        "actor": e["actor"],
        "at": e["created_at"].isoformat(),
        "clock": _clock(e["created_at"]),
        "text": _event_line(e),          # "10:42, logged: two units O neg started"
        "body": e["body"],
    }


# ---------------------------------------------------------------- room

@router.get("/room/{hadm_id}/timeline")
def room_timeline(hadm_id: int,
                  after_seq: int = Query(0, ge=0,
                      description="Only events after this seq, for polling."),
                  limit: int = Query(100, ge=1, le=500),
                  conn=Depends(_conn)):
    """
    The room record, oldest first. Poll with after_seq = the last seq you
    have, every few seconds, to append new lines as Alexa writes them.
    """
    _admission(conn, hadm_id)
    rows = conn.execute(
        "SELECT event_id, seq, kind, actor, body, created_at "
        "FROM timeline_events WHERE hadm_id = %s AND seq > %s "
        "ORDER BY seq LIMIT %s", (hadm_id, after_seq, limit)).fetchall()
    import json
    events = [{"event_id": e, "seq": s, "kind": k, "actor": a,
               "body": json.loads(b), "created_at": t, "tag": f"event#{e}"}
              for e, s, k, a, b, t in rows]
    open_sid = conn.execute(
        "SELECT session_id, room, started_by, started_at FROM room_sessions "
        "WHERE hadm_id = %s AND ended_at IS NULL "
        "ORDER BY session_id DESC LIMIT 1", (hadm_id,)).fetchone()
    return {
        "hadm_id": hadm_id,
        "short_id": _short_id(conn, hadm_id),
        "room_open": bool(open_sid),
        "session": ({"session_id": open_sid[0], "room": open_sid[1],
                     "started_by": open_sid[2],
                     "started_at": open_sid[3].isoformat()}
                    if open_sid else None),
        "events": [_event_json(e) for e in events],
        "last_seq": events[-1]["seq"] if events else after_seq,
    }


@router.get("/room/{hadm_id}/verify")
def room_verify(hadm_id: int, conn=Depends(_conn)):
    """Recompute every hash. ok=false names the first broken entry."""
    _admission(conn, hadm_id)
    v = R.verify_chain(conn, hadm_id)
    if v["ok"]:
        msg = (f"All {v['events']} entries check out. Nothing has been "
               f"changed or removed." if v["events"] else "Nothing recorded yet.")
    else:
        msg = (f"Entry {v['broken_at']} fails its check: {v['reason']}. "
               f"Entries before it are intact.")
    return {"hadm_id": hadm_id, **v, "message": msg}


@router.get("/room/{hadm_id}/reminders")
def room_reminders(hadm_id: int, conn=Depends(_conn)):
    """Pending and fired-but-not-done reminders for this patient."""
    _admission(conn, hadm_id)
    rows = conn.execute(
        "SELECT reminder_id, text, due_at, status, created_by FROM reminders "
        "WHERE hadm_id = %s AND status IN ('pending', 'fired') "
        "ORDER BY due_at", (hadm_id,)).fetchall()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return {"hadm_id": hadm_id, "reminders": [
        {"reminder_id": i, "text": t, "due_at": d.isoformat(),
         "due_clock": _clock(d), "status": s, "created_by": c,
         "is_due": d <= now}
        for i, t, d, s, c in rows]}


class LogBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)
    actor: str = Field("web", min_length=1, max_length=40)


@router.post("/room/{hadm_id}/log", status_code=201)
def room_log(hadm_id: int, body: LogBody, conn=Depends(_conn)):
    """Log an entry from the screen. Same hash chain as voice entries."""
    _admission(conn, hadm_id)
    sid = conn.execute(
        "SELECT session_id FROM room_sessions WHERE hadm_id = %s AND "
        "ended_at IS NULL ORDER BY session_id DESC LIMIT 1",
        (hadm_id,)).fetchone()
    ev = R.log_note(conn, hadm_id, body.text.strip(), body.actor,
                    sid[0] if sid else None)
    conn.commit()
    return {"hadm_id": hadm_id, "seq": ev["seq"], "tag": ev["tag"],
            "clock": _clock(ev["created_at"])}


# ---------------------------------------------------------------- desk

@router.get("/desk/requests")
def desk_requests(hadm_id: Optional[int] = None, conn=Depends(_conn)):
    """Open and acknowledged requests, stat first, then oldest first."""
    reqs = R.open_requests(conn, hadm_id)
    out = []
    for r in reqs:
        out.append({**r,
                    "short_id": _short_id(conn, r["hadm_id"]),
                    "requested_at": r["requested_at"].isoformat(),
                    "requested_clock": _clock(r["requested_at"])})
    return {"count": len(out), "requests": out}


class RequestUpdate(BaseModel):
    status: Literal["acknowledged", "done", "cancelled"]
    actor: str = Field("desk", min_length=1, max_length=40)


@router.post("/desk/requests/{request_id}")
def desk_update(request_id: int, body: RequestUpdate, conn=Depends(_conn)):
    """The desk acknowledges or completes a request. Logged in the room."""
    ev = R.update_request(conn, request_id, body.status, body.actor)
    if not ev:
        raise HTTPException(404, {"code": "REQUEST_NOT_FOUND",
                                  "message": f"No request {request_id}."})
    conn.commit()
    return {"request_id": request_id, "status": body.status,
            "seq": ev["seq"], "clock": _clock(ev["created_at"])}


@router.post("/desk/reminders/check")
def desk_reminders_check(conn=Depends(_conn)):
    """
    Reminders whose time has come, for all patients. Each is returned once
    (it is marked fired and logged), so poll this every ~15 seconds and
    show/sound whatever comes back.
    """
    due = R.due_reminders(conn)
    conn.commit()
    return {"due": [{**d, "short_id": _short_id(conn, d["hadm_id"]),
                     "due_at": d["due_at"].isoformat(),
                     "due_clock": _clock(d["due_at"])} for d in due]}
