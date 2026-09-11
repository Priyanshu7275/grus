# GRUS — Phase 1 Conclusion

**Data exploration and demo patient selection**
Project: GRUS · AWS Agents for Humans, Professional Track
Dataset: MIMIC-IV v2.1 (hosp + icu modules), explored locally with DuckDB

---

## 1. What Phase 1 Set Out to Do

Find one real patient in MIMIC-IV who matches the GRUS demo scenario — an emergency arrival whose record holds something dangerous that a doctor could not guess from looking at them — and learn enough about the data's structure to design the ETL correctly the first time.

No cloud resources were used. All exploration ran on a laptop, free.

---

## 2. The Selection Funnel

| Stage | Filter | Remaining |
|---|---|---|
| Start | All MIMIC-IV admissions | 431,088 |
| 1 | Anticoagulant prescribed | — |
| 2 | Trauma diagnosis, **same admission** | 1,506 |
| 3 | Emergency admission, single ICU stay, 2–12 day hospital stay | 20 shortlisted |
| 4 | **First INR on arrival ≥ 2.0** (genuinely anticoagulated *before* injury) | 25 candidates |
| 5 | Accident cause code present, ≤15 diagnoses (excludes chronically-ill cases) | 23 candidates |
| 6 | Manual review of clinical story | **1 patient** |

### Two rejected candidates, and why

**`10008454` (26F)** — passed every structured filter, but her first INR was **1.1 (normal)**. The warfarin was started *in hospital* as post-trauma prophylaxis. The `V5861` "long-term anticoagulant use" code reflected her status at discharge, not arrival.
→ **Lesson: a diagnosis code is not a timestamp.** Always verify against a lab value from the arrival window.

**`10933609` (53M, INR 4.4)** — had a trauma code, but the diagnosis list was dominated by sepsis, septic shock, kidney failure, and cachexia. A chronically ill patient who happened to fracture a vertebra, not an accident victim.
→ **Lesson: a trauma code present ≠ a trauma admission.** Filter on external cause codes (`E8xx`) and diagnosis count.

---

## 3. The Selected Patient

**`subject_id 19272232` · `hadm_id 28173870` · `stay_id 30004576`**

36-year-old male. Emergency admission. Survived, discharged day 3.

### Why he is the right case

He looks like a healthy young accident victim. Nothing visible suggests bleeding risk. His record says otherwise:

| Code | Meaning |
|---|---|
| V422 | **Heart valve replaced** — why a 36-year-old is anticoagulated |
| V1251 | Prior venous thrombosis — second indication |
| V5861 | Long-term anticoagulant use, confirmed pre-arrival |
| E9342 | **Anticoagulants causing adverse effects** — the hospital coded the warfarin as the cause of harm |
| 4230 | Hemopericardium — bleeding into the sac around the heart |
| 4233 | **Cardiac tamponade** — the heart compressed until it cannot fill |
| 2851 | Acute post-hemorrhagic anemia |
| 4019 | Hypertension |

This is exactly the hidden history GRUS exists to surface.

### Verified clinical timeline

| Time | Event | Source |
|---|---|---|
| 06-04 20:17 | Arrives Emergency Department | transfers |
| 06-04 20:40 | **INR 6.3** (target 2–3), Hgb 8.9, Hct 28.2 | labevents |
| 06-05 00:34 | Transfer to Medicine/Cardiology | transfers |
| 06-05 07:42 | **INR 1.8** — reversal has occurred | labevents |
| 06-05 12:07 | Transfer to CCU | transfers |
| 06-05 13:00 | **Pericardial drain: 1000 ml** | outputevents |
| 06-05 13:07 | ICU vitals monitoring begins | chartevents |
| 06-05 16:00 | Heparin started — valve cannot stay unanticoagulated | prescriptions |
| 06-05 16:21 | Pericardial output 87 ml | outputevents |
| 06-05 18:13 | INR 1.4 | labevents |
| 06-05 22:00 | **Pericardial output 0 ml — bleeding stopped** | outputevents |
| 06-06 11:00 | Transthoracic echo — confirms no reaccumulation | procedureevents |
| 06-06 14:32 | Out of CCU | transfers |
| 06-07 16:35 | Discharged | transfers |

**The causal chain, in numbers:** INR 6.3 → bleeding into pericardium → tamponade → 1000 ml drained → reversal → INR 1.4 → output 0 ml → discharged.

Every step has a source row. Nothing inferred.

### Data density check

| Signal | Coverage |
|---|---|
| Heart rate, respiratory rate | 26 readings / 24h (~hourly) |
| Non-invasive BP (sys/dia/mean) | 24 readings |
| SpO2 | 19 readings |
| GCS (verbal + motor) | 8 readings — neuro status |
| INR | 5 values, 6.3 → 1.4 |
| Hemoglobin / Hematocrit | 4 / 5 values |
| Creatinine | 4 values, 1.1 → 0.8 (kidneys fine) |
| Platelets | 4 values, all normal (~380) |
| Pericardial drain output | 4 readings, 1000 → 0 ml |

Sufficient for an arrival brief and an hourly trend. Not sufficient for minute-by-minute animation — **do not claim continuous streaming in the pitch.**

---

## 4. The Undocumented Reversal — Found, Not Designed

His INR fell **6.3 → 1.8 in eleven hours**. Warfarin's half-life is ~40 hours; this cannot happen unaided. A reversal agent (vitamin K, FFP, or PCC) was certainly given.

**It is not recorded anywhere.** Five tables checked exhaustively:

