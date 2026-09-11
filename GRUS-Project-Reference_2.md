python -m ipykernel install --user --name=grus-env --display-name="GRUS"
# GRUS — Project Reference Document

**Emergency medicine agent that reads the patient's full record and hands the doctor a decision-ready brief before they ask.**

Hackathon: AWS Agents for Humans (Devpost) · Track: **Professional Agents**
Status: Planning locked · Version 1.0

---

## 1. What GRUS Is

When a patient hits the ER, the doctor loses 15–30 minutes chasing records. Is this person on blood thinners? Any allergies? What was their kidney function? Those minutes matter most in exactly the cases where you have the least time.

GRUS fires automatically on patient registration. In under 10 seconds it produces a one-page brief: active problems, meds, allergies, last vitals with dates, red flags, and — the part nobody else builds — **a list of what is dangerously missing from the record**.

It is not a chatbot. The chat is secondary, for follow-up questions like "why do you think that?"

### The three rules that define GRUS

1. **Every claim carries a source row ID.** No source → it prints `UNKNOWN`, never guesses.
2. **Rules and ML decide *what*. The LLM only explains *why*.** The language model never diagnoses.
3. **Patient data never leaves the VPC.** The model comes to the data, not the reverse.

### Positioning

**Decision support, not diagnosis.** This banner appears on every screen. A tool that tells an ER doctor what's in the chart is defensible. A tool that appears to tell them what to do invites regulatory questions we do not want in a 5-minute pitch.

---

## 2. The Hero Output

What appears on screen 8 seconds after a trauma patient registers:

```
TRAUMA BRIEF — 34M, RTA, arrived 14:02

⛔ BLEEDING RISK — HIGH
   On Apixaban 5mg BD (last filled 2026-08-02)
   → Head CT priority. Consider andexanet.
   [source: prescription #44192]

⛔ ALLERGY — Morphine (anaphylaxis, 2019)
   → Do NOT give. Use fentanyl.
   [source: allergy record #881]

⚠️ KIDNEY — eGFR 41 (2026-06-14)
   → Contrast CT risk. Hydrate first.
   [source: lab #77210]

VITALS (live, 14:06)
   HR 128 ↑↑   BP 88/54 ↓↓   SpO2 94% ↓
   Shock Index 1.45 → HAEMORRHAGIC SHOCK LIKELY
   Trend: BP falling 6 pts / 5 min

BLOOD
   Grp B+ (2023) · Hb 9.1 pre-existing · Plt 210

❓ CRITICAL UNKNOWNS
   Last meal — ASK (intubation risk)
   Tetanus status — no record
```

Every line is: a fact + what it means for treatment + where it came from. No diagnosis anywhere.

### The reasoning chat

Doctor taps the shock alert:

> **Doc:** why haemorrhagic shock?
>
> **GRUS:** Three signals, 6-min window:
> 1. Shock Index 1.45 (HR/BP). Above 0.9 = concern, above 1.3 = strong.
> 2. BP dropped 104→88 systolic while HR rose 96→128. Compensating for volume loss.
> 3. Patient is anticoagulated → bleeds more, clots less.
>
> Against it: no external bleeding logged yet. Internal bleed would fit better.
> Confidence: 0.78. Not a diagnosis — FAST scan confirms or rules out.

**How this is built:** every alert writes a **trace** (rule fired, inputs used, model score) *before* any text is generated. The chat reads the trace back in plain words. The model cannot invent a reason because the reason was recorded first.

---

## 3. Architecture — 9 Parts

### Part 1 — Data Layer
Where the record lives. Never leaves.

| What | Service |
|---|---|
| Patient records, labs, meds, notes | Aurora PostgreSQL |
| Semantic search over notes | pgvector (inside Aurora) |
| Raw MIMIC files, embeddings | S3 |
| MIMIC → our schema | AWS Glue |
| Encryption keys | KMS |
| No-internet network | VPC private subnet |

### Part 2 — Live Vitals Layer
Watches the patient right now.

| What | Service |
|---|---|
| Monitor readings streaming in | Kinesis |
| Recalculate scores per reading | Lambda |
| Recent vitals, fast lookup | DynamoDB |
| Wake the agent on change | EventBridge |

