# GRUS: a voice assistant for the ICU that never says a number it can't trace

**GRUS** is clinical decision support for the emergency department and the resus room. It reads a patient's record and gives the care team checked answers, **on screen and by voice through Alexa**.

> **The rule:** GRUS never shows or speaks a number it cannot trace to the patient's record.

- **Live demo:** https://grus-frontend.vercel.app (demo sign-in details are on the login page)
- **Track:** Alexa+ (self-hosted MCP server)
- **Data:** MIMIC-IV, a public research dataset of de-identified hospital records. No real patient can be identified.
- **Licence:** Apache-2.0

*Decision support, not diagnosis. The clinician always makes the call.*

---

## The problem

An emergency or ICU doctor often has minutes to act on a patient whose record they haven't read. The one fact that changes the plan (a blood thinner, a falling haemoglobin, an allergy) is buried somewhere in it, and finding it takes 15–30 minutes. In the resus room the doctor's hands are busy, so they can't scroll or type at all.

AI summaries can help, but a confident made-up number looks exactly like a real one. **In medicine, a plausible lie is worse than no answer.** GRUS is built so that every number can be traced, and anything that can't be traced is removed before anyone sees or hears it.

---

## What GRUS does

### By voice: GRUS Room (Alexa)

Say *"Alexa, open grus assistant"*, or put *"ask grus assistant …"* before any command. Patients are named by the last four digits of their admission number. You can say *"patient 2254"*, *"twenty two fifty four"* or *"two two five four"*.

| Use case | Example command |
|---|---|
| Who needs attention first | *"Which patients need attention first?"* |
| Vitals | *"What are the current vitals for patient 2254?"* |
| One lab value | *"What was the INR on arrival for patient 5843?"* |
| Alerts | *"Any alerts for patient 2254?"* |
| Any question about the record | *"Is patient 5843 on any blood thinners?"* |
| Clinical score, asked one question at a time | *"Calculate the NEWS2 for patient 5843."* |
| Ask the desk | *"Ask the desk for two units of O negative for patient 2254, stat."* |
| Request status | *"What's the status of my requests?"* |
| Log a note | *"Log for patient 2254: two units O negative started."* |
| Reminder | *"Remind me to recheck the haemoglobin for patient 2254 in twenty minutes."* |
| What happened so far | *"What have we done so far for patient 2254?"* |
| Tamper check | *"Is the record for patient 2254 intact?"* |
| Handover | *"Give me a handover for patient 2254."* |
| Open / close a room | *"Start a room for patient 2254 in resus bay one."* / *"End the room for patient 2254."* |

The **Talk to GRUS** mic on the website uses the same agent.

### On screen: the website

- **Patient board:** every patient ranked by risk, with alerts, missing information and a short spoken ID.
- **Patient dashboard:** heart rate, blood pressure, oxygen and breathing rate, a shock-index gauge and trends. A missing reading says so instead of showing a blank.
- **Sourced brief:** summary, red flags, current state and critical unknowns. Every claim links to its source row (100% traceable).
- **Risk predictions** next to rule-based alerts, and **15+ validated clinical scores** (NEWS2, HEART, PERC, qSOFA, HAS-BLED, CHA₂DS₂-VASc, CURB-65, KDIGO and more).
- **Room record:** a live, tamper-proof timeline of everything said in the room, with a **Record intact** check.
- **Main desk:** the nursing station's live request queue (Acknowledge / Done) and reminders that pop up with a sound.
- **Timeline slider:** see the patient exactly as the record stood at any hour.

---

## How "traceable" works

Every entry in the record has an ID, such as `labs#301742`. GRUS attaches that ID to every number it shows or says. Then **plain code, not AI**:

1. **Value check** (`grus_valuecheck.py`): every number must match the row it cites. Uncited numbers are removed.
2. **Speculation guard:** sentences like "likely due to…" or "may suggest…" are removed.
3. **Spoken check** (`grus_spoken.py`): a spoken sentence may only contain numbers that survived the checks above. Otherwise a rule-built sentence is spoken instead.

On screen, anything removed is shown as `[REMOVED — could not be traced to source data]`, so the clinician knows something was stripped.

---

## Architecture