| Table | Result |
|---|---|
| prescriptions | Not present |
| pharmacy | Not present (40 rows, full table reviewed) |
| emar | **Empty** — MIMIC EMAR coverage starts ~2019 |
| inputevents | Not present |
| ingredientevents | Not present (fluids only) |

The reversal happened on the cardiology ward between 00:34 and 07:42 — a window MIMIC barely covers.

### Why this is the strongest asset of Phase 1

GRUS's differentiating feature is the **Critical Unknowns box** — telling doctors what is *missing and dangerous*, not just what was found. Phase 1 produced a real, verified instance of exactly that, in the demo patient's own record.

Correct GRUS output:

```
⚠️ REVERSAL — UNDOCUMENTED
   INR 6.3 (06-04 20:40) → 1.8 (06-05 07:42)
   Drop inconsistent with warfarin half-life.
   Reversal agent likely given. NOT IN RECORD.
   → ASK: what was given, when, how much?
   [sources: lab #..., lab #...]
```

It reasons from the lab trend that something happened, states plainly that it cannot find what, and asks. A guessing system would have written "vitamin K administered" and been wrong.

Because all five tables were checked, this claim is defensible under questioning.

---

## 5. ETL Findings

Twelve structural facts about MIMIC-IV, each a schema decision that would otherwise have been wrong.

| # | Finding | ETL action |
|---|---|---|
| 1 | Prescriptions are one row per dose period — warfarin appeared 5× for one course | Collapse: group by drug, min(start), max(stop) |
| 2 | Three ID levels: `subject_id` → `hadm_id` → `stay_id`; one admission can hold several ICU stays | Vitals attach to `stay_id`, never `hadm_id` |
| 3 | **Dates are shifted per patient** — same patient shows 2110 and 2145 across tables | Compute `hours_since_admission`; never order by raw timestamp |
| 4 | Drug names are messy: `*nf* warfarin`, `inv-apixaban`, brand names | Normalize at load, not at query time |
| 5 | **Medications span multiple tables** — the single most important drug was in none of them | Never read one med table in isolation |
| 6 | ICU days ≠ hospital days (one candidate: 3 ICU days inside a 72-day stay) | Store both durations |
| 7 | **ED/ward coverage is thin** — ICU tables begin at ICU admission | Demo is the *arrival brief*, not live monitoring |
| 8 | Null values in real fields: 4 null medication names, 1 null careunit | Print `UNKNOWN`; never silently skip |
| 9 | **Corrupt timestamps** — heparin `stoptime` (14:00) precedes `starttime` (16:00) | Validate `stop > start`; flag violations |
| 10 | `pharmacy.status` distinguishes active / discontinued / expired | Never report a med as active without checking status |
| 11 | `outputevents` holds the most striking number in the case (1000 ml drain) | **Essential table** — was nearly overlooked |
| 12 | `procedureevents` missed the pericardiocentesis (done in cath lab, not ICU) | Low value; get procedures from `procedures_icd` |

### Table inventory decision

**Essential:** patients, admissions, transfers, diagnoses_icd, procedures_icd, prescriptions, pharmacy, labevents, icustays, chartevents, inputevents, **outputevents**, plus dictionaries (d_icd_diagnoses, d_icd_procedures, d_labitems, d_items)

**Conditional:** omr (outpatient BP/weight — not yet checked), microbiologyevents (sepsis rules only), services

**Skip:** poe, poe_detail, drgcodes, hcpcsevents, d_hcpcs, ingredientevents, datetimeevents, procedureevents, emar/emar_detail (empty for pre-2019 admissions)

This matters for cost: `chartevents` alone is tens of GB. Uploading tables GRUS never queries is wasted money and time.

---

## 6. Impact on Project Design

**Demo reframed.** The crisis window (arrival → reversal → drain) is largely absent from MIMIC. The demo is therefore the **arrival brief** — GRUS reads the record and produces a sourced, decision-ready page in seconds, versus 15–30 minutes of manual chart review. The hourly replay showing the INR normalising and the alert standing down becomes a secondary feature.

This is a cleaner demo. One strong moment beats a long animation.

**Claims to avoid in the pitch:**
- No "continuous/streaming vitals" — the data is hourly
- No "we would have saved them" framing on any outcome
- No claim the reversal was vitamin K — the record does not say

**Still general-purpose.** Nothing built in Phase 1 is trauma-specific. The output is two integers. The schema has no trauma columns. Anticoagulant + trauma is *one rule* beside sepsis, allergy conflict, eGFR/contrast, and chest pain. Phase 5 will run GRUS on three unrelated patients to demonstrate this.

---

## 7. Outstanding Items

- [ ] **MIMIC-IV-Note v2.2** — DUA signed, download pending. No `discharge.csv` means no vector search.
- [ ] **`omr` table** — outpatient BP/weight, not yet checked. Directly answers "last recorded blood pressure."
- [ ] **Date anchor inconsistency** — `admissions` vs `prescriptions` showed different shifted years for one patient. Resolve before building the Reconciler.
- [ ] MIMIC data still under OneDrive — move to `C:\mimic\` (DUA: no third-party cloud sync)
- [ ] Record dataset version (v2.1) in README for reproducibility

---

## 8. Next: Phase 2

1. Design the Aurora schema from the twelve findings above
2. Write the JSON contract — **this unblocks the frontend**
3. Extract only the needed tables and columns
4. Then upload

**Understand the data → design the schema → then move it.** Moving data before understanding it means moving it twice.
