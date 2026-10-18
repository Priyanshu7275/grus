"""
One-time setup for GRUS Room: creates the room tables in the grus schema
and runs a self-test on a throwaway transaction (rolled back, nothing kept).
Safe to run more than once.

Connection: same as check_alerts.py. If your DB import line there is
different, copy it over the "from ... import DB" line below.
"""
import psycopg
from psycopg.rows import tuple_row
from grus_config import DB   # <- use the same import line as check_alerts.py

import grus_room as R

conn = psycopg.connect(host=DB.HOST, port=5432, dbname=DB.NAME, user=DB.USER,
                       password=DB.PASSWORD, sslmode="require",
                       row_factory=tuple_row)
conn.execute("SET search_path TO grus, public")

R.create_tables(conn)
conn.commit()
print("Tables ready: room_sessions, timeline_events, desk_requests, "
      "reminders, conversation_turns, chain_anchors")

# Self-test on a real admission, then roll back so nothing is kept.
(hadm_id,) = conn.execute("SELECT hadm_id FROM admissions LIMIT 1").fetchone()
sid = R.start_session(conn, hadm_id, "setup-test", "test")
R.log_note(conn, hadm_id, "self-test", "setup-test", sid)
R.send_request(conn, hadm_id, "self-test request", "setup-test", "routine", sid)
v = R.verify_chain(conn, hadm_id)
try:
    conn.execute("SAVEPOINT s")
    conn.execute("UPDATE timeline_events SET actor = 'x' WHERE hadm_id = %s",
                 (hadm_id,))
    blocked = False
except Exception:
    conn.execute("ROLLBACK TO SAVEPOINT s")
    blocked = True
conn.rollback()

print(f"Chain check: {'OK' if v['ok'] else 'FAILED'} ({v['events']} events)")
print(f"Edits blocked: {'YES' if blocked else 'NO - tell Claude'}")
print("Self-test rolled back; no test rows kept.")