**Alert rule: fire on change, not on state.** A BP of 88 held for an hour is not news. 104→88 in five minutes is.

### Part 3 — The Brain (3 stacked layers)

**3a. Rules — deterministic, zero hallucination**
Real clinical protocols as plain Python: NEWS2, Shock Index, sepsis criteria, Wells score, HEART score, allergy–drug conflict, eGFR vs. contrast, anticoagulant + head trauma.

**3b. ML models — trained on MIMIC**
- Deterioration within next 6 hours
- Cardiac event risk
- SHAP output for "why"

| What | Service |
|---|---|
| Train | SageMaker Training |
| Serve | SageMaker Endpoint |
| Fairness checks | SageMaker Clarify |
| Version + approve | SageMaker Model Registry |

**3c. The LLM — writes explanations only**

**Two models, two jobs.** The LLM never reasons from scratch — it reads a trace that rules and ML already produced. That's a constrained writing task for the brief, and an open-ended one for the chat. Different jobs, different models.

| Job | Model | Why |
|---|---|---|
| **Brief generation** | Qwen (Bedrock) | Runs on every patient, fixed template, high volume. Cheap, fast, and **fine-tunable** on MIMIC discharge summaries for clinical shorthand. |
| **Doctor chat** | Claude Opus (Bedrock) | Follow-ups are open-ended and need real reasoning. Low volume, so cost is fine. This is the feature judges will poke at live. |
| Dev + synthetic data (NO PHI) | Fireworks API | Fast iteration without touching PHI |

Both production models run **natively on Bedrock, inside the VPC** — legal for MIMIC, no self-hosting burden. Qwen can also be deployed to a private SageMaker endpoint if we want the fully-offline story.

**Why not Opus for both:** cost at scale, no fine-tuning, and we lose the "runs offline on a hospital's own hardware" pitch line.
**Why not Qwen for both:** the chat is the showcase feature; a 7B model wobbles on hard follow-ups.

Pluggable model layer — one config line switches any of them. With two models actually in production, this is a real architectural decision, not a claim.

**Gemini is not an option.** Google does not ship Gemini on AWS (Vertex AI only). Their open Gemma models are on Bedrock Marketplace, but Gemma ≠ Gemini. Dropped from the plan.

### Part 4 — The Agents (Strands Agents SDK)

Five specialists in a graph. Agents 1–3 run in parallel → 4 → 5.

| # | Agent | Job |
|---|---|---|
| 1 | **Retriever** | Pulls record. SQL for numbers, vector for notes. |
| 2 | **Reconciler** | Builds one timeline, flags contradictions. |
| 3 | **Risk** | Calls ML models, returns scores + SHAP. |
| 4 | **Verifier** | Kills any claim without a source row. Runs rule engine. |
| 5 | **Composer** | Writes the brief in fixed sections. |

| What | Service |
|---|---|
| Host the agent graph | Bedrock AgentCore |
| Audit every retrieval | Aurora audit table |

### Part 5 — Retrieval Design (RAG)

**Hybrid, and the split is non-negotiable:**

| Vector search (pgvector) | Plain SQL |
|---|---|
| Discharge summaries | Last recorded BP |
| Radiology + nursing notes | Current med list |
| Past surgical history | Allergies |
| "any history of bleeding disorder?" | Lab values |

Never let the vector index answer "what is the latest creatinine." Embeddings lose exact numbers and ordering — that is how you get a wrong lab value on stage.

**Pipeline:**
- **Chunk by clinical section** (HPI, PMH, Meds on Discharge), never fixed token count — fixed chunking splits a med list in half
- **Metadata on every chunk:** patient_id, note_id, note_type, date, section. Filter before searching.
- **Embeddings:** BGE or PubMedBERT-based, self-hosted. General embedders miss "SOB", "AF", clinical shorthand.
- **Two-stage:** top 40 by vector → cross-encoder rerank → top 8 to the LLM
- **Recency decay:** score = similarity × time weight. Old notes must earn their place.
- **Chunk IDs travel all the way to output.** This is what makes the Verifier possible.

