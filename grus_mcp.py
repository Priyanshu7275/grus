"""
GRUS Room — MCP server

The tools Alexa+ (or any MCP client) is allowed to call. Streamable HTTP,
MCP protocol 2025-11-25, served at /mcp on the same FastAPI app as the
website, so one deploy serves both.

The design rule: Alexa's model never composes a clinical number.

  - Clinical questions go through ask_record, which runs the GRUS chat
    (tools -> citations -> invented-citation strip -> value check) and
    returns a sentence already rewritten for the ear (grus_spoken).
  - Alerts and scores come from the deterministic rule and score engines.
  - Every patient answer starts "For patient ending 2254:" so a wrong
    patient is caught by ear, not discovered later.
  - Everything Alexa is asked to read is in the result's text, already
    checked. Alexa is told to read it as written.

Room tools (log, request, remind, timeline, handover, verify) write to the
hash-chained record in grus_room.

Stateless HTTP: no session state on the server, so it works behind the
Elastic Beanstalk load balancer with any number of instances. Every
patient tool takes the patient's last digits; the open room session is
looked up from the database, not remembered in memory.

Auth: a bearer token per caller, set in the environment:

    GRUS_MCP_TOKENS="long-random-token-1=dr.rao,long-random-token-2=desk"

The name after "=" is who the timeline records as having said it. With
no tokens set the endpoint refuses every request, unless
GRUS_MCP_DEV=1 (local testing only).

Mount it in grus_api.py, after `app = FastAPI(...)`:

    from grus_mcp import attach_mcp
    attach_mcp(app)
"""

import hmac
import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import JSONResponse
from starlette.routing import Route

import grus_room as R
from grus_spoken import speak_units

ROOM_TZ = ZoneInfo(os.environ.get("GRUS_ROOM_TZ", "Asia/Kolkata"))
SERVER_VERSION = "1.0.0"

INSTRUCTIONS = """\
GRUS Room is clinical decision support for one patient's room. Decision
support, not diagnosis.

Rules:
1. Every patient tool needs `patient`: the last 4 digits of the admission
   number the clinician says ("patient ending 2254"). If a tool says the
   digits match more than one patient, ask for more digits.
2. Read the `text` of each result exactly as written. It is already
   checked against the record. Do not add, round, or change any number,
   and never state a clinical value no tool returned.
3. Any clinical question about the patient goes to ask_record, even if
   another tool seems close.
4. "Log ...", "note ...", "record ..." -> log_event.
   "Ask the desk / get / send for ..." -> send_request.
   "Remind me ..." -> set_reminder.
   "What happened / what did we do ..." -> query_timeline.
   "Handover" -> prepare_handover.
5. If a result says something could not be verified, say so; do not guess.
"""

# --------------------------------------------------------------- helpers


class ToolError(Exception):
    """A problem the clinician should hear, in plain words."""


def _connect():
    from psycopg.rows import tuple_row
    from grus_config import connect
    return connect(row_factory=tuple_row)


def _short_id(conn, hadm_id):
    """
    The fewest trailing digits (at least 4) that name this patient and no
    other current patient, so "ending 0495" is never ambiguous by ear.
    """
    s = str(hadm_id)
    for n in range(4, len(s)):
        (others,) = conn.execute(
            "SELECT COUNT(*) FROM admissions WHERE is_current = TRUE "
            "AND hadm_id <> %s AND CAST(hadm_id AS TEXT) LIKE %s",
            (hadm_id, f"%{s[-n:]}")).fetchone()
        if not others:
            return s[-n:]
    return s


def _prefix(conn, hadm_id):
    return f"For patient ending {_short_id(conn, hadm_id)}: "


def _clock(ts):
    return ts.astimezone(ROOM_TZ).strftime("%H:%M")


