"""
GRUS — Chat

The doctor asks a question. The model answers from stored traces and tool
calls, never from memory.

Two mechanisms keep it honest:

  Traces. Every alert wrote what it saw and where it read it BEFORE any
  text existed. When a doctor asks 'why do you think that', the model is
  reading a stored record back, not composing a justification.

  Tools. Everything else comes from the same fifteen functions the agent
  graph uses. The model chooses which to call; it never writes a query.

The answer carries the source id of every claim, and an explicit list of
what it could not answer. A plausible wrong answer about a patient is
worse than no answer.
"""

import os
import re
import json
import time
from typing import List, Dict, Optional

import boto3
from grus_config import DB, AWS
from sqlalchemy import tuple_

from grus_tools import GrusTools, TOOL_SCHEMAS, dispatch
from grus_rules import evaluate

HOST = DB.HOST
PWD = DB.PASSWORD

REGION = AWS.REGION

# Claude for the chat: follow-ups are open-ended and this is where
# reasoning quality shows. Qwen is the fallback when Claude is
# unavailable — see MODEL_FALLBACK below.
CHAT_MODEL = AWS.REASONING_PREFERRED
MODEL_FALLBACK = AWS.FAST_MODEL
MAX_TOOL_ROUNDS = 6

bedrock = boto3.client("bedrock-runtime", region_name=REGION)


SYSTEM = """You answer an emergency clinician's questions about one patient.

WHAT YOU ARE

Not a diagnostician. You report what the record contains, what the rules
found, and what is missing. The clinician decides.

HOW TO ANSWER

Call tools. Never answer from memory or from what seems likely for a
patient like this one. If you did not retrieve it, you do not know it.

Start with get_active_alerts when the question is about risk — those
alerts were produced by deterministic rules that recorded exactly what
they saw. Explaining one means reading its inputs back, not inventing a
rationale.

Use list_available_labs when unsure of a name. MIMIC writes 'INR(PT)'
not 'INR', 'Urea Nitrogen' not 'BUN'.

For drug names, pass keywords to search_notes. Embeddings map drug names
poorly — a passage naming vitamin K scored 0.185 on semantic search while
a keyword match found it at once.

CITATIONS

Every clinical claim ends with its source: [labs#301694]. Several:
[labs#301694, vitals#492675]. Copy ids exactly as the tools return them.

A tool name is not a source. Never write [get_active_alerts], [unknown],
or [no source]. When a statement has no source, write it with no
brackets at all.

WHAT YOU DO NOT DO

Do not recommend a diagnosis. Do not give a drug or a dose. You may
repeat an action a rule already stated.

Do not fill a gap with something plausible. If a tool returns found:
false, that is the answer — say what is missing and what to ask.

  Good: "No allergy record exists for this patient. That is not the same
         as no allergies — ask the patient before prescribing."
  Bad:  "No known drug allergies."

TONE

Terse. Clinical register. A registrar reads this between patients.
Answer the question asked; do not summarise the whole chart unless asked.
Times as hours since arrival, never dates."""


def _trace_context(conn, hadm_id, trace_id):
    """
    The stored reasoning behind one alert.

    This is what makes 'why do you think that' answerable rather than
    re-derivable: the rule recorded its inputs and sources before any
    text was written, so the model narrates a record instead of
    constructing an argument.
    """
    row = conn.execute("""
        SELECT alert_code, severity, inputs, source_row_ids, rule_version,
               fired_at
        FROM alert_traces WHERE trace_id = %s AND hadm_id = %s
    """, (trace_id, hadm_id)).fetchone()
    if not row:
        return None

    if isinstance(row, dict):
        code, sev, inputs, srcs, ver = (row["alert_code"], row["severity"],
                                        row["inputs"], row["source_row_ids"],
                                        row["rule_version"])
    else:
        code, sev, inputs, srcs, ver = row[0], row[1], row[2], row[3], row[4]

    tags = ", ".join(f"{s['table']}#{s['id']}" for s in (srcs or []))
    return (f"\nThe clinician is asking about this alert. It was produced "
            f"by rule '{code}' (version {ver}), severity {sev}.\n"
            f"The rule saw exactly these values: {json.dumps(inputs)}\n"
            f"It read them from: [{tags}]\n"
            f"Explain from these recorded inputs. Do not construct a "
            f"different rationale.\n")