**Memory layers (separate from RAG):**
1. *Patient memory* — compressed structured summary per patient, rebuilt nightly. Uses vector search.
2. *Institutional memory* — patterns across patients. This is the ML models.
3. *Session memory* — what the doctor already asked. Just state.

### Part 6 — Frontend

Next.js + Tailwind on Vercel.

Screens: gated login → patient board → **brief (hero)** → source drawer → risk/SHAP → live vitals → chat → governance page.

Visual direction: dark, dense, monospace numbers, red/amber/grey status. It must look like clinical software, not a SaaS landing page.

| What | Service |
|---|---|
| Login (3-step gate) | Cognito |
| API | API Gateway + Lambda |
| Live push to screen | AppSync / WebSocket |

### Part 7 — Self-Improvement Loop

Nightly:
1. Pull the day's admissions + actual outcomes
2. Train a **challenger** model
3. Test against a locked holdout — accuracy, calibration, subgroup fairness
4. Beats champion on **all** gates → appears as "pending approval" on dashboard
5. Human clicks Approve → goes live

Also log every alert as **acted-on** or **dismissed**. Dismissals show where we're crying wolf.

| What | Service |
|---|---|
| Nightly trigger | EventBridge |
| Orchestrate | Step Functions |
| Retrain | SageMaker Pipelines |
| Gate + approve | SageMaker Model Registry |

**Why not live self-retraining:** uncontrolled online learning on mortality data drifts silently, creates feedback loops, and has no audit trail. Champion/challenger with human promotion gets the same "it grows every day" story and scores far higher with judges.

### Part 8 — Safety & Proof

- Every claim carries a source row ID
- No source → prints `UNKNOWN`
- Immutable audit log: who read what, when
- Break-glass emergency access — allowed, logged loudly, never blocked
- **Trust panel:** live counters for claims made / sourced / abstained
- Zero PHI egress — no external API touches patient data
- "Decision support, not diagnosis" banner on every screen

### Part 9 — Deliverables

- Public repo, **Apache-2.0**, license visible in About section
- README
- Architecture diagram
- Demo video, ≤5 minutes, on YouTube
- Live demo link (open MIMIC demo subset only)
- AWS Builder ID
- 3 × builder.aws.com blog posts (0.2 each, +0.6 max)

---

## 4. Data & Legal — Read Before Writing Code

**MIMIC-IV is credentialed data under a PhysioNet DUA.**

| Rule | Meaning |
|---|---|
| Cannot go in a public repo | No sample data files committed, ever |
| Cannot go to a third-party API | This is *why* we self-host Qwen — make it a pitch point |
| Full MIMIC-IV | Private training + benchmarking only |
| **MIMIC-IV Clinical Demo (100 patients, open)** | Everything public: repo, video, live demo |

State this split explicitly in the README. Judges notice.

**Fireworks API:** synthetic patients and offline eval only. No PHI. Ever.

---

## 5. Design Principles

| Principle | Why |
|---|---|
| Abstention over confidence | "UNKNOWN — ask patient" beats a plausible guess. The refusal moment is the best demo beat we have. |
| Fire on change, not state | Alert fatigue kills real clinical tools |
| Rules before ML before LLM | Each layer is more capable and less trustworthy than the one before it |
| Trace before text | Reasons are recorded, then narrated — never invented |
| Timestamps on everything | A BP from 2021 is not a BP |
| Inverted output | Everyone shows what they found. We show what's missing and dangerous. |

### Clinical traps to avoid

**Do not say "high glucose = diabetic."** In a trauma patient that is usually stress hyperglycemia — the body dumping sugar in response to injury. Correct output:

> Glucose 310 (now). HbA1c 5.4% (2024-11) → normal. Likely **stress hyperglycemia**, not diabetes. Still treat the sugar. Confidence: moderate.

Showing GRUS knows this difference is what makes it look like a real clinician.

**Do not claim "never hallucinates."** No model guarantees that, and a judge will break it in 30 seconds. Claim **enforced abstention** instead — a property we can prove live.

---

## 6. Team Split

