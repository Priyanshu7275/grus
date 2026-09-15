# GRUS — Emergency Medicine Decision Support System

An AI agent that reads a patient's entire medical record and produces a
verified clinical brief in seconds. Every claim traces to a real
database row. Where the record is silent, GRUS says so — instead of
guessing.

Built for the Agents for Humans hackathon with the Strands Agents SDK,
Amazon Bedrock, SageMaker, and Aurora PostgreSQL.

Decision support, not diagnosis.

## Run locally

```
pip install -r requirements.txt
cp .env.example .env        # fill in DB_PASSWORD and the rest
uvicorn grus_api:app --reload --port 8000
```

Docs at `http://localhost:8000/docs`.

## Connect the frontend

Frontend lives in `grus-frontend/`. Set the API base URL:

```
NEXT_PUBLIC_API_URL=<your deployed API URL>
```

Endpoints used: `/patients`, `/patients/{id}/brief`, `/patients/{id}/alerts`,
`/patients/{id}/risk`, `/patients/{id}/scores/{name}`, `/chat`,
`/admissions`.

## Demo login

```
clinician@hospital-x.org  /  grus-demo
```

## Deploy

**Backend** — Elastic Beanstalk, Python 3.11 platform. `Procfile` and
`application.py` are already set up; push the repo and deploy.

**Frontend** — Vercel. Add `NEXT_PUBLIC_API_URL`, deploy.

**AgentCore** — `python grus_agentcore_deploy.py role|ecr|build|create`
builds and registers the containerized agent graph as a managed
runtime.

## Architecture

Five Strands agents run as a directed graph. Three in parallel — a
Retriever (hybrid pgvector + keyword search over clinical notes), a
Reconciler (checks coded data against free text), and a Risk agent
(rule engine plus three SageMaker XGBoost models). All three converge
on a Verifier, which confirms every claim traces to a real source
before anything is shown. Only then does a Composer write the brief.

Four agents run on Qwen 3 32B for fast structured extraction. The
Verifier runs on Amazon Nova Pro, chosen because it bills directly
through AWS rather than through AWS Marketplace, which this account's
payment method didn't support for Anthropic models.

## Data

MIMIC-IV, a real de-identified clinical database. 301 patients loaded
into Aurora PostgreSQL through a purpose-built ETL pipeline; another
20,000 processed separately for model training. Clinical notes are
split by section — not by character count — before embedding, so a
medication list never gets cut mid-entry.

MIMIC-IV is credentialed data under a Data Use Agreement. No raw MIMIC
files are included in this repository.

## Models

Fifteen validated clinical decision scores (PERC, HEART, qSOFA, KDIGO,
and others) are encoded as data and computed deterministically — the
model only selects which one applies.

Seven XGBoost risk models were trained on 20,000 MIMIC-IV admissions
using SageMaker. Three met the shipping bar; four were rejected, with
the reason kept rather than discarded.

### Model accuracy

| Model | Status | AUC | Precision | Recall | Alert rate | Reason |
|---|---|---|---|---|---|---|
| AKI risk (24h) | Shipped | 0.907 | 0.73 | 0.62 | 13% | — |
| Transfusion likelihood (12h) | Shipped | 0.892 | 0.59 | 0.54 | 6% | — |
| Electrolyte crisis (12h) | Shipped | 0.818 | 0.43 | 0.48 | 7% | — |
| Sepsis progression (12h) | Rejected | 0.966 | — | — | — | White cell count carried 48% of the model's weight — restating a current value, not predicting a future one. |
| Respiratory decline (6h) | Rejected | 0.719 | 0.48 | — | 61% | Fired on 61% of all patient-hours. Noise, not signal. |
| Bleeding progression (6h) | Rejected | — | — | — | — | Too rare in the training data (2.9% base rate) to learn reliably. |
| Neurological decline (6h) | Rejected | — | — | — | — | Too rare in the training data (1.9% base rate) to learn reliably. |

Every model is registered in SageMaker Model Registry behind a manual
approval gate — nothing reaches a clinician automatically, including
the three that shipped.

## Files

### Backend — serving