def _patient_context(conn, hadm_id, as_of_hours):
    """A short orienting header so the model does not waste a round."""
    row = conn.execute("""
        SELECT p.anchor_age AS anchor_age, p.gender AS gender,
               a.admission_type AS admission_type,
               a.arrival_unit AS arrival_unit, a.cohort AS cohort,
               (SELECT COUNT(*) FROM admissions pa
                 WHERE pa.subject_id = a.subject_id AND pa.is_current = FALSE)
                 AS prior
        FROM admissions a JOIN patients p ON p.subject_id = a.subject_id
        WHERE a.hadm_id = %s
    """, (hadm_id,)).fetchone()

    if not row:
        return ""

    g = row if isinstance(row, dict) else {
        "anchor_age": row[0], "gender": row[1], "admission_type": row[2],
        "arrival_unit": row[3], "cohort": row[4], "prior": row[5]}

    window = ("the full record" if as_of_hours is None
              else f"only what was known {as_of_hours}h after arrival")

    return (f"Patient: {g['anchor_age']}{g['gender']}, "
            f"{g['admission_type']}, arrived via {g['arrival_unit']}. "
            f"{g['prior']} prior admissions.\n"
            f"You are looking at {window}. Nothing recorded after that "
            f"point exists for this conversation.\n")


def _to_bedrock_tools():
    return {"tools": [{"toolSpec": {
        "name": t["name"],
        "description": t["description"],
        "inputSchema": {"json": t["input_schema"]},
    }} for t in TOOL_SCHEMAS]}


def _extract_sources(text):
    return sorted(set(re.findall(r"([a-z_]+#\d+)", text or "")))


def answer(conn, hadm_id: int, message: str,
           as_of_hours: Optional[float] = None,
           trace_id: Optional[int] = None,
           history: List[Dict[str, str]] = None,
           model_id: str = None):
    """
    One turn of conversation.

    Returns the answer, its sources, what it could not answer, and which
    tools were called — the last so a doctor can see how a conclusion was
    reached rather than trusting it.
    """
    t0 = time.time()
    try:
        conn.rollback()
    except Exception:
        pass
    tools = GrusTools(conn, hadm_id, as_of_hours)

    context = _patient_context(conn, hadm_id, as_of_hours)
    if trace_id:
        tc = _trace_context(conn, hadm_id, trace_id)
        if tc:
            context += tc

    messages = []
    for h in (history or [])[-8:]:          # keep the window small
        role = h.get("role")
        if role in ("user", "assistant") and h.get("content"):
            messages.append({"role": role,
                             "content": [{"text": h["content"]}]})
    messages.append({"role": "user",
                     "content": [{"text": f"{context}\nQuestion: {message}"}]})

    model = model_id or CHAT_MODEL
    calls, sources_seen = [], []

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            r = bedrock.converse(
                modelId=model,
                system=[{"text": SYSTEM}],
                messages=messages,
                toolConfig=_to_bedrock_tools(),
                inferenceConfig={"maxTokens": 1500, "temperature": 0.2},
            )
        except Exception as e:
            # Claude sits behind AWS Marketplace, which needs a card on
            # file. Qwen does not. Fall back rather than fail.
            if model != MODEL_FALLBACK:
                model = MODEL_FALLBACK
                continue
            raise RuntimeError(f"chat model unavailable: {e}")

        out = r["output"]["message"]
        messages.append(out)

        blocks = out.get("content", [])
        tool_uses = [b["toolUse"] for b in blocks if "toolUse" in b]

        if not tool_uses:
            text = "".join(b.get("text", "") for b in blocks).strip()
            text = re.sub(r"<think>.*?</think>", "", text,
                          flags=re.DOTALL).strip()

            cited = _extract_sources(text)
            valid = [c for c in cited if c in sources_seen]
            invented = [c for c in cited if c not in sources_seen]

            # Strip any citation the tools never returned. A fabricated
            # id looks verified and is not.
            for bad in invented:
                text = text.replace(f"[{bad}]", "").replace(bad, "")
            text = re.sub(r"\[\s*[,\s]*\]", "", text)
            text = re.sub(r"\[(no source|unknown|n/?a)\]", "", text,
                          flags=re.I)

            abstained = [
                {"claim": c["tool"], "reason": "no data on file"}
                for c in calls if not c["found"]]

            return {
                "answer": text,
                "sources": valid,
                "abstained": abstained,
                "tools_called": calls,
                "retrieval": {
                    "tool_calls": len(calls),
                    "sources_available": len(sources_seen),
                    "sources_cited": len(valid),
                    "citations_removed": len(invented),
                },
                "model": model,
                "trace_id": trace_id,
                "as_of_hours": as_of_hours,
                "latency_ms": int((time.time() - t0) * 1000),
                "disclaimer": "Decision support, not diagnosis.",
            }

        results = []
        for tu in tool_uses:
            res = dispatch(tools, tu["name"], tu.get("input") or {})
            calls.append({"tool": tu["name"], "args": tu.get("input") or {},
                          "found": res.found})
            for s in res.sources:
                tag = f"{s['table']}#{s['id']}"
                if tag not in sources_seen:
                    sources_seen.append(tag)
            results.append({"toolResult": {
                "toolUseId": tu["toolUseId"],
                "content": [{"json": res.to_dict()}],
            }})

        messages.append({"role": "user", "content": results})

    return {
        "answer": "I could not resolve that within the retrieval budget. "
                  "Try narrowing the question.",
        "sources": [], "abstained": [], "tools_called": calls,
        "model": model, "as_of_hours": as_of_hours,
        "latency_ms": int((time.time() - t0) * 1000),
        "disclaimer": "Decision support, not diagnosis.",
    }


