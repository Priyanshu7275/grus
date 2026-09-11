"""
GRUS — AgentCore entrypoint

Wraps the Strands agent graph in a Bedrock AgentCore runtime, so the
agents run in AWS rather than on a laptop.

Nothing about the graph changes. AgentCore is a place to run it, not a
different way to build it — the same five agents, the same tools, the
same rules underneath.

    payload in:   {"hadm_id": 28173870, "as_of_hours": 1}
    payload out:  {"brief": "...", "trust": {...}, "agents_run": [...]}

Run locally:
    python grus_agentcore.py
    curl -X POST http://localhost:8080/invocations \\
         -H 'Content-Type: application/json' \\
         -d '{"hadm_id": 28173870, "as_of_hours": 1}'

Deploy:
    see grus_agentcore_deploy.py
"""

import os
import time
import psycopg
from psycopg.rows import tuple_row
from grus_config import DB
from bedrock_agentcore import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

# In the runtime these come from the container environment rather than a
# .env file. Defaults let the same file run locally without changes.
DB_HOST = DB.HOST
DB_NAME = DB.NAME
DB_USER = DB.USER
DB_PASSWORD = DB.PASSWORD

if not DB_PASSWORD:
    # Local development only. The container gets a real environment.
    from dotenv import load_dotenv
    
    DB_PASSWORD = os.environ.get("DB_PASSWORD")


def _conn():
    """
    A fresh connection per request.

    AgentCore may reuse a container across invocations, and a pooled
    connection that has been idle through a scale-down returns errors
    rather than rows. One connection per request is slower and correct.
    """
    conn = psycopg.connect(
        host=DB_HOST, port=5432, dbname=DB_NAME, user=DB_USER,
        password=DB_PASSWORD, sslmode="require",
        connect_timeout=15, keepalives=1, keepalives_idle=30,
        row_factory=tuple_row)
    conn.execute("SET search_path TO grus, public")
    return conn


@app.entrypoint
def invoke(payload, context=None):
    """
    Generate a brief for one patient.

    payload:
        hadm_id       required
        as_of_hours   optional. Omit for the full record; pass 1 for the
                      record as it stood one hour after arrival.
        mode          'brief' (default) | 'alerts' | 'chat'

    'alerts' skips the model entirely and returns rule output in
    milliseconds — worth having when the caller needs something instant
    and a five-second brief would be wasted.
    """
    t0 = time.time()

    hadm_id = payload.get("hadm_id")
    if not hadm_id:
        return {"error": "hadm_id is required",
                "example": {"hadm_id": 28173870, "as_of_hours": 1}}

    as_of = payload.get("as_of_hours")
    mode = payload.get("mode", "brief")

    conn = None
    try:
        conn = _conn()

        exists = conn.execute(
            "SELECT 1 FROM admissions WHERE hadm_id = %s", (hadm_id,)
        ).fetchone()
        if not exists:
            return {"error": f"no admission {hadm_id}"}

        # --- rules only ---
        if mode == "alerts":
            from grus_rules import evaluate
            alerts = evaluate(conn, hadm_id, as_of, persist=False)
            return {
                "hadm_id": hadm_id,
                "as_of_hours": as_of,
                "alerts": [{"code": a.code, "severity": a.severity,
                            "title": a.title, "detail": a.body,
                            "action": a.action, "sources": a.sources}
                           for a in alerts],
                "latency_ms": int((time.time() - t0) * 1000),
                "disclaimer": "Decision support, not diagnosis.",
            }

        # --- chat ---
        if mode == "chat":
            message = payload.get("message")
            if not message:
                return {"error": "chat mode needs a message"}
            from grus_chat import answer
            return answer(conn, hadm_id, message, as_of_hours=as_of,
                          trace_id=payload.get("trace_id"),
                          history=payload.get("history", []))

        # --- the graph ---
        from grus_agents import generate_brief, check_citations
        result = generate_brief(conn, hadm_id, as_of, verbose=False)

        node = result.results.get("composer")
        text = str(node.result) if node else ""
        valid, invalid, pct = check_citations(text)

        return {
            "hadm_id": hadm_id,
            "as_of_hours": as_of,
            "brief": text,
            "agents_run": list(result.results.keys()),
            "trust": {
                "citations_valid": len(valid),
                "citations_invalid": len(invalid),
                "traceable_pct": pct,
                "rejected": sorted(set(invalid))[:10],
            },
            "latency_ms": int((time.time() - t0) * 1000),
            "disclaimer": "Decision support, not diagnosis.",
        }

    except Exception as e:
        # A brief that cannot be generated must not return nothing. Fall
        # back to the rule engine — unformatted facts beat an error page.
        try:
            if conn:
                from grus_rules import evaluate
                alerts = evaluate(conn, hadm_id, as_of, persist=False)
                return {
                    "hadm_id": hadm_id,
                    "degraded": True,
                    "error": str(e)[:200],
                    "message": "Agent graph unavailable. Rule engine only.",
                    "alerts": [{"severity": a.severity, "title": a.title,
                                "detail": a.body, "action": a.action,
                                "sources": a.sources} for a in alerts],
                    "disclaimer": "Decision support, not diagnosis.",
                }
        except Exception:
            pass
        return {"error": str(e)[:300], "hadm_id": hadm_id}

    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    app.run(port=9500)