| File | What it does |
|---|---|
| `grus_api.py` | FastAPI backend. Every endpoint the frontend calls. |
| `grus_agents.py` | The five-agent Strands graph — Retriever, Reconciler, Risk, Verifier, Composer. |
| `grus_composer.py` | Assembles the source data block and generates the brief. |
| `grus_verifier.py` | Deterministic pass that strips any claim without a real source. |
| `grus_tools.py` | The tool functions agents call — labs, vitals, meds, notes, history. |
| `grus_retriever.py` | Hybrid retrieval — pgvector semantic search plus keyword matching. |
| `grus_rules.py` | Nine deterministic clinical rules. No model involved. |
| `grus_scores.py` | Fifteen clinical decision scores, encoded as data. |
| `grus_score_engine.py` | Computes a score from the record; reports what's missing rather than guessing. |
| `grus_risk_score.py` | Calls the three SageMaker risk endpoints at inference time. |
| `grus_chat.py` | Grounded chat assistant — answers only from retrieved, cited facts. |
| `grus_note_split.py` | Splits a pasted clinical note by section rather than by character count. |
| `grus_config.py` | Central configuration — database, region, model IDs, all read from the environment. |

### Data pipeline — offline, run once

| File | What it does |
|---|---|
| `grus_etl_tier1.py` | Loads the core 301-patient cohort from MIMIC-IV into Aurora. |
| `grus_etl_prior.py` | Loads prior admissions for patients with more than one visit. |
| `grus_notes_pipeline.py` | Chunks discharge notes by clinical section. |
| `grus_embed.py` | Generates Titan embeddings for note chunks, stored via pgvector. |
| `grus_fix_cohorts.py` | Version-aware cohort reclassification (ICD-9 vs ICD-10 handling). |

### Machine learning

| File | What it does |
|---|---|
| `grus_risk_features.py` | Builds the training feature set — point-in-time correct, no future leakage. |
| `grus_risk_train.py` | Trains and evaluates the seven candidate XGBoost models. |
| `grus_sagemaker.py` | Training jobs, model registry, and serverless endpoint deployment on SageMaker. |
| `grus_scenario_search.py` | Searches MIMIC for a real deterioration trajectory (documents why the live demo uses a labelled synthetic scenario instead). |

### Live data / simulation

| File | What it does |
|---|---|
| `grus_scenario.py` | Defines and replays a synthetic patient scenario locally. |
| `grus_eventbridge_setup.py` | Deploys the EventBridge schedule and Lambda that feed live vitals into Aurora. |
| `lambda_function.py` | The Lambda handler — reads a scenario from S3, writes the next hour of vitals/labs. |

### Deployment

| File | What it does |
|---|---|
| `application.py` | Elastic Beanstalk WSGI/ASGI entrypoint. |
| `Procfile` | Tells Elastic Beanstalk how to start the app (uvicorn). |
| `Dockerfile` | Container build for AgentCore deployment. |
| `grus_agentcore.py` | AgentCore entrypoint wrapping the agent graph. |
| `grus_agentcore_deploy.py` | Builds, pushes, and registers the AgentCore runtime. |
| `requirements.txt` | Python dependencies for the FastAPI backend. |
| `requirements-agentcore.txt` | Python dependencies for the AgentCore container. |

### Utility scripts

| File | What it does |
|---|---|
| `add_prior_history.py` | Manually attaches a prior admission to a patient, for demo purposes. |
| `cleanup_test_patients.py` | Removes synthetic test patients from Aurora. |

### Documentation

| File | What it does |
|---|---|
| `GRUS-Phase1-Conclusion.md` | Write-up of the initial MIMIC-IV data exploration and cohort selection. |
| `GRUS-Project-Reference_2.md` | Running project reference — schema, decisions, file map. |
| `Grus-filesMap.md` | Map of every file in the project and its purpose. |
| `Grus schema.sql` | Full Aurora schema — sixteen tables, no patient data. |

## Known limitations

- Aurora is currently reachable from a broad IP range rather than
  locked to the application's VPC. Scoped access is the next step once
  the backend's own network location is finalised.
- IAM policies on the deployment user are broader than they need to be
  for day-to-day operation; tightening them to the specific resource
  ARNs used is planned but not yet done.
- The database password is passed as a Lambda environment variable
  rather than pulled from Secrets Manager at runtime — a deliberate
  tradeoff to avoid the cost of a VPC interface endpoint for a
  hackathon deployment.
- AgentCore deployment is containerised and registered (status READY)
  but invocation currently returns an error under investigation. The
  primary serving path is the FastAPI backend on Elastic Beanstalk,
  which is fully functional.

## License

Apache 2.0. See `LICENSE`.