def _ago(ts, now=None):
    mins = int(((now or datetime.now(timezone.utc)) - ts).total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    h = mins // 60
    return f"{h} hour{'s' if h != 1 else ''} ago"


_ACRONYMS = {"INR", "NSAID", "NSAIDS", "AKI", "BP", "SBP", "PE", "ICU", "ECG",
             "HR", "CKD", "GI", "DVT", "KDIGO", "SIRS", "SOFA", "QSOFA"}


def _plain_title(title):
    """'SHOCK INDEX - SEVERE' -> 'Shock index, severe'; 'INR' stays 'INR'."""
    t = re.sub(r"\s*[—-]\s+|\s+[—-]\s*", ", ", title.strip())
    words = [w if w.upper().strip(",.()") in _ACRONYMS else w.lower()
             for w in t.split()]
    t = " ".join(words)
    return t[:1].upper() + t[1:]


def _speak_hours(text):
    """
    Rule bodies carry record hours ('nadir 6.9 at 771h'). MIMIC dates are
    shifted, so say where on the admission it falls instead.
    """
    from grus_spoken import when

    return re.sub(r"(?:\bat |@ ?)?\b(\d+(?:\.\d+)?)\s?h\b",
                  lambda m: when(float(m.group(1))) or m.group(0), text)


def resolve_patient(conn, patient):
    """
    Spoken digits -> one current admission. Never guesses: no match or
    several matches is an answer the clinician hears.
    """
    digits = re.sub(r"\D", "", str(patient or ""))
    if len(digits) < 3:
        raise ToolError("Which patient? Say the last four digits of the "
                        "admission number.")
    rows = conn.execute(
        "SELECT hadm_id FROM admissions WHERE is_current = TRUE "
        "AND CAST(hadm_id AS TEXT) LIKE %s ORDER BY hadm_id LIMIT 6",
        (f"%{digits}",)).fetchall()
    if not rows:
        raise ToolError(f"No current patient's admission number ends in "
                        f"{digits}.")
    if len(rows) > 1:
        endings = ", ".join(str(r[0])[-6:] for r in rows)
        raise ToolError(f"{len(rows)} patients end in {digits} "
                        f"(ending {endings}). Say more digits.")
    return rows[0][0]


def _open_session(conn, hadm_id):
    row = conn.execute(
        "SELECT session_id FROM room_sessions WHERE hadm_id = %s "
        "AND ended_at IS NULL ORDER BY session_id DESC LIMIT 1",
        (hadm_id,)).fetchone()
    return row[0] if row else None


def _result(text, data=None, error=False):
    return {"text": text, "data": data or {}, "error": error}


# ------------------------------------------------------- patient tools

def t_find_patient(conn, a, actor):
    digits = re.sub(r"\D", "", str(a.get("digits", "")))
    if len(digits) < 2:
        raise ToolError("Say at least two digits of the admission number.")
    rows = conn.execute("""
        SELECT a.hadm_id, p.anchor_age, p.gender, a.risk_level,
               (SELECT long_title FROM diagnoses d WHERE d.hadm_id = a.hadm_id
                 AND long_title IS NOT NULL ORDER BY seq_num NULLS LAST LIMIT 1)
        FROM admissions a JOIN patients p ON p.subject_id = a.subject_id
        WHERE a.is_current = TRUE AND CAST(a.hadm_id AS TEXT) LIKE %s
        ORDER BY a.hadm_id LIMIT 5""", (f"%{digits}",)).fetchall()
    if not rows:
        return _result(f"No current patient's admission number ends in "
                       f"{digits}.")
    found = [{"hadm_id": h, "ending": _short_id(conn, h), "age": age, "sex": sex,
              "risk": risk or "unknown", "diagnosis": dx} for
             h, age, sex, risk, dx in rows]
    parts = [f"ending {f['ending']}, {f['age']} {f['sex'] or ''}, "
             f"{f['risk']} risk" + (f", {f['diagnosis']}" if f["diagnosis"]
                                    else "") for f in found]
    return _result(f"{len(found)} match{'es' if len(found) > 1 else ''}: "
                   + "; ".join(parts) + ".", {"patients": found})


_RISK_ORDER = "CASE a.risk_level WHEN 'critical' THEN 0 WHEN 'high' THEN 1 " \
              "WHEN 'moderate' THEN 2 WHEN 'low' THEN 3 ELSE 4 END"


def t_triage_board(conn, a, actor):
    from grus_rules import RULE_VERSION
    limit = max(1, min(int(a.get("limit") or 5), 10))
    rows = conn.execute(f"""
        SELECT a.hadm_id, a.risk_level,
               (SELECT COUNT(*) FROM alert_traces t WHERE t.hadm_id = a.hadm_id
                  AND t.rule_version = %s AND t.dismissed_at IS NULL
                  AND t.severity IN ('critical','warning')) AS n_alerts,
               (SELECT COUNT(*) FROM desk_requests r WHERE r.hadm_id = a.hadm_id
                  AND r.status IN ('open','acknowledged')) AS n_requests
        FROM admissions a WHERE a.is_current = TRUE
        ORDER BY {_RISK_ORDER}, n_alerts DESC, a.hadm_id
        LIMIT %s""", (RULE_VERSION, limit)).fetchall()
    board = [{"hadm_id": h, "ending": _short_id(conn, h), "risk": r or "unknown",
              "alerts": n, "open_requests": q} for h, r, n, q in rows]
    parts = [f"ending {b['ending']}, {b['risk']}, {b['alerts']} alert"
             f"{'s' if b['alerts'] != 1 else ''}"
             + (f", {b['open_requests']} open request"
                f"{'s' if b['open_requests'] != 1 else ''}"
                if b["open_requests"] else "") for b in board]
    return _result("Most urgent first: " + "; ".join(parts) + ".",
                   {"patients": board})


def t_ask_record(conn, a, actor):
    hadm_id = resolve_patient(conn, a.get("patient"))
    question = (a.get("question") or "").strip()
    if not question:
        raise ToolError("What would you like to know?")
    sid = _open_session(conn, hadm_id)
    history = R.recent_turns(conn, hadm_id, sid, limit=6) if sid else []
    conn.commit()

    from grus_chat import answer
    res = answer(conn, hadm_id, question, history=history)
    conn.rollback()                       # answer() reads only

    written = res.get("answer", "")
    speech = res.get("speech") or re.sub(r"\[?[a-z_]+#\d+\]?", "",
                                         written).strip()
    R.add_turn(conn, hadm_id, "user", question, sid)
    R.add_turn(conn, hadm_id, "assistant", written, sid, speech,
               res.get("sources", []))
    rejected = (res.get("retrieval") or {}).get("values_rejected") or []
    return _result(_prefix(conn, hadm_id) + speech, {
        "hadm_id": hadm_id, "answer": written, "sources": res.get("sources"),
        "values_rejected": len(rejected),
        "tools_called": [c.get("tool") for c in res.get("tools_called", [])],
    })


def t_get_alerts(conn, a, actor):
    hadm_id = resolve_patient(conn, a.get("patient"))
    from grus_rules import evaluate
    alerts = evaluate(conn, hadm_id, None, persist=False)
    order = {"critical": 0, "warning": 1, "unknown": 2, "info": 3}
    alerts = sorted(alerts, key=lambda x: order.get(x.severity, 9))
    shown = [x for x in alerts if x.severity in ("critical", "warning",
                                                 "unknown")]
    if not shown:
        text = "No critical or warning alerts from the rule engine."
    else:
        # All titles, but the detail only for the most severe one: four
        # full alerts read aloud is too long to hold in your head.
        bits = []
        for i, x in enumerate(shown):
            label = {"critical": "Critical", "warning": "Warning",
                     "unknown": "Can't assess"}[x.severity]
            line = f"{label}: {_plain_title(x.title)}."
            if i == 0 and x.body:
                line += " " + _speak_hours(x.body.strip())
            bits.append(line)
        n = len(shown)
        text = (f"{n} alert{'s' if n != 1 else ''}. " + " ".join(bits)
                + (" Ask about any of them for details." if n > 1 else ""))
        text = re.sub(r"\s{2,}", " ", text)
    return _result(_prefix(conn, hadm_id) + speak_units(text), {
        "hadm_id": hadm_id,
        "alerts": [{"code": x.code, "severity": x.severity, "title": x.title,
                    "detail": x.body, "action": x.action,
                    "sources": [f"{s['table']}#{s['id']}" for s in x.sources]}
                   for x in alerts]})


def t_calculate_score(conn, a, actor):
    hadm_id = resolve_patient(conn, a.get("patient"))
    from grus_tools import GrusTools, dispatch
    res = dispatch(GrusTools(conn, hadm_id), "calculate_score",
                   {"score_name": a.get("score_name"),
                    "provided": a.get("provided") or {}})
    if not res.found or not res.rows:
        return _result(_prefix(conn, hadm_id) + res.summary)
    d = res.rows[0]
    missing = (d.get("components") or {}).get("missing") or []
    if d.get("total") is None:
        asks = " ".join(
            q if q.endswith("?") else q + "?"
            for q in ((m.get("ask") or m["label"]).strip() for m in missing))
        text = (f"{d['name']}: the record can't supply {len(missing)} "
                f"criteri{'a' if len(missing) != 1 else 'on'}, so there is "
                f"no total. {asks}")
    else:
        text = (f"{d['name']} is {d['total']}"
                + (f" out of {d['max_possible']}" if d.get("max_possible")
                   else "")
                + (f", {d['risk']} risk" if d.get("risk") else "") + "."
                + (f" {d['interpretation']}" if d.get("interpretation")
                   else ""))
    return _result(_prefix(conn, hadm_id) + speak_units(text),
                   {"hadm_id": hadm_id, "score": d})


# ---------------------------------------------------------- room tools

def t_start_room(conn, a, actor):
    hadm_id = resolve_patient(conn, a.get("patient"))
    sid = _open_session(conn, hadm_id)
    if sid:
        return _result(_prefix(conn, hadm_id) + "the room is already open.",
                       {"hadm_id": hadm_id, "session_id": sid})
    sid = R.start_session(conn, hadm_id, actor, a.get("room"))
    return _result(_prefix(conn, hadm_id) + "room opened. I'll record only what "
                   "you ask me to log.", {"hadm_id": hadm_id,
                                          "session_id": sid})


def t_end_room(conn, a, actor):
    hadm_id = resolve_patient(conn, a.get("patient"))
    sid = _open_session(conn, hadm_id)
    if not sid:
        return _result(_prefix(conn, hadm_id) + "no room is open.")
    R.end_session(conn, sid, actor)
    return _result(_prefix(conn, hadm_id) + "room closed.",
                   {"hadm_id": hadm_id, "session_id": sid})


def t_log_event(conn, a, actor):
    hadm_id = resolve_patient(conn, a.get("patient"))
    text = (a.get("text") or "").strip()
    if not text:
        raise ToolError("What should I log?")
    ev = R.log_note(conn, hadm_id, text, actor, _open_session(conn, hadm_id))
    return _result(_prefix(conn, hadm_id) + f"logged at {_clock(ev['created_at'])}"
                   f": {text}.", {"hadm_id": hadm_id, "event": ev["tag"],
                                  "seq": ev["seq"]})


def t_send_request(conn, a, actor):
    hadm_id = resolve_patient(conn, a.get("patient"))
    item = (a.get("item") or "").strip()
    if not item:
        raise ToolError("What should I ask the desk for?")
    pr = a.get("priority") or "routine"
    if pr not in ("routine", "urgent", "stat"):
        pr = "routine"
    rq = R.send_request(conn, hadm_id, item, actor, pr,
                        _open_session(conn, hadm_id))
    return _result(_prefix(conn, hadm_id) + f"sent to the desk"
                   + (f" as {pr}" if pr != "routine" else "")
                   + f": {item}. Request number {rq['request_id']}.",
                   {"hadm_id": hadm_id, "request_id": rq["request_id"],
                    "event": rq["tag"]})


def t_list_requests(conn, a, actor):
    hadm_id = resolve_patient(conn, a["patient"]) if a.get("patient") else None
    reqs = R.open_requests(conn, hadm_id)
    if not reqs:
        text = "No open desk requests."
    else:
        now = datetime.now(timezone.utc)
        text = f"{len(reqs)} open: " + "; ".join(
            f"number {r['request_id']}, {r['item']}"
            + (f", {r['priority']}" if r["priority"] != "routine" else "")
            + (f", patient ending {_short_id(conn, r['hadm_id'])}"
               if hadm_id is None else "")
            + f", {r['status']}, asked {_ago(r['requested_at'], now)}"
            for r in reqs) + "."
    if hadm_id:
        text = _prefix(conn, hadm_id) + text
    return _result(text, {"requests": [
        {**r, "requested_at": r["requested_at"].isoformat()} for r in reqs]})


def t_update_request(conn, a, actor):
    status = a.get("status")
    if status not in ("acknowledged", "done", "cancelled"):
        raise ToolError("Status must be acknowledged, done or cancelled.")
    ev = R.update_request(conn, int(a.get("request_id")), status, actor)
    if not ev:
        raise ToolError(f"No request number {a.get('request_id')}.")
    return _result(f"Request {a.get('request_id')} marked {status}.",
                   {"event": ev["tag"]})


def t_set_reminder(conn, a, actor):
    hadm_id = resolve_patient(conn, a.get("patient"))
    text = (a.get("text") or "").strip()
    try:
        minutes = float(a.get("minutes"))
    except (TypeError, ValueError):
        raise ToolError("In how many minutes?")
    if not text or not (0 < minutes <= 24 * 60):
        raise ToolError("I need what to remind you of, and when, "
                        "within 24 hours.")
    due = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    rm = R.set_reminder(conn, hadm_id, text, due, actor,
                        _open_session(conn, hadm_id))
    return _result(_prefix(conn, hadm_id) + f"I'll remind you at {_clock(due)}: "
                   f"{text}.", {"hadm_id": hadm_id,
                                "reminder_id": rm["reminder_id"],
                                "due_at": due.isoformat()})


def t_check_reminders(conn, a, actor):
    due = R.due_reminders(conn)
    if a.get("patient"):
        hadm_id = resolve_patient(conn, a["patient"])
        due = [d for d in due if d["hadm_id"] == hadm_id]
    if not due:
        return _result("No reminders due.")
    return _result(" ".join(
        f"Reminder for patient ending {_short_id(conn, d['hadm_id'])}: "
        f"{d['text']}."
        for d in due), {"due": [{**d, "due_at": d["due_at"].isoformat()}
                                for d in due]})


_KIND_SPOKEN = {"log": "logged", "request": "asked the desk for",
                "request_update": "desk update", "reminder": "reminder set",
                "reminder_fired": "reminder", "session_start": "room opened",
                "session_end": "room closed"}


def _event_line(e):
    b, k = e["body"], e["kind"]
    if k == "log":
        what = b.get("text", "")
    elif k == "request":
        what = b.get("item", "") + (f" ({b['priority']})"
                                    if b.get("priority") not in
                                    (None, "routine") else "")
    elif k == "request_update":
        what = f"{b.get('item', '')} {b.get('status', '')}"
    elif k in ("reminder", "reminder_fired"):
        what = b.get("text", "")
    else:
        what = ""
    label = _KIND_SPOKEN.get(k, k)
    return f"{_clock(e['created_at'])}, {label}" + (f": {what}" if what
                                                     else "")


def t_query_timeline(conn, a, actor):
    hadm_id = resolve_patient(conn, a.get("patient"))
    since = None
    if a.get("last_minutes"):
        since = datetime.now(timezone.utc) - timedelta(
            minutes=float(a["last_minutes"]))
    kinds = None
    if a.get("kind") in ("log", "request", "reminder"):
        kinds = {"log": ["log"], "request": ["request", "request_update"],
                 "reminder": ["reminder", "reminder_fired"]}[a["kind"]]
    events = list(reversed(R.timeline(conn, hadm_id, kinds, since,
                                      limit=int(a.get("limit") or 10))))
    if not events:
        return _result(_prefix(conn, hadm_id) + "nothing recorded"
                       + (" in that time." if since else " yet."))
    return _result(_prefix(conn, hadm_id) + "; ".join(_event_line(e)
                                                for e in events) + ".",
                   {"hadm_id": hadm_id, "events": [
                       {"event": e["tag"], "seq": e["seq"], "kind": e["kind"],
                        "actor": e["actor"], "body": e["body"],
                        "at": e["created_at"].isoformat()} for e in events]})


def t_recall(conn, a, actor):
    hadm_id = resolve_patient(conn, a.get("patient"))
    n = max(1, min(int(a.get("count") or 3), 10))
    rows = conn.execute(
        "SELECT q.text, ans.speech, ans.text, ans.created_at "
        "FROM conversation_turns ans JOIN LATERAL ("
        "  SELECT text FROM conversation_turns u WHERE u.hadm_id = ans.hadm_id"
        "   AND u.role = 'user' AND u.turn_id < ans.turn_id"
        "   ORDER BY u.turn_id DESC LIMIT 1) q ON TRUE "
        "WHERE ans.hadm_id = %s AND ans.role = 'assistant' "
        "ORDER BY ans.turn_id DESC LIMIT %s", (hadm_id, n)).fetchall()
    if not rows:
        return _result(_prefix(conn, hadm_id) + "no earlier questions.")
    rows = list(reversed(rows))
    return _result(_prefix(conn, hadm_id) + " ".join(
        f"At {_clock(t)}, you asked: {q} I said: {s or w}" for q, s, w, t
        in rows), {"hadm_id": hadm_id, "turns": [
            {"question": q, "answer": w, "at": t.isoformat()}
            for q, s, w, t in rows]})


def t_prepare_handover(conn, a, actor):
    """
    Deterministic: assembled from the rule engine and the room's own
    record, no model. What was done, what is pending, what is flagged.
    """
    hadm_id = resolve_patient(conn, a.get("patient"))
    parts = []

    from grus_rules import evaluate
    flagged = [x for x in evaluate(conn, hadm_id, None, persist=False)
               if x.severity in ("critical", "warning")]
    if flagged:
        parts.append("Flagged: " + ", ".join(
            _plain_title(x.title).lower() + (" (critical)"
                                             if x.severity == "critical"
                                             else "") for x in flagged) + ".")

    sid = _open_session(conn, hadm_id)
    started = None
    if sid:
        started = conn.execute("SELECT started_at FROM room_sessions "
                               "WHERE session_id = %s", (sid,)).fetchone()[0]
    logs = [e for e in reversed(R.timeline(conn, hadm_id, ["log"], started,
                                           limit=15))]
    if logs:
        parts.append("Done: " + "; ".join(
            f"{_clock(e['created_at'])} {e['body'].get('text', '')}"
            for e in logs) + ".")

    reqs = R.open_requests(conn, hadm_id)
    if reqs:
        parts.append("Waiting on the desk: " + "; ".join(
            f"{r['item']} ({r['status']})" for r in reqs) + ".")

    pend = conn.execute(
        "SELECT text, due_at FROM reminders WHERE hadm_id = %s "
        "AND status IN ('pending','fired') ORDER BY due_at",
        (hadm_id,)).fetchall()
    if pend:
        parts.append("Reminders: " + "; ".join(
            f"{t} at {_clock(d)}" for t, d in pend) + ".")

    chain = R.verify_chain(conn, hadm_id)
    parts.append("Record intact." if chain["ok"] else
                 f"Warning: the record fails its integrity check at entry "
                 f"{chain['broken_at']}.")

    ev = R.log_event(conn, hadm_id, "handover",
                     {"summary": " ".join(parts)}, actor, sid)
    return _result(_prefix(conn, hadm_id) + "handover. " + " ".join(parts),
                   {"hadm_id": hadm_id, "event": ev["tag"],
                    "chain_ok": chain["ok"]})


def t_verify_record(conn, a, actor):
    hadm_id = resolve_patient(conn, a.get("patient"))
    v = R.verify_chain(conn, hadm_id)
    if v["ok"]:
        text = (f"all {v['events']} entries check out; nothing has been "
                f"changed or removed." if v["events"] else
                "nothing recorded yet.")
    else:
        text = (f"the record fails its check at entry {v['broken_at']}: "
                f"{v['reason']}. Entries before it are intact.")
    return _result(_prefix(conn, hadm_id) + text, {"hadm_id": hadm_id, **v})


# ------------------------------------------------------------ registry

_P = {"patient": {"type": "string",
                  "description": "Last 4 digits of the admission number, "
                                 "e.g. '2254'."}}


def _schema(props=None, required=()):
    return {"type": "object", "properties": {**(props or {})},
            "required": list(required)}


TOOLS = {
    "find_patient": (t_find_patient, False,
        "Find current patients whose admission number ends in the given "
        "digits.",
        _schema({"digits": {"type": "string"}}, ["digits"])),
    "triage_board": (t_triage_board, False,
        "Current patients, most urgent first (risk level, then alert count).",
        _schema({"limit": {"type": "integer", "minimum": 1, "maximum": 10}})),
    "ask_record": (t_ask_record, False,
        "Answer any clinical question about the patient from their record "
        "(labs, vitals, meds, notes, history). The answer is checked "
        "against the record before it is returned. Read `text` as written.",
        _schema({**_P, "question": {"type": "string"}},
                ["patient", "question"])),
    "get_alerts": (t_get_alerts, False,
        "Alerts from the deterministic rule engine, most severe first.",
        _schema(_P, ["patient"])),
    "calculate_score": (t_calculate_score, False,
        "A validated clinical score (PERC, Wells PE, HEART, qSOFA, NEWS2, "
        "CURB-65, HAS-BLED, CHA2DS2-VASc, KDIGO AKI, shock index, ...). "
        "If the record can't supply every criterion there is no total; "
        "ask the clinician the questions and call again with `provided`.",
        _schema({**_P, "score_name": {"type": "string"},
                 "provided": {"type": "object",
                              "description": "answers the clinician gave"}},
                ["patient", "score_name"])),
    "start_room": (t_start_room, True,
        "Open the room for a patient. Entries logged after this belong to "
        "this session.",
        _schema({**_P, "room": {"type": "string"}}, ["patient"])),
    "end_room": (t_end_room, True, "Close the patient's room session.",
        _schema(_P, ["patient"])),
    "log_event": (t_log_event, True,
        "Record something said deliberately in the room, e.g. 'two units "
        "O-negative started'. Permanent and tamper-evident.",
        _schema({**_P, "text": {"type": "string"}}, ["patient", "text"])),
    "send_request": (t_send_request, True,
        "Send a request to the main desk (blood, reports, porter, a call).",
        _schema({**_P, "item": {"type": "string"},
                 "priority": {"type": "string",
                              "enum": ["routine", "urgent", "stat"]}},
                ["patient", "item"])),
    "list_requests": (t_list_requests, False,
        "Open desk requests, for one patient or all.",
        _schema(_P)),
    "update_request": (t_update_request, True,
        "Desk only: mark a request acknowledged, done or cancelled.",
        _schema({"request_id": {"type": "integer"},
                 "status": {"type": "string",
                            "enum": ["acknowledged", "done", "cancelled"]}},
                ["request_id", "status"])),
    "set_reminder": (t_set_reminder, True,
        "Remind the room of something in N minutes.",
        _schema({**_P, "text": {"type": "string"},
                 "minutes": {"type": "number", "exclusiveMinimum": 0,
                             "maximum": 1440}},
                ["patient", "text", "minutes"])),
    "check_reminders": (t_check_reminders, True,
        "Reminders that are now due (marks them as delivered).",
        _schema(_P)),
    "query_timeline": (t_query_timeline, False,
        "What was logged, requested or reminded for this patient, oldest "
        "first, with clock times.",
        _schema({**_P, "last_minutes": {"type": "number"},
                 "kind": {"type": "string",
                          "enum": ["log", "request", "reminder"]},
                 "limit": {"type": "integer", "maximum": 30}},
                ["patient"])),
    "recall": (t_recall, False,
        "What was asked and answered earlier about this patient.",
        _schema({**_P, "count": {"type": "integer", "maximum": 10}},
                ["patient"])),
    "prepare_handover": (t_prepare_handover, True,
        "A handover: flagged alerts, what was done this session, what the "
        "desk still owes, pending reminders, and whether the record is "
        "intact. Built from the record, not composed by a model.",
        _schema(_P, ["patient"])),
    "verify_record": (t_verify_record, False,
        "Check that the patient's room record has not been changed or "
        "had entries removed.",
        _schema(_P, ["patient"])),
}


def run_tool(name, args, actor, conn=None):
    """
    Run one tool synchronously. Commits on success, rolls back on any
    error. Returns {"text", "data", "error"}. Usable without MCP (tests,
    the website's voice page).
    """
    entry = TOOLS.get(name)
    if entry is None:
        return _result(f"No tool named {name}.", error=True)
    fn = entry[0]
    own = conn is None
    try:
        if own:
            conn = _connect()
        out = fn(conn, args or {}, actor)
        conn.commit()
        return out
    except ToolError as e:
        conn.rollback()
        return _result(str(e), error=True)
    except Exception as e:                       # never leak a traceback
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"[grus_mcp] {name} failed: {e!r}")
        return _result("Something went wrong reading the record, so I "
                       "can't answer that right now.", error=True)
    finally:
        if own and conn is not None:
            conn.close()