```
            ┌──────────────┐      ┌──────────────┐
            │ Alexa skill  │      │ Web mic      │
            │ "grus        │      │ (Talk to     │
            │  assistant"  │      │  GRUS)       │
            └──────┬───────┘      └──────┬───────┘
                   │  Lambda (locked     │ POST /voice/turn
                   │  to our skill)      │
                   ▼                     ▼
          ┌─────────────────────────────────────────┐
          │ Bridge agent: Nova 2 Lite on             │
          │ Amazon Bedrock AgentCore                 │
          └───────────────────┬─────────────────────┘
                              │ MCP (Streamable HTTP, bearer token)
                              ▼  via Amazon CloudFront (HTTPS)
   ┌──────────────────────────────────────────────────────────┐
   │ GRUS backend: FastAPI on AWS Elastic Beanstalk            │
   │                                                          │
   │  /mcp        MCP server, 20 tools        (grus_mcp.py)    │
   │  /room/...   room record API             (grus_room_api)  │
   │  /desk/...   desk queue + reminders                       │
   │  /patients/… brief, vitals, risk, scores  (grus_api.py)   │
   │                                                          │
   │  value check · speculation guard · spoken check           │
   │                                                          │
   │  Brief: 5 agents (Strands Agents SDK, Amazon Bedrock)    │
   │   Retriever ─┐                                           │
   │   Reconciler ┼─► Verifier (Nova Pro) ─► Composer         │
   │   Risk ──────┘                                           │
   └──────────────────────────┬───────────────────────────────┘
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │ Aurora PostgreSQL + pgvector                              │
   │  clinical record (MIMIC-IV) · hash-chained room record    │
   └──────────────────────────────────────────────────────────┘

   Website: Next.js 14 on Vercel (grus-frontend/)
   Risk models: Amazon SageMaker · Scenario replay: EventBridge + Lambda · S3
```

### The five brief agents

| Agent | Job |
|---|---|
| **Retriever** | Collects labs, vitals, notes, medications and prior visits, each with its source ID. Doesn't interpret. |
| **Reconciler** | Builds one timeline and flags contradictions (e.g. a stop time before a start time). Cites both sides; doesn't pick one. |
| **Risk** | Reports risk from published rules and from models trained on 20,000 admissions, and shows both if they disagree. |
| **Verifier** | Runs on Amazon Nova Pro. Checks every claim against the source rows → VERIFIED / REJECTED. |
| **Composer** | Writes the brief, copying every value and ID from the verified data. |

Retriever, Reconciler and Risk run in parallel. The other agents run on Qwen 3 32B.

### The room record

`grus_room.py` stores every room action (log, desk request, reminder, score, handover) in an **append-only, hash-chained** table:

```
hash = sha256(previous_hash | admission | seq | kind | actor | created_at | body)
```

Database triggers block `UPDATE`, `DELETE` and `TRUNCATE`. `GET /room/{id}/verify` recomputes the chain and names the first broken entry if anything was changed.

### MCP tools (20)

`triage_board` · `find_patient` · `current_vitals` · `lab_value` · `get_alerts` · `ask_record` · `calculate_score` · `answer_score_question` · `start_room` · `end_room` · `log_event` · `send_request` · `list_requests` · `update_request` · `set_reminder` · `check_reminders` · `query_timeline` · `recall` · `prepare_handover` · `verify_record`

The server implements MCP spec **2025-11-25** over Streamable HTTP (stateless, JSON responses), with a bearer-token and Origin check.

---

## Repository layout

```
grus_api.py          FastAPI app: patients, brief, vitals, risk, scores, chat
grus_mcp.py          MCP server (20 tools), mounted at /mcp
grus_room.py         Hash-chained room record, desk requests, reminders
grus_room_api.py     Website API for the Room and Desk pages
grus_voice.py        /voice/turn: web mic → the same agent Alexa uses
grus_valuecheck.py   Value check + speculation guard
grus_spoken.py       Turns checked text into safe spoken sentences
grus_agents.py       The five-agent brief graph (Strands)
grus_score_engine.py Clinical scores, computed in code
grus_scores.py       Score definitions (15+ validated scores)
grus_rules.py        Rule-based alerts
grus_tools.py        Record lookups used by the agents
grus_config.py       All settings, read from environment variables
setup_room.py        One-time creation of the room tables
test_mcp.py          End-to-end MCP test client
grus-frontend/       Next.js website
```

