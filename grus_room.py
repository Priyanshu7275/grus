"""
GRUS Room — the room's record

Everything said deliberately in the room ("log: 2 units O-neg started",
"ask the desk for a crossmatch", "remind me in 20 minutes") becomes a row
here. Nothing is recorded continuously; only what a clinician chose to say.

The timeline is a black box in the flight-recorder sense: append-only and
hash-chained per patient. Each event stores the hash of the one before it,
so changing, deleting or re-ordering any past event breaks every hash
after it, and verify_chain() says exactly where.

  hash = sha256(prev_hash | hadm_id | seq | kind | actor | created_at | body)

The database also refuses UPDATE / DELETE / TRUNCATE on the timeline.
Someone with admin rights could drop that trigger, so the chain head is
anchored outside the database as well (anchor_head, S3 Object Lock) —
after that, even a rewrite of the whole chain is detectable.

Times here are REAL clock times (UTC): GRUS Room writes these rows itself.
That is different from the MIMIC-IV record, whose dates are shifted.

Functions do not commit. The caller commits, as in backfill_alerts.py.
"""

import hashlib
import json
from datetime import datetime, timezone

GENESIS = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS room_sessions (
    session_id   BIGSERIAL PRIMARY KEY,
    hadm_id      BIGINT NOT NULL REFERENCES admissions(hadm_id),
    room         TEXT,
    started_by   TEXT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS timeline_events (
    event_id     BIGSERIAL PRIMARY KEY,
    hadm_id      BIGINT NOT NULL REFERENCES admissions(hadm_id),
    session_id   BIGINT REFERENCES room_sessions(session_id),
    seq          INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    actor        TEXT NOT NULL,
    body         TEXT NOT NULL,          -- canonical JSON, exactly as hashed
    created_at   TIMESTAMPTZ NOT NULL,
    prev_hash    CHAR(64) NOT NULL,
    hash         CHAR(64) NOT NULL UNIQUE,
    UNIQUE (hadm_id, seq)
);
CREATE INDEX IF NOT EXISTS timeline_events_hadm ON timeline_events (hadm_id, seq);

CREATE OR REPLACE FUNCTION timeline_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'timeline_events is append-only';
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS timeline_no_change ON timeline_events;
CREATE TRIGGER timeline_no_change BEFORE UPDATE OR DELETE ON timeline_events
    FOR EACH ROW EXECUTE FUNCTION timeline_append_only();
DROP TRIGGER IF EXISTS timeline_no_truncate ON timeline_events;
CREATE TRIGGER timeline_no_truncate BEFORE TRUNCATE ON timeline_events
    FOR EACH STATEMENT EXECUTE FUNCTION timeline_append_only();

CREATE TABLE IF NOT EXISTS desk_requests (
    request_id   BIGSERIAL PRIMARY KEY,
    hadm_id      BIGINT NOT NULL REFERENCES admissions(hadm_id),
    session_id   BIGINT REFERENCES room_sessions(session_id),
    item         TEXT NOT NULL,
    priority     TEXT NOT NULL DEFAULT 'routine'
                 CHECK (priority IN ('routine', 'urgent', 'stat')),
    status       TEXT NOT NULL DEFAULT 'open'
                 CHECK (status IN ('open', 'acknowledged', 'done', 'cancelled')),
    requested_by TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by   TEXT,
    event_id     BIGINT REFERENCES timeline_events(event_id)
);
CREATE INDEX IF NOT EXISTS desk_requests_open ON desk_requests (status, requested_at);

CREATE TABLE IF NOT EXISTS reminders (
    reminder_id  BIGSERIAL PRIMARY KEY,
    hadm_id      BIGINT NOT NULL REFERENCES admissions(hadm_id),
    session_id   BIGINT REFERENCES room_sessions(session_id),
    text         TEXT NOT NULL,
    due_at       TIMESTAMPTZ NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'fired', 'done', 'cancelled')),
    created_by   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_id     BIGINT REFERENCES timeline_events(event_id)
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders (status, due_at);