# ------------------------------------------------------------ MCP layer

def _tokens():
    out = {}
    for pair in os.environ.get("GRUS_MCP_TOKENS", "").split(","):
        tok, _, who = pair.strip().partition("=")
        if tok and who:
            out[tok] = who
    return out


def _actor_for(auth_header):
    tokens = _tokens()
    if not tokens:
        return "dev" if os.environ.get("GRUS_MCP_DEV") == "1" else None
    tok = (auth_header or "").removeprefix("Bearer ").strip()
    for known, who in tokens.items():
        if tok and hmac.compare_digest(tok, known):
            return who
    return None


async def _list_tools(ctx, params):
    return types.ListToolsResult(tools=[
        types.Tool(name=name, description=desc, inputSchema=schema,
                   annotations=types.ToolAnnotations(
                       readOnlyHint=not writes, destructiveHint=False,
                       idempotentHint=not writes, openWorldHint=False))
        for name, (_, writes, desc, schema) in TOOLS.items()])


async def _call_tool(ctx, params):
    headers = ctx.request.headers if ctx.request is not None else {}
    actor = _actor_for(headers.get("authorization")) or "unknown"
    out = await anyio.to_thread.run_sync(
        run_tool, params.name, dict(params.arguments or {}), actor)
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=out["text"])],
        structuredContent=json.loads(json.dumps(
            {"text": out["text"], **out["data"]}, default=str)),
        isError=out["error"])