### Buddy — Agents + ML
- Strands agent graph (all five agents)
- Model router (Qwen for briefs, Opus for chat, Fireworks for dev)
- Qwen fine-tune (LoRA on clinical text)
- Risk models + SHAP
- Rule engine
- Retrieval pipeline (chunking, embeddings, reranking)
- Nightly retraining pipeline
- Aurora schema + Glue ETL

### Friend — Frontend
- Next.js + Tailwind, deployed on Vercel
- All screens listed in Part 6
- Cognito three-step gated login
- Live vitals via WebSocket
- **Unblocked from day one** by a mocked JSON contract — never waiting on the backend

### Claude — Docs + Pitch
- Architecture diagram
- README
- Pitch script + video storyboard
- Three builder.aws blog posts
- Review passes on clinical safety framing

---

## 7. Roadmap

### Phase 0 — Foundation
- AWS account, VPC, private subnets, KMS, IAM
- PhysioNet credentialing confirmed for full MIMIC-IV
- Aurora PostgreSQL + pgvector enabled
- Repo created, Apache-2.0 license in About
- **Lock the JSON contract** — frontend starts immediately after this

### Phase 1 — Data
- Glue ETL: MIMIC → our schema
- Section-aware chunking
- Embeddings generated, pgvector index built
- Hybrid retrieval working: SQL for numbers, vector for notes
- Reranker in place

### Phase 2 — Brain
- Rule engine: Shock Index, NEWS2, allergy conflict, eGFR/contrast, anticoagulant+trauma
- Risk models trained on MIMIC, SHAP wired
- Qwen enabled on Bedrock (brief generation) + LoRA fine-tune on MIMIC discharge summaries
- Claude Opus enabled on Bedrock (doctor chat)
- Model router: one config line switches Qwen / Opus / Fireworks
- Fireworks path working for dev

### Phase 3 — Agents
- All five Strands agents built
- Parallel graph: 1–3 → 4 → 5
- Verifier proven: inject an unsourced claim, confirm it gets killed
- Trace written before every alert
- Audit table logging every retrieval

### Phase 4 — Live Vitals
- Kinesis stream + Lambda scoring
- DynamoDB recent-vitals table
- EventBridge wake-on-change
- Trend detection (direction flip, not just threshold)

### Phase 5 — Frontend Integration
- Real API replaces mocks
- Brief screen, source drawer, SHAP view, chat, governance page
- WebSocket live vitals
- Trust panel counters live

### Phase 6 — Self-Improvement
- Step Functions nightly pipeline
- Champion/challenger with all gates
- Model Registry approval flow
- Alert acted/dismissed logging

### Phase 7 — Polish & Submit
- AgentCore deployment
- Live demo on open MIMIC demo subset
- Architecture diagram finalized
- Demo video recorded (≤5 min)
- README complete
- Three blog posts published on builder.aws.com

### Scope-cut order, if time gets tight
Cut from the bottom up:
1. Phase 4 (live vitals) — most impressive, most work
2. Phase 6 (self-improvement) — but this is our biggest differentiator, cut reluctantly
3. Never cut: Phases 1, 2, 3, and Part 8 (safety)

---

## 8. Judging Criteria Mapping

| Criterion | Our answer |
|---|---|
| Technical Implementation | 5-agent Strands graph, hybrid RAG, dual-model router (Qwen + Opus on Bedrock), AgentCore deploy, live demo link |
| Design | Complete clinical product — login, board, brief, drill-down, chat, governance |
| Potential Impact | Quantified: 15–30 min records chase → 8 seconds. Specific audience: ER clinicians. |
| Creativity & Originality | Inverted output (Critical Unknowns), enforced abstention, trace-before-text |
| Presentation | Demo video anchored on one trauma case, end to end |
| Bonus (+0.6) | Three builder.aws posts |

---

## 9. Open Decisions

- [ ] Demo case to anchor on: road accident + anticoagulant / chest pain unknown history / unconscious unidentified
- [ ] Aurora schema — to be written next
- [ ] JSON contract — blocks frontend, needed first
- [ ] Confirm "GRUS" name is final (reads close to GRU, the RNN)
- [ ] Qwen size for brief generation: 7B vs 14B
- [ ] Bedrock-native Qwen vs. private SageMaker endpoint (affects the "fully offline" pitch line)