---

## Run it yourself

### 1. Backend

Requires Python 3.11+, a PostgreSQL database with the GRUS schema (`Grus schema.sql`) and MIMIC-IV data loaded, and AWS credentials with Amazon Bedrock access.

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in your own values (see below)
python setup_room.py          # one time: creates the room tables
uvicorn grus_api:app --reload --port 8000
```

API docs: `http://localhost:8000/docs`

**Environment variables** (never commit real values):

| Variable | What it is |
|---|---|
| `GRUS_DB_HOST`, `GRUS_DB_PORT`, `GRUS_DB_NAME`, `GRUS_DB_USER`, `DB_PASSWORD` | PostgreSQL connection |
| `GRUS_DB_SCHEMA` | Schema name (default `grus`) |
| `AWS_REGION` | Region for Bedrock and the database |
| `GRUS_FAST_MODEL`, `GRUS_REASONING_MODEL` | Bedrock model IDs (defaults: Qwen 3 32B, Nova Pro) |
| `GRUS_MCP_TOKENS` | MCP access tokens, as `token=name,token2=name2` |
| `GRUS_MCP_ALLOWED_ORIGINS` | Optional comma-separated list of allowed origins |
| `GRUS_MCP_DEV=1` | Local testing only: skips the token check |
| `GRUS_ROOM_TZ` | Time zone for room times (default `Asia/Kolkata`) |
| `GRUS_VOICE_RUNTIME_ARN`, `GRUS_VOICE_REGION` | The AgentCore runtime used by the web mic |

### 2. Test the MCP server

```bash
GRUS_MCP_DEV=1 uvicorn grus_api:app --port 8000
python test_mcp.py                        # local
python test_mcp.py https://<your-host>/mcp  # deployed (needs a token)
```

### 3. Connect Alexa

GRUS uses the open-source **[alexa-skill-mcp-bridge](https://github.com/KayLerch/alexa-skill-mcp-bridge)**, which deploys an Alexa skill, a Lambda and an agent on Amazon Bedrock AgentCore that calls any MCP server.

1. Clone the bridge and follow its setup.
2. In `bridge.config.ts`, set:
   - the MCP server URL: `https://<your-host>/mcp`
   - auth: `{ type: 'bearer', secretName: '<your-secret-name>' }`, with the token stored in AWS Secrets Manager
   - the invocation name: `'grus assistant'`
3. Deploy with CDK and the ASK CLI as the bridge's README describes.
4. Add the same token to `GRUS_MCP_TOKENS` on the backend.

The bridge config holds your server URL and secret name, so keep it out of public repos.

### 4. Website

```bash
cd grus-frontend
npm install
cp .env.local.example .env.local   # set NEXT_PUBLIC_API_BASE to your backend URL
npm run dev
```

Open `http://localhost:3000`. If the backend can't be reached, the website falls back to built-in demo data so it still renders.

---

## Built with

Alexa Skills Kit · Amazon Bedrock · Amazon Bedrock AgentCore · Amazon Nova (Nova Pro, Nova 2 Lite) · Qwen 3 32B · Model Context Protocol · Strands Agents SDK · Amazon CloudFront · AWS Lambda · AWS Elastic Beanstalk · Amazon Aurora PostgreSQL + pgvector · Amazon SageMaker · Amazon S3 · Amazon EventBridge · AWS Secrets Manager · Python · FastAPI · Next.js · TypeScript · Tailwind CSS · Vercel · Web Speech API · MIMIC-IV

## Responsible use

- **Decision support, not diagnosis.** GRUS doesn't diagnose or prescribe.
- **Demo data only:** de-identified MIMIC-IV. Not for clinical use.
- **Deployment path:** Amazon ended third-party HIPAA-eligible Alexa skills in December 2022. A hospital deployment would use **Alexa Smart Properties for Healthcare** and comply with local law (e.g. India's DPDP Act 2023) and clinical decision-support rules (CDSCO / FDA).

## Licence

Apache-2.0. See [LICENSE](LICENSE).

