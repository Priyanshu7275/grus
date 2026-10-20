"""
GRUS Room — voice on the website

The website's "talk to GRUS" button sends what the doctor said here; this
forwards it to the SAME agent the Alexa skill uses (Nova 2 Lite on
Bedrock AgentCore, via the alexa-skill-mcp-bridge), which calls the same
GRUS MCP tools. So the web panel and Alexa give identical, checked
answers, from the same place.

    Browser speech-to-text -> POST /voice/turn -> AgentCore runtime
        -> GRUS MCP server -> answer -> browser speaks it

The browser does speech-to-text and text-to-speech itself (Chrome's
built-in Web Speech API), so no audio passes through the backend.

Settings (Elastic Beanstalk environment properties):
    GRUS_VOICE_RUNTIME_ARN   RuntimeArn output of AlexaMcpBridgeStack
    GRUS_VOICE_REGION        us-east-1 (where the bridge is deployed)

The instance role needs bedrock-agentcore:InvokeAgentRuntime on that
runtime (see the README for the policy).
"""

import hashlib
import json
import os
import re
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["voice"])

# The web can wait longer than Alexa (8 s hard limit), so most answers
# arrive in one call instead of "still working, ask again".
WEB_BUDGET_MS = 20000
MAX_TEXT = 400

_client = None


def _agentcore():
    global _client
    if _client is None:
        import boto3
        _client = boto3.client(
            "bedrock-agentcore",
            region_name=os.environ.get("GRUS_VOICE_REGION", "us-east-1"))
    return _client


def _hash(raw: str) -> str:
    # Same as the bridge's hashId: sha256 hex. 64 chars, which also meets
    # AgentCore's minimum runtime session id length.
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class VoiceTurn(BaseModel):
    text: Optional[str] = Field(None, max_length=MAX_TEXT)
    # One per browser tab/conversation; the panel makes it up.
    session_id: str = Field(..., min_length=8, max_length=80)
    # Ask again for an answer that was still being worked on.
    poll: bool = False
    # When the panel is on a patient's page: their short id ("5843").
    # Added to the request if the doctor didn't name a patient.
    patient: Optional[str] = Field(None, max_length=12)


def _with_patient(text: str, patient: Optional[str]) -> str:
    # Leave it alone if a patient (or all patients) is already named.
    if not patient or re.search(
            r"\bpatients?\b|\b\d{4,}\b|\bboard\b|\beveryone\b|\bdesk\b",
            text, re.I):
        return text
    return f"For patient {patient}: {text}"


@router.post("/voice/turn")
def voice_turn(req: VoiceTurn):
    runtime_arn = os.environ.get("GRUS_VOICE_RUNTIME_ARN", "").strip()
    if not runtime_arn:
        raise HTTPException(503, {"code": "VOICE_NOT_CONFIGURED",
                                  "message": "GRUS_VOICE_RUNTIME_ARN is not set."})
    if not req.poll and not (req.text or "").strip():
        raise HTTPException(400, {"code": "EMPTY", "message": "Say something."})

    actor = _hash("grus-web:" + req.session_id)
    if req.poll:
        turn = {"type": "poll"}
        said = None
    else:
        said = _with_patient(req.text.strip(), req.patient)
        turn = {"type": "turn", "utterance": {"text": said}}

    payload = {
        "turn": turn,
        "actorId": actor,
        "sessionId": _hash("grus-web-session:" + req.session_id),
        "locale": "en-US",
        "budgetMs": WEB_BUDGET_MS,
        "debug": True,
    }

    t0 = time.time()
    try:
        resp = _agentcore().invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=actor,
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(payload).encode("utf-8"),
        )
        body = resp["response"].read()
        out = json.loads(body or b"{}")
    except Exception as e:
        print(f"[grus_voice] agent call failed: {e!r}", flush=True)
        raise HTTPException(502, {"code": "VOICE_AGENT_ERROR",
                                  "message": "The voice agent did not answer."})

    debug = out.get("debug") or {}
    return {
        "status": out.get("status", "error"),      # done | pending | question | error
        "speech": out.get("speech", ""),
        "question": (out.get("question") or {}).get("message"),
        "end_session": out.get("endSession", True),
        "heard": said,
        "tools": [{"name": c.get("name"), "status": c.get("status"),
                   "ms": c.get("elapsedMs")}
                  for c in debug.get("toolCalls", [])],
        "elapsed_ms": int((time.time() - t0) * 1000),
    }