CREATE TABLE IF NOT EXISTS conversation_turns (
    turn_id      BIGSERIAL PRIMARY KEY,
    session_id   BIGINT REFERENCES room_sessions(session_id),
    hadm_id      BIGINT NOT NULL REFERENCES admissions(hadm_id),
    role         TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text         TEXT NOT NULL,
    speech       TEXT,
    sources      JSONB NOT NULL DEFAULT '[]',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversation_turns_hadm ON conversation_turns (hadm_id, created_at);

CREATE TABLE IF NOT EXISTS chain_anchors (
    anchor_id    BIGSERIAL PRIMARY KEY,
    hadm_id      BIGINT NOT NULL,
    seq          INTEGER NOT NULL,
    hash         CHAR(64) NOT NULL,
    location     TEXT NOT NULL,          -- e.g. s3://bucket/key?versionId=...
    anchored_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def create_tables(conn):
    conn.execute(SCHEMA)


# ---------------------------------------------------------------- hashing

def _now():
    return datetime.now(timezone.utc)


def _iso(ts):
    return ts.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _canon(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _hash(prev_hash, hadm_id, seq, kind, actor, created_at, body_text):
    s = "|".join([prev_hash, str(hadm_id), str(seq), kind, actor,
                  _iso(created_at), body_text])
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- timeline

def log_event(conn, hadm_id, kind, body, actor, session_id=None):
    """
    Append one event to this patient's chain. Returns a dict with
    event_id, seq, hash, created_at and the citation tag "event#<id>".

    An advisory lock per patient serialises appends, so two requests at
    the same moment cannot both claim the same seq.
    """
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (hadm_id,))
    last = conn.execute(
        "SELECT seq, hash FROM timeline_events WHERE hadm_id = %s "
        "ORDER BY seq DESC LIMIT 1", (hadm_id,)).fetchone()
    seq, prev = (last[0] + 1, last[1]) if last else (1, GENESIS)

    created_at = _now()
    body_text = _canon(body)
    h = _hash(prev, hadm_id, seq, kind, actor, created_at, body_text)
    (event_id,) = conn.execute(
        "INSERT INTO timeline_events (hadm_id, session_id, seq, kind, actor, "
        "body, created_at, prev_hash, hash) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING event_id",
        (hadm_id, session_id, seq, kind, actor, body_text, created_at,
         prev, h)).fetchone()
    return {"event_id": event_id, "seq": seq, "hash": h,
            "created_at": created_at, "tag": f"event#{event_id}"}


def verify_chain(conn, hadm_id):
    """
    Recompute every hash for this patient, in order.

    Returns {"ok", "events", "head", "broken_at", "reason"}. broken_at is
    the first seq that fails; everything before it is intact.
    """
    rows = conn.execute(
        "SELECT seq, kind, actor, body, created_at, prev_hash, hash "
        "FROM timeline_events WHERE hadm_id = %s ORDER BY seq",
        (hadm_id,)).fetchall()
    prev, expected_seq = GENESIS, 1
    for seq, kind, actor, body, created_at, prev_hash, h in rows:
        fail = None
        if seq != expected_seq:
            fail = f"missing event: expected #{expected_seq}, found #{seq}"
        elif prev_hash != prev:
            fail = "link to the previous event does not match"
        elif _hash(prev, hadm_id, seq, kind, actor, created_at, body) != h:
            fail = "contents changed after they were recorded"
        if fail:
            return {"ok": False, "events": len(rows), "head": None,
                    "broken_at": seq, "reason": fail}
        prev, expected_seq = h, seq + 1
    return {"ok": True, "events": len(rows),
            "head": {"seq": expected_seq - 1, "hash": prev} if rows else None,
            "broken_at": None, "reason": None}


def timeline(conn, hadm_id, kinds=None, since=None, limit=50):
    """Events for one patient, newest first, for query_timeline / the UI."""
    sql = ("SELECT event_id, seq, kind, actor, body, created_at "
           "FROM timeline_events WHERE hadm_id = %s")
    args = [hadm_id]
    if kinds:
        sql += " AND kind = ANY(%s)"
        args.append(list(kinds))
    if since is not None:
        sql += " AND created_at >= %s"
        args.append(since)
    sql += " ORDER BY seq DESC LIMIT %s"
    args.append(limit)
    return [{"event_id": e, "seq": s, "kind": k, "actor": a,
             "body": json.loads(b), "created_at": t, "tag": f"event#{e}"}
            for e, s, k, a, b, t in conn.execute(sql, args).fetchall()]


# ---------------------------------------------------------------- sessions

def start_session(conn, hadm_id, started_by, room=None):
    (sid,) = conn.execute(
        "INSERT INTO room_sessions (hadm_id, room, started_by) "
        "VALUES (%s,%s,%s) RETURNING session_id",
        (hadm_id, room, started_by)).fetchone()
    log_event(conn, hadm_id, "session_start", {"room": room}, started_by, sid)
    return sid


def end_session(conn, session_id, actor):
    row = conn.execute(
        "UPDATE room_sessions SET ended_at = now() "
        "WHERE session_id = %s AND ended_at IS NULL RETURNING hadm_id",
        (session_id,)).fetchone()
    if row:
        log_event(conn, row[0], "session_end", {}, actor, session_id)
    return bool(row)


# ---------------------------------------------------------------- log

def log_note(conn, hadm_id, text, actor, session_id=None):
    """'Alexa, log: two units O-negative started.'"""
    return log_event(conn, hadm_id, "log", {"text": text}, actor, session_id)


# ---------------------------------------------------------------- desk

def send_request(conn, hadm_id, item, actor, priority="routine",
                 session_id=None):
    """'Ask the desk for a crossmatch, urgent.' Logged, then queued."""
    ev = log_event(conn, hadm_id, "request",
                   {"item": item, "priority": priority}, actor, session_id)
    (rid,) = conn.execute(
        "INSERT INTO desk_requests (hadm_id, session_id, item, priority, "
        "requested_by, event_id) VALUES (%s,%s,%s,%s,%s,%s) "
        "RETURNING request_id",
        (hadm_id, session_id, item, priority, actor,
         ev["event_id"])).fetchone()
    return {"request_id": rid, **ev}


def update_request(conn, request_id, status, actor):
    """The desk acknowledges / completes; that is logged too."""
    row = conn.execute(
        "UPDATE desk_requests SET status = %s, updated_at = now(), "
        "updated_by = %s WHERE request_id = %s "
        "RETURNING hadm_id, session_id, item",
        (status, actor, request_id)).fetchone()
    if not row:
        return None
    hadm_id, sid, item = row
    return log_event(conn, hadm_id, "request_update",
                     {"request_id": request_id, "item": item,
                      "status": status}, actor, sid)


def open_requests(conn, hadm_id=None):
    sql = ("SELECT request_id, hadm_id, item, priority, status, requested_by, "
           "requested_at FROM desk_requests "
           "WHERE status IN ('open', 'acknowledged')")
    args = []
    if hadm_id is not None:
        sql += " AND hadm_id = %s"
        args.append(hadm_id)
    sql += (" ORDER BY CASE priority WHEN 'stat' THEN 0 WHEN 'urgent' THEN 1 "
            "ELSE 2 END, requested_at")
    cols = ["request_id", "hadm_id", "item", "priority", "status",
            "requested_by", "requested_at"]
    return [dict(zip(cols, r)) for r in conn.execute(sql, args).fetchall()]


# ---------------------------------------------------------------- reminders

def set_reminder(conn, hadm_id, text, due_at, actor, session_id=None):
    """'Remind me to recheck the potassium in 20 minutes.'"""
    ev = log_event(conn, hadm_id, "reminder",
                   {"text": text, "due_at": _iso(due_at)}, actor, session_id)
    (rid,) = conn.execute(
        "INSERT INTO reminders (hadm_id, session_id, text, due_at, "
        "created_by, event_id) VALUES (%s,%s,%s,%s,%s,%s) "
        "RETURNING reminder_id",
        (hadm_id, session_id, text, due_at, actor,
         ev["event_id"])).fetchone()
    return {"reminder_id": rid, **ev}


def due_reminders(conn, now=None):
    """Pending reminders whose time has come; marks them fired."""
    rows = conn.execute(
        "UPDATE reminders SET status = 'fired' "
        "WHERE status = 'pending' AND due_at <= %s "
        "RETURNING reminder_id, hadm_id, session_id, text, due_at",
        (now or _now(),)).fetchall()
    out = []
    for rid, hadm_id, sid, text, due in rows:
        log_event(conn, hadm_id, "reminder_fired",
                  {"reminder_id": rid, "text": text}, "system", sid)
        out.append({"reminder_id": rid, "hadm_id": hadm_id, "text": text,
                    "due_at": due})
    return out


# ---------------------------------------------------------------- history

def add_turn(conn, hadm_id, role, text, session_id=None, speech=None,
             sources=None):
    (tid,) = conn.execute(
        "INSERT INTO conversation_turns (session_id, hadm_id, role, text, "
        "speech, sources) VALUES (%s,%s,%s,%s,%s,%s) RETURNING turn_id",
        (session_id, hadm_id, role, text, speech,
         json.dumps(sources or []))).fetchone()
    return tid


def recent_turns(conn, hadm_id, session_id=None, limit=10):
    """Last N turns, oldest first — the shape grus_chat's history expects."""
    sql = "SELECT role, text FROM conversation_turns WHERE hadm_id = %s"
    args = [hadm_id]
    if session_id is not None:
        sql += " AND session_id = %s"
        args.append(session_id)
    sql += " ORDER BY turn_id DESC LIMIT %s"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    return [{"role": r, "content": t} for r, t in reversed(rows)]