server = Server("grus-room", version=SERVER_VERSION, title="GRUS Room",
                instructions=INSTRUCTIONS, on_list_tools=_list_tools,
                on_call_tool=_call_tool)


class _Guard:
    """Bearer token and Origin check in front of the MCP endpoint."""

    def __init__(self, app):
        self.app = app
        self.origins = {o.strip() for o in os.environ.get(
            "GRUS_MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        h = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        origin = h.get("origin")
        if origin and origin not in self.origins:
            return await JSONResponse({"error": "origin not allowed"},
                                      403)(scope, receive, send)
        if _actor_for(h.get("authorization")) is None:
            msg = ("MCP auth not configured" if not _tokens()
                   else "invalid or missing bearer token")
            return await JSONResponse(
                {"error": msg}, 401,
                headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        return await self.app(scope, receive, send)


def build_asgi():
    """(asgi_app, session_manager). The manager must be run() in lifespan."""
    manager = StreamableHTTPSessionManager(
        app=server, stateless=True, json_response=True,
        session_idle_timeout=None)

    async def endpoint(scope, receive, send):
        await manager.handle_request(scope, receive, send)

    return _Guard(endpoint), manager


def attach_mcp(app, path="/mcp"):
    """Mount the MCP endpoint on an existing FastAPI/Starlette app."""
    asgi, manager = build_asgi()
    app.router.routes.append(
        Route(path, endpoint=asgi, methods=["GET", "POST", "DELETE"]))

    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(a):
        async with manager.run():
            async with previous(a) as state:
                yield state

    app.router.lifespan_context = lifespan
    return app