def suggested_questions(conn, hadm_id, as_of_hours=None):
    """
    Questions worth asking about this patient, from what the rules found.

    A blank chat box is a poor interface for someone with ninety seconds.
    """
    alerts = evaluate(conn, hadm_id, as_of_hours, persist=False)
    qs = []

    for a in alerts[:3]:
        if a.severity in ("critical", "warning"):
            qs.append(f"Why did you flag {a.title.split(' - ')[0].lower()}?")
        elif a.severity == "unknown":
            qs.append(f"What is missing for {a.title.split(' - ')[0].lower()}?")

    qs += ["What is he taking at home?",
           "Any history of bleeding?",
           "What is missing from this record?"]

    seen, out = set(), []
    for q in qs:
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:6]


if __name__ == "__main__":
    import psycopg
    from psycopg.rows import tuple_row

    HOST = "grus-db.cluster-ch02wk02ky83.ap-south-1.rds.amazonaws.com"
    conn = psycopg.connect(
        host=HOST, port=5432, dbname="grus", user="grusadmin",
        password=os.getenv("DB_PASSWORD"), sslmode="require",
        keepalives=1, keepalives_idle=30, row_factory=tuple_row)
    conn.rollback()
    conn.execute("SET search_path TO grus, public")
    conn.commit()

    HADM = 28173870
    print("suggested:", suggested_questions(conn, HADM, 1), "\n")

    for q in [
        "Why do you think he's anticoagulated?",
        "Can I give him ibuprofen?",
        "What was his last creatinine?",
        "Does he have any allergies?",
        "What's his magnesium doing?",
    ]:
        print("=" * 70)
        print(f"Q: {q}")
        print("=" * 70)
        r = answer(conn, HADM, q, as_of_hours=1)
        print(r["answer"])
        print(f"\n  tools: {[c['tool'] for c in r['tools_called']]}")
        print(f"  sources: {r['sources']}")
        if r.get("abstained"):
            print(f"  abstained: {r['abstained']}")
        print(f"  {r['latency_ms']}ms via {r['model'].split('.')[-1]}\n")

    conn.close()
    