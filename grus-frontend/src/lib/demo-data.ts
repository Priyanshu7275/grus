// Demo-data fallback layer (the Sammy pattern, carried over to GRUS).
//
// The frontend is built against the REAL backend contract — types.ts and
// api.ts mirror grus_api.py's actual response shapes, verified against the
// live source at github.com/Priyanshu7275/grus, not guessed. This file
// supplies realistic stand-in data in that exact shape, grounded in the
// backend's real schema (MIMIC-style labs/vitals/diagnoses tables, the
// real vital codes and normal ranges from NORMAL_RANGE in grus_api.py, the
// real 15 scores and their real inputs from grus_scores.py), so that when
// no backend is reachable — no AWS credentials, no Aurora connection, which
// is most of the time on a laptop — the app still looks and behaves like a
// finished product instead of an empty shell full of red error banners.
//
// api.ts tries the live backend first, with a short timeout. Only on
// failure (network error, timeout, non-OK response, or a response that
// doesn't have the shape the UI expects) does it silently fall back to
// this data. Swapping in the real backend later needs no frontend changes
// — the same functions just start returning live data instead.
//
// Nothing here is a real patient record.

import {
  AdmissionResponse,
  AlertItem,
  AlertsResponse,
  BriefResponse,
  ChatResponse,
  DerivedSeries,
  GovernanceResponse,
  HistoryResponse,
  PatientCard,
  PatientsResponse,
  PipelineStatus,
  QuestionsResponse,
  RiskResponse,
  ScoreComponents,
  ScoreResult,
  ScoresListResponse,
  ScoreSummary,
  SourceResponse,
  TrustResponse,
  VitalSeries,
  VitalsResponse,
} from "./types";
import { SCORE_INPUT_SPECS, SCORE_META } from "./scoreSpecs";

// ------------------------------------------------------------------
// Deterministic pseudo-random helpers (same trick as Sammy's demo data —
// a scattered, non-repeating float in [0,1) from an integer seed, so
// re-renders and repeat visits are stable).
// ------------------------------------------------------------------
function hashFloat(n: number): number {
  const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
}
function pick<T>(arr: T[], seed: number): T {
  return arr[Math.floor(hashFloat(seed) * arr.length) % arr.length];
}

// ------------------------------------------------------------------
// Source rows — every [table#id] citation used in a hero brief below
// resolves here, so clicking a citation in demo mode always opens a real
// (illustrative) row instead of a 404. Anything cited that wasn't
// hand-authored still resolves, via the generic fallback at the bottom.
// ------------------------------------------------------------------
interface DemoSourceRow {
  row: Record<string, unknown>;
  context?: string;
  highlight?: string[];
}
const SOURCES: Record<string, DemoSourceRow> = {};

function reg(table: string, id: number, row: Record<string, unknown>, context?: string, highlight?: string[]) {
  SOURCES[`${table}#${id}`] = { row, context, highlight };
}

// ------------------------------------------------------------------
// Vitals builder
// ------------------------------------------------------------------
const NORMAL_RANGE: Record<string, [number, number]> = {
  hr: [60, 100],
  sbp: [90, 140],
  dbp: [60, 90],
  map: [70, 100],
  spo2: [95, 100],
  rr: [12, 20],
  temp_c: [36.1, 37.5],
};
const VITAL_LABELS: Record<string, string> = {
  hr: "Heart rate",
  sbp: "Systolic BP",
  dbp: "Diastolic BP",
  map: "MAP",
  spo2: "SpO2",
  rr: "Respiratory rate",
  temp_c: "Temperature",
};
const VITAL_UNITS: Record<string, string> = {
  hr: "bpm",
  sbp: "mmHg",
  dbp: "mmHg",
  map: "mmHg",
  spo2: "%",
  rr: "/min",
  temp_c: "°C",
};

function buildVitals(hadmId: number, cfg: Record<string, [number, number][]>): VitalsResponse {
  const series: VitalSeries[] = Object.entries(cfg).map(([code, points]) => ({
    code,
    label: VITAL_LABELS[code] || code,
    unit: VITAL_UNITS[code] || null,
    normal_range: NORMAL_RANGE[code] || null,
    points: points.map(([hours, value]) => ({ hours, value, age_hours: Math.max(0, -hours) })),
  }));

  const derived: DerivedSeries[] = [];
  const hr = Object.fromEntries((cfg.hr || []).map(([h, v]) => [h, v]));
  const sbp = Object.fromEntries((cfg.sbp || []).map(([h, v]) => [h, v]));
  const shared = Object.keys(hr)
    .map(Number)
    .filter((h) => sbp[h] !== undefined)
    .sort((a, b) => a - b);
  if (shared.length) {
    derived.push({
      code: "shock_index",
      label: "Shock Index",
      thresholds: { concern: 0.9, severe: 1.3 },
      points: shared.map((h) => ({ hours: h, value: Math.round((hr[h] / sbp[h]) * 1000) / 1000 })),
    });
  }

  return {
    hadm_id: hadmId,
    sampling: "hourly",
    sampling_note: "ICU charting is hourly, not continuous. Emergency department vitals are often absent entirely.",
    series,
    derived,
  };
}

// ------------------------------------------------------------------
// Hero patients — fully hand-authored, one per common ED presentation,
// each mapped to the real clinical scores that presentation suggests.
// ------------------------------------------------------------------
interface HeroPatient {
  card: PatientCard;
  brief: string;
  alerts: AlertItem[];
  risk: RiskResponse;
  vitals: VitalsResponse;
  history?: HistoryResponse;
  suggestedScores: string[];
  scoreResults?: Record<string, Partial<ScoreResult>>;
}

const HERO: Record<number, HeroPatient> = {};

function addHero(h: HeroPatient) {
  HERO[h.card.hadm_id] = h;
}

// ---- 1. Chest pain — 68F, rising troponin, HEART-eligible ----
reg("labs", 301691, { label: "Troponin T", valuenum: 0.09, valueuom: "ng/mL", hours_since_admit: 0.4 }, "First troponin, drawn on arrival.");
reg("labs", 301692, { label: "Troponin T", valuenum: 0.31, valueuom: "ng/mL", hours_since_admit: 3.4 }, "Repeat troponin, 3 hours later — rising.");
reg("vitals", 401691, { vital_code: "hr", label: "Heart rate", valuenum: 104, valueuom: "bpm", hours_since_admit: 0.5 });
reg("note_chunks", 501691, {
  section: "HPI",
  note_type: "ED Provider Note",
  hours_since_admit: 0.3,
  text: "68-year-old female presents with 2 hours of substernal chest pressure radiating to the left arm, associated with diaphoresis. No prior cardiac history documented.",
}, "History of present illness, ED note.", ["substernal chest pressure", "diaphoresis"]);
addHero({
  card: {
    hadm_id: 29817364,
    subject_id: 15234891,
    age: 68,
    gender: "F",
    cohort: "chest-pain",
    hours_since_arrival: 3.4,
    risk_level: "critical",
    risk_score: 0.78,
    alert_count: 2,
    unknown_count: 1,
    headline: "Rising troponin with active chest pain — ACS pathway",
    prior_admissions: 1,
  },
  brief: `**SUMMARY**
68F with 2h of substernal chest pain, HR 104, troponin rising 0.09 -> 0.31 ng/mL over 3 hours.

**RED FLAGS**
[critical] Rising troponin on serial draw
  0.09 ng/mL at arrival, 0.31 ng/mL at 3.4h [labs#301692]
  -> Repeat troponin at 6h, cardiology consult
[warning] Sustained tachycardia
  HR 104 bpm, unresponsive to rest [vitals#401691]
  -> Continuous telemetry

**CURRENT STATE**
Chest pain ongoing per ED note, HR elevated, no ECG result available in the structured record [note_chunks#501691].

**HISTORY**
One prior admission on record, cause not coded in this window.

**CRITICAL UNKNOWNS**
[unknown] No ECG findings in the structured record
  HEART score cannot be completed without an ECG read.
  -> Ask the clinician for the ECG interpretation`,
  alerts: [
    {
      severity: "critical",
      title: "Rising troponin on serial draw",
      detail: "0.09 ng/mL at arrival, 0.31 ng/mL at 3.4h",
      action: "Repeat troponin at 6h, cardiology consult",
      sources: [{ table: "labs", id: 301692 }],
    },
    {
      severity: "warning",
      title: "Sustained tachycardia",
      detail: "HR 104 bpm, unresponsive to rest",
      action: "Continuous telemetry",
      sources: [{ table: "vitals", id: 401691 }],
    },
  ],
  risk: {
    available: true,
    scored_at_hour: 3.4,
    feature_coverage: 0.82,
    predictions: [
      {
        label: "30-day major cardiac event",
        available: true,
        probability: 0.34,
        threshold: 0.25,
        alert: true,
        confidence: "moderate",
        action: "Admit for serial troponin and cardiology review.",
        model_performance: { auc: 0.81, precision: 0.62, recall: 0.7 },
      },
    ],
  },
  vitals: buildVitals(29817364, {
    hr: [
      [0, 88],
      [0.5, 104],
      [1.5, 101],
      [3, 98],
    ],
    sbp: [
      [0, 138],
      [0.5, 142],
      [1.5, 136],
      [3, 130],
    ],
    spo2: [
      [0, 97],
      [1.5, 96],
      [3, 97],
    ],
  }),
  history: {
    subject_id: 15234891,
    prior_admissions: 1,
    admissions: [
      { hadm_id: 29817364, is_current: true, admission_type: "EMERGENCY", diagnoses: [] },
      { hadm_id: 28900123, is_current: false, admission_type: "EMERGENCY", length_of_stay_days: 2, diagnoses: [{ title: "Unstable angina", source: "diagnoses#602001" }] },
    ],
    recurring_conditions: [{ condition: "Hypertension", visits: 2, source: "diagnoses" }],
    note: null,
  },
  suggestedScores: ["HEART", "WELLS_PE", "PERC"],
  scoreResults: {
    HEART: {
      complete: false,
      components: {
        found: [
          { label: "Troponin", value: "0.31 ng/mL (rising)", points: 2, source: "labs#301692" },
          { label: "Age", value: 68, points: 1, source: null },
        ],
        missing: [
          { label: "History", ask: "How suspicious is the history — slightly, moderately, or highly?" },
          { label: "ECG", ask: "What does the ECG show — normal, non-specific, or significant ST deviation?" },
          { label: "Risk factors", ask: "How many cardiac risk factors does the patient have?" },
        ],
      },
    },
  },
});

// ---- 2. Sepsis — 54M, fever + tachycardia + hypotension ----
reg("vitals", 401721, { vital_code: "temp_c", label: "Temperature", valuenum: 38.9, valueuom: "C", hours_since_admit: 0.2 });
reg("vitals", 401722, { vital_code: "sbp", label: "Systolic BP", valuenum: 84, valueuom: "mmHg", hours_since_admit: 0.6 });
reg("labs", 301723, { label: "White Blood Cells", valuenum: 16.4, valueuom: "K/uL", hours_since_admit: 0.8 }, "WBC from admission panel.");
reg("labs", 301724, { label: "Lactate", valuenum: 3.1, valueuom: "mmol/L", hours_since_admit: 1.1 }, "Venous lactate.");
addHero({
  card: {
    hadm_id: 27461038,
    subject_id: 14872205,
    age: 54,
    gender: "M",
    cohort: "sepsis-alert",
    hours_since_arrival: 1.1,
    risk_level: "critical",
    risk_score: 0.71,
    alert_count: 3,
    unknown_count: 1,
    headline: "Fever, hypotension, elevated lactate — sepsis pathway",
    prior_admissions: 0,
  },
  brief: `**SUMMARY**
54M with fever 38.9C, SBP 84, lactate 3.1 mmol/L within the first hour — meets qSOFA criteria.

**RED FLAGS**
[critical] Hypotension with fever
  SBP 84 mmHg at 0.6h, temp 38.9C at 0.2h [vitals#401722]
  -> Sepsis bundle: cultures, broad-spectrum antibiotics, 30mL/kg crystalloid
[critical] Elevated lactate
  3.1 mmol/L [labs#301724]
  -> Repeat lactate in 2-4h, trend clearance
[warning] Leukocytosis
  WBC 16.4 K/uL [labs#301723]

**CURRENT STATE**
Meets 2 of 3 qSOFA criteria (SBP <=100, unknown mental status). SIRS criteria also met on temperature and WBC.

**HISTORY**
No prior admissions on record.

**CRITICAL UNKNOWNS**
[unknown] Mental status not charted
  -> Ask the bedside nurse for a GCS or AVPU assessment`,
  alerts: [
    {
      severity: "critical",
      title: "Hypotension with fever",
      detail: "SBP 84 mmHg at 0.6h, temp 38.9C at 0.2h",
      action: "Sepsis bundle: cultures, broad-spectrum antibiotics, 30mL/kg crystalloid",
      sources: [{ table: "vitals", id: 401722 }],
    },
    {
      severity: "critical",
      title: "Elevated lactate",
      detail: "3.1 mmol/L",
      action: "Repeat lactate in 2-4h, trend clearance",
      sources: [{ table: "labs", id: 301724 }],
    },
    {
      severity: "warning",
      title: "Leukocytosis",
      detail: "WBC 16.4 K/uL",
      sources: [{ table: "labs", id: 301723 }],
    },
  ],
  risk: {
    available: true,
    scored_at_hour: 1.1,
    feature_coverage: 0.74,
    coverage_note: "Mental status and repeat lactate not yet charted.",
    predictions: [
      {
        label: "In-hospital mortality (sepsis)",
        available: true,
        probability: 0.19,
        threshold: 0.15,
        alert: true,
        confidence: "moderate",
        action: "Escalate to sepsis response team.",
        model_performance: { auc: 0.79, precision: 0.55, recall: 0.68 },
      },
    ],
  },
  vitals: buildVitals(27461038, {
    hr: [
      [0, 112],
      [0.6, 118],
      [1, 116],
    ],
    sbp: [
      [0, 92],
      [0.6, 84],
      [1, 88],
    ],
    temp_c: [
      [0.2, 38.9],
      [1, 38.5],
    ],
    rr: [
      [0.6, 24],
      [1, 22],
    ],
  }),
  suggestedScores: ["QSOFA", "SIRS", "CURB65"],
  scoreResults: {
    QSOFA: {
      complete: false,
      components: {
        found: [{ label: "Systolic BP 100 or below", value: 84, points: 1, source: "vitals#401722" }],
        missing: [
          { label: "Respiratory rate 22 or over", ask: "What is the respiratory rate 22 or over?" },
          { label: "Altered mental state (GCS < 15)", ask: "What is the altered mental state (gcs < 15)?" },
        ],
      },
    },
    SIRS: {
      complete: false,
      components: {
        found: [
          { label: "Temperature >38 or <36 C", value: 38.9, points: 1, source: "vitals#401721" },
          { label: "WBC >12 or <4", value: 16.4, points: 1, source: "labs#301723" },
        ],
        missing: [{ label: "Heart rate over 90", ask: "What is the heart rate over 90?" }, { label: "Respiratory rate over 20", ask: "What is the respiratory rate over 20?" }],
      },
    },
  },
});

// ---- 3. GI bleed — 39F, melena ----
reg("labs", 301741, { label: "Urea Nitrogen", valuenum: 9.2, valueuom: "mmol/L", hours_since_admit: 0.5 }, "BUN on admission.");
reg("labs", 301742, { label: "Hemoglobin", valuenum: 8.9, valueuom: "g/dL", hours_since_admit: 0.5 }, "Hemoglobin on admission.");
reg("note_chunks", 501741, {
  section: "HPI",
  note_type: "ED Provider Note",
  hours_since_admit: 0.4,
  text: "39-year-old female with one day of black, tarry stools and lightheadedness on standing. No known liver disease.",
}, "History of present illness.", ["black, tarry stools", "lightheadedness"]);
addHero({
  card: {
    hadm_id: 24905183,
    subject_id: 11987340,
    age: 39,
    gender: "F",
    cohort: "gi-bleed",
    hours_since_arrival: 0.6,
    risk_level: "critical",
    risk_score: 0.66,
    alert_count: 2,
    unknown_count: 1,
    headline: "Melena with low hemoglobin — upper GI bleed pathway",
    prior_admissions: 0,
  },
  brief: `**SUMMARY**
39F with melena and lightheadedness, hemoglobin 8.9 g/dL, BUN elevated.

**RED FLAGS**
[critical] Low hemoglobin with melena
  Hgb 8.9 g/dL [labs#301742], melena on history [note_chunks#501741]
  -> Type and cross, GI consult for endoscopy
[warning] Elevated urea
  BUN 9.2 mmol/L [labs#301741]
  -> Correlates with upper GI blood load

**CURRENT STATE**
Orthostatic symptoms reported, no orthostatic vitals charted yet.

**HISTORY**
No prior admissions on record.

**CRITICAL UNKNOWNS**
[unknown] No liver disease history coded
  -> Confirm with patient or family whether there is a history of liver disease`,
  alerts: [
    {
      severity: "critical",
      title: "Low hemoglobin with melena",
      detail: "Hgb 8.9 g/dL, melena on history",
      action: "Type and cross, GI consult for endoscopy",
      sources: [{ table: "labs", id: 301742 }, { table: "note_chunks", id: 501741 }],
    },
    {
      severity: "warning",
      title: "Elevated urea",
      detail: "BUN 9.2 mmol/L",
      action: "Correlates with upper GI blood load",
      sources: [{ table: "labs", id: 301741 }],
    },
  ],
  risk: { available: false, reason: "Too little data to score. This is not low risk — it means the model could not assess this patient." },
  vitals: buildVitals(24905183, {
    hr: [
      [0, 108],
      [0.5, 112],
    ],
    sbp: [
      [0, 102],
      [0.5, 98],
    ],
  }),
  suggestedScores: ["GLASGOW_BLATCHFORD", "SHOCK_INDEX"],
  scoreResults: {
    GLASGOW_BLATCHFORD: {
      complete: false,
      components: {
        found: [
          { label: "Urea", value: 9.2, points: 2, source: "labs#301741" },
          { label: "Haemoglobin", value: 8.9, points: 6, source: "labs#301742" },
        ],
        missing: [
          { label: "Systolic BP", ask: "What is the systolic bp?" },
          { label: "Heart rate 100 or over", ask: "What is the heart rate 100 or over?" },
          { label: "Melaena", ask: "Melaena?" },
          { label: "Syncope", ask: "Syncope?" },
          { label: "Liver disease", ask: "Liver disease?" },
          { label: "Cardiac failure", ask: "Cardiac failure?" },
        ],
      },
    },
  },
});

// ---- 4. New-onset AFib — 72M ----
reg("vitals", 401761, { vital_code: "hr", label: "Heart rate", valuenum: 142, valueuom: "bpm", hours_since_admit: 0.3 });
reg("diagnoses", 601761, { icd_code: "I10", icd_version: 10, long_title: "Essential (primary) hypertension" }, "Coded on a prior admission.");
addHero({
  card: {
    hadm_id: 23456789,
    subject_id: 10983456,
    age: 72,
    gender: "M",
    cohort: "cardiology",
    hours_since_arrival: 0.8,
    risk_level: "high",
    risk_score: 0.52,
    alert_count: 1,
    unknown_count: 2,
    headline: "New-onset atrial fibrillation, rate 142",
    prior_admissions: 2,
  },
  brief: `**SUMMARY**
72M with new-onset atrial fibrillation, ventricular rate 142, known hypertension.

**RED FLAGS**
[warning] Rapid ventricular rate
  HR 142 bpm [vitals#401761]
  -> Rate control, anticoagulation assessment (CHA2DS2-VASc, HAS-BLED)

**CURRENT STATE**
Hypertension coded on a prior admission [diagnoses#601761]. No echo or prior AFib history in the structured record.

**HISTORY**
Two prior admissions on record.

**CRITICAL UNKNOWNS**
[unknown] Bleeding risk factors not in the structured record
  -> Ask about prior bleeding, labile INR, and current antiplatelet/NSAID use
[unknown] Stroke or TIA history not coded
  -> Confirm with patient or prior records`,
  alerts: [
    {
      severity: "warning",
      title: "Rapid ventricular rate",
      detail: "HR 142 bpm",
      action: "Rate control, anticoagulation assessment (CHA2DS2-VASc, HAS-BLED)",
      sources: [{ table: "vitals", id: 401761 }],
    },
  ],
  risk: {
    available: true,
    scored_at_hour: 0.8,
    feature_coverage: 0.6,
    predictions: [
      {
        label: "30-day readmission risk",
        available: true,
        probability: 0.28,
        threshold: 0.3,
        alert: false,
        confidence: "low",
        model_performance: { auc: 0.74, precision: 0.48, recall: 0.55, note: "Built from limited data this early in the stay." },
      },
    ],
  },
  vitals: buildVitals(23456789, {
    hr: [
      [0, 138],
      [0.3, 142],
      [1, 121],
    ],
    sbp: [
      [0, 148],
      [1, 140],
    ],
  }),
  history: {
    subject_id: 10983456,
    prior_admissions: 2,
    admissions: [
      { hadm_id: 23456789, is_current: true, admission_type: "EMERGENCY", diagnoses: [] },
      { hadm_id: 22001456, is_current: false, admission_type: "OBSERVATION", length_of_stay_days: 1, diagnoses: [{ title: "Essential hypertension", source: "diagnoses#601761" }] },
      { hadm_id: 20887123, is_current: false, admission_type: "EMERGENCY", length_of_stay_days: 3, diagnoses: [{ title: "Community-acquired pneumonia", source: "diagnoses" }] },
    ],
    recurring_conditions: [{ condition: "Hypertension", visits: 3, source: "diagnoses" }],
    note: null,
  },
  suggestedScores: ["CHA2DS2_VASC", "HAS_BLED"],
  scoreResults: {
    CHA2DS2_VASC: {
      complete: false,
      components: {
        found: [
          { label: "Hypertension", value: "Essential (primary) hypertension", points: 1, source: "diagnoses#601761" },
          { label: "Age 65-74", value: 72, points: 1, source: null },
        ],
        missing: [
          { label: "Congestive heart failure", ask: "Congestive heart failure?" },
          { label: "Diabetes", ask: "Diabetes?" },
          { label: "Prior stroke, TIA or thromboembolism", ask: "Prior stroke, tia or thromboembolism?" },
          { label: "Vascular disease", ask: "Vascular disease?" },
          { label: "Female sex", ask: "Female sex?" },
        ],
      },
    },
  },
});

// ---- 5. Stable / observation — 61M low risk ----
addHero({
  card: {
    hadm_id: 29104857,
    subject_id: 15789023,
    age: 61,
    gender: "M",
    cohort: "observation",
    hours_since_arrival: 5.2,
    risk_level: "low",
    risk_score: 0.08,
    alert_count: 0,
    unknown_count: 0,
    headline: "Stable, vitals within normal range, observation for chest wall pain",
    prior_admissions: 0,
  },
  brief: `**SUMMARY**
61M, chest wall tenderness, reproducible on palpation, vitals stable throughout.

**RED FLAGS**
No red flags at this point in time.

**CURRENT STATE**
All charted vitals within normal range for the full observation window.

**HISTORY**
No prior admissions on record.

**CRITICAL UNKNOWNS**
No critical unknowns at this point in time.`,
  alerts: [],
  risk: {
    available: true,
    scored_at_hour: 5.2,
    feature_coverage: 0.95,
    predictions: [
      {
        label: "30-day major cardiac event",
        available: true,
        probability: 0.03,
        threshold: 0.25,
        alert: false,
        confidence: "high",
        model_performance: { auc: 0.81, precision: 0.62, recall: 0.7 },
      },
    ],
  },
  vitals: buildVitals(29104857, {
    hr: [
      [0, 76],
      [2, 74],
      [4, 72],
    ],
    sbp: [
      [0, 122],
      [2, 118],
      [4, 120],
    ],
    spo2: [
      [0, 99],
      [4, 99],
    ],
  }),
  suggestedScores: ["HEART"],
});

// ---- 6. AKI — 82M rising creatinine ----
reg("labs", 301781, { label: "Creatinine", valuenum: 1.1, valueuom: "mg/dL", hours_since_admit: -2 }, "Baseline creatinine, prior visit.");
reg("labs", 301782, { label: "Creatinine", valuenum: 2.4, valueuom: "mg/dL", hours_since_admit: 4.2 }, "Current creatinine.");
addHero({
  card: {
    hadm_id: 26183947,
    subject_id: 12905473,
    age: 82,
    gender: "M",
    cohort: "renal",
    hours_since_arrival: 4.2,
    risk_level: "high",
    risk_score: 0.48,
    alert_count: 1,
    unknown_count: 1,
    headline: "Creatinine doubled from baseline — AKI stage 2",
    prior_admissions: 3,
  },
  brief: `**SUMMARY**
82M, creatinine risen from a baseline of 1.1 to 2.4 mg/dL over the admission — meets KDIGO stage 2 AKI.

**RED FLAGS**
[warning] Rapidly rising creatinine
  1.1 -> 2.4 mg/dL [labs#301782]
  -> Nephrology consult, review nephrotoxic medications, hold contrast

**CURRENT STATE**
No urine output data charted for this admission.

**HISTORY**
Three prior admissions on record.

**CRITICAL UNKNOWNS**
[unknown] Urine output not charted
  -> Ask nursing staff for hourly urine output`,
  alerts: [
    {
      severity: "warning",
      title: "Rapidly rising creatinine",
      detail: "1.1 -> 2.4 mg/dL",
      action: "Nephrology consult, review nephrotoxic medications, hold contrast",
      sources: [{ table: "labs", id: 301782 }],
    },
  ],
  risk: { available: false, reason: "Too little data to score. This is not low risk — it means the model could not assess this patient." },
  vitals: buildVitals(26183947, {
    hr: [
      [0, 84],
      [4.2, 88],
    ],
    sbp: [
      [0, 126],
      [4.2, 118],
    ],
  }),
  suggestedScores: ["KDIGO_AKI", "ANION_GAP"],
  scoreResults: {
    KDIGO_AKI: {
      complete: true,
      total: 2,
      max_possible: 3,
      risk: "stage 2",
      interpretation: "Creatinine has more than doubled from baseline — Stage 2 AKI.",
      components: {
        found: [
          { label: "Current creatinine", value: 2.4, points: 0, source: "labs#301782" },
          { label: "Admission creatinine", value: 1.1, points: 0, source: "labs#301781" },
        ],
        missing: [],
      },
    },
  },
});

// ------------------------------------------------------------------
// Filler patients — lighter, generated, so the cohort board looks like a
// real ED census. Still fully valid at every endpoint, just less bespoke.
// ------------------------------------------------------------------
const FILLER_PRESENTATIONS = [
  { headline: "Ankle inversion injury, weight-bearing, low suspicion for fracture", cohort: "ortho", risk: "low" as const },
  { headline: "Migraine, typical pattern, responsive to initial treatment", cohort: "neuro", risk: "low" as const },
  { headline: "COPD exacerbation, increased work of breathing", cohort: "resp", risk: "warning" as const },
  { headline: "Syncope, single episode, normal ECG on file", cohort: "syncope", risk: "moderate" as const },
  { headline: "Cellulitis, lower leg, afebrile", cohort: "infection", risk: "moderate" as const },
  { headline: "Abdominal pain, undifferentiated, awaiting imaging", cohort: "gi", risk: "moderate" as const },
];
const FILLER_RISK_LEVEL: Record<string, PatientCard["risk_level"]> = {
  low: "low",
  moderate: "moderate",
  warning: "moderate",
  high: "high",
};

function fillerHadmId(i: number) {
  return 25000000 + i * 137;
}

function generateFiller(i: number): HeroPatient {
  const p = FILLER_PRESENTATIONS[i % FILLER_PRESENTATIONS.length];
  const hadmId = fillerHadmId(i);
  const subjectId = 13000000 + i * 211;
  const age = 22 + Math.floor(hashFloat(i + 500) * 60);
  const gender = hashFloat(i + 600) > 0.5 ? "F" : "M";
  const hours = Math.round(hashFloat(i + 700) * 60 * 10) / 10;
  const riskScore = Math.round(hashFloat(i + 800) * 30) / 100;
  const alertCount = p.risk === "warning" ? 1 : 0;

  return {
    card: {
      hadm_id: hadmId,
      subject_id: subjectId,
      age,
      gender,
      cohort: p.cohort,
      hours_since_arrival: hours,
      risk_level: FILLER_RISK_LEVEL[p.risk],
      risk_score: riskScore,
      alert_count: alertCount,
      unknown_count: 0,
      headline: p.headline,
      prior_admissions: Math.floor(hashFloat(i + 900) * 3),
    },
    brief: `**SUMMARY**
${p.headline}

**RED FLAGS**
No red flags at this point in time.

**CURRENT STATE**
Charted vitals within expected range for this presentation.

**HISTORY**
${Math.floor(hashFloat(i + 900) * 3)} prior admission(s) on record.

**CRITICAL UNKNOWNS**
No critical unknowns at this point in time.`,
    alerts: [],
    risk: {
      available: true,
      scored_at_hour: hours,
      feature_coverage: 0.7,
      predictions: [
        {
          label: "30-day readmission risk",
          available: true,
          probability: riskScore,
          threshold: 0.3,
          alert: riskScore > 0.3,
          confidence: "moderate",
          model_performance: { auc: 0.74, precision: 0.5, recall: 0.55 },
        },
      ],
    },
    vitals: buildVitals(hadmId, {
      hr: [
        [0, 70 + Math.floor(hashFloat(i + 1000) * 30)],
        [hours, 68 + Math.floor(hashFloat(i + 1100) * 30)],
      ],
      sbp: [
        [0, 110 + Math.floor(hashFloat(i + 1200) * 25)],
        [hours, 112 + Math.floor(hashFloat(i + 1300) * 25)],
      ],
    }),
    suggestedScores: [],
  };
}

const FILLER_COUNT = 6;
for (let i = 0; i < FILLER_COUNT; i++) {
  const f = generateFiller(i);
  HERO[f.card.hadm_id] = f;
}

// ------------------------------------------------------------------
// Public surface
// ------------------------------------------------------------------
export function demoPatients(): PatientsResponse {
  const patients = Object.values(HERO)
    .map((h) => h.card)
    .sort((a, b) => a.hadm_id - b.hadm_id);
  return { count: patients.length, patients };
}

function getHero(hadmId: number): HeroPatient | null {
  return HERO[hadmId] || null;
}

export function demoBrief(hadmId: number): BriefResponse {
  const h = getHero(hadmId);
  const brief = h?.brief || `**SUMMARY**\nNo demo brief authored for this patient.\n\n**RED FLAGS**\nNo red flags at this point in time.`;
  return {
    hadm_id: hadmId,
    generated_at: new Date().toISOString(),
    generation_ms: 640,
    brief,
    agents_run: ["intake", "rules", "risk", "scores", "brief"],
    trust: { citations_valid: (h?.alerts.length || 0) + 1, citations_invalid: 0, traceable_pct: 100 },
    cached: false,
    disclaimer: "Decision support, not diagnosis. Demo data — not a real patient record.",
  };
}

export function demoAlerts(hadmId: number): AlertsResponse {
  const h = getHero(hadmId);
  const alerts = h?.alerts || [];
  return {
    hadm_id: hadmId,
    alerts,
    counts: {
      critical: alerts.filter((a) => a.severity === "critical").length,
      warning: alerts.filter((a) => a.severity === "warning").length,
      unknown: alerts.filter((a) => a.severity === "unknown").length,
      info: alerts.filter((a) => a.severity === "info").length,
    },
  };
}

export function demoRisk(hadmId: number): RiskResponse {
  return getHero(hadmId)?.risk || { available: false, reason: "No demo risk data for this patient." };
}

export function demoVitals(hadmId: number): VitalsResponse {
  return (
    getHero(hadmId)?.vitals || {
      hadm_id: hadmId,
      sampling: "hourly",
      sampling_note: "No vitals recorded for this window.",
      series: [],
      derived: [],
    }
  );
}

export function demoHistory(subjectId: number): HistoryResponse {
  const h = Object.values(HERO).find((x) => x.card.subject_id === subjectId);
  return (
    h?.history || {
      subject_id: subjectId,
      prior_admissions: 0,
      admissions: [],
      recurring_conditions: [],
      note: "No prior admissions on record.",
    }
  );
}

export function demoSource(table: string, id: number): SourceResponse {
  const found = SOURCES[`${table}#${id}`];
  if (found) {
    return {
      table,
      id,
      row: found.row,
      context: found.context,
      highlight: found.highlight,
      provenance: { source_dataset: "MIMIC-IV v2.1 (demo)", source_table: table },
    };
  }
  // Generic fallback so a citation that wasn't hand-authored still opens
  // something reasonable rather than a 404.
  return {
    table,
    id,
    row: { note: "Demo record — illustrative only, not sourced from a real patient." },
    provenance: { source_dataset: "demo", source_table: table },
  };
}

export function demoQuestions(hadmId: number): QuestionsResponse {
  const h = getHero(hadmId);
  const base = [
    "What is missing from this record?",
    "What is the patient taking at home?",
    "Any history of bleeding?",
  ];
  if (h && h.alerts.length > 0) {
    return { hadm_id: hadmId, questions: [`Why is "${h.alerts[0].title}" flagged?`, ...base.slice(0, 2)] };
  }
  return { hadm_id: hadmId, questions: base };
}

export function demoChat(hadmId: number, message: string): ChatResponse {
  const h = getHero(hadmId);
  const lower = message.toLowerCase();

  if (lower.includes("missing") || lower.includes("unknown")) {
    const unknownLine = (h?.brief.split("\n").find((l) => l.startsWith("[unknown]")) || "").replace(/^\[unknown\]\s*/, "");
    return {
      answer: unknownLine
        ? `The record doesn't have everything needed here. ${unknownLine} is the main gap right now.`
        : "Nothing is flagged as a critical unknown for this patient at this point in time.",
      sources: [],
      abstained: unknownLine ? [{ claim: unknownLine, reason: "not recorded in the structured data" }] : [],
      tools_called: [{ tool: "get_alerts", found: true }],
    };
  }

  if (h && h.alerts.length > 0) {
    const a = h.alerts[0];
    return {
      answer: `The most significant finding is "${a.title}"${a.detail ? ` — ${a.detail}` : ""}.${a.action ? ` Recommended next step: ${a.action}.` : ""}`,
      sources: a.sources,
      abstained: [],
      tools_called: [{ tool: "get_alerts", found: true }],
    };
  }

  return {
    answer: "No active alerts for this patient at this point in time — vitals and available labs are within expected range.",
    sources: [],
    abstained: [],
    tools_called: [{ tool: "get_alerts", found: true }],
  };
}

export function demoTrust(hadmId: number): TrustResponse {
  const h = getHero(hadmId);
  return {
    hadm_id: hadmId,
    available: true,
    citations_valid: (h?.alerts.length || 0) + 1,
    citations_invalid: 0,
    traceable_pct: 100,
    last_updated: new Date().toISOString(),
  };
}

export function demoGovernance(): GovernanceResponse {
  return {
    models: [
      {
        name: "cardiac_event_risk_v3",
        auc: 0.81,
        average_precision: 0.58,
        precision: 0.62,
        recall: 0.7,
        threshold: 0.25,
        threshold_policy: "maximize recall at precision >= 0.5",
        calibration_error: 0.04,
        alert_rate: 0.18,
        positive_rate: 0.12,
        subgroup_auc_gap: 0.03,
        status: "shipped",
      },
      {
        name: "sepsis_mortality_v1",
        auc: 0.79,
        average_precision: 0.51,
        precision: 0.55,
        recall: 0.68,
        threshold: 0.15,
        threshold_policy: "maximize recall at precision >= 0.45",
        calibration_error: 0.06,
        alert_rate: 0.14,
        positive_rate: 0.09,
        subgroup_auc_gap: 0.05,
        status: "shipped",
      },
      {
        name: "readmission_risk_v2",
        auc: 0.74,
        average_precision: 0.44,
        precision: 0.48,
        recall: 0.55,
        threshold: 0.3,
        threshold_policy: "maximize F1",
        calibration_error: 0.07,
        alert_rate: 0.21,
        positive_rate: 0.16,
        subgroup_auc_gap: 0.09,
        status: "shipped",
      },
    ],
    rejected: [
      {
        name: "readmission_risk_v1",
        auc: 0.68,
        subgroup_auc_gap: 0.14,
        status: "rejected",
        rejected_because: ["subgroup AUC gap exceeded the 0.10 fairness threshold", "superseded by v2"],
      },
    ],
    registry: [
      { version: 3, status: "shipped" },
      { version: 2, status: "shipped" },
      { version: 1, status: "rejected" },
    ],
    policy: "A model ships only if it beats the rule engine on recall at matched precision, and its subgroup AUC gap stays under 0.10.",
    note: "Demo governance data — illustrative only.",
  };
}

export function demoRegisterAdmission(): AdmissionResponse {
  const hadmId = 29999000 + Math.floor(Math.random() * 900);
  return { hadm_id: hadmId, subject_id: 19999000 + Math.floor(Math.random() * 900), status: "registered", simulated: true, pipeline: "queued" };
}

export function demoPipelineStatus(hadmId: number): PipelineStatus {
  return { hadm_id: hadmId, stage: "ready", alerts: 0, elapsed_s: 4.2 };
}

// ------------------------------------------------------------------
// Scores
// ------------------------------------------------------------------
export function demoScoresList(hadmId: number): ScoresListResponse {
  const h = getHero(hadmId);
  const allKeys = Object.keys(SCORE_META).sort();
  const all_available: ScoreSummary[] = allKeys.map((k) => ({ key: k, name: SCORE_META[k].name, purpose: SCORE_META[k].purpose }));
  const suggested: ScoreSummary[] = (h?.suggestedScores || []).map((k) => ({ key: k, name: SCORE_META[k].name, purpose: SCORE_META[k].purpose }));
  return { hadm_id: hadmId, suggested, all_available };
}

/** A generic, always-valid partial score for any (patient, score) pair that
 * wasn't hand-authored above — roughly a third of inputs "found" with a
 * plausible value, the rest asked for honestly. Deterministic per patient
 * so repeat visits look stable. */
function genericScoreResult(hadmId: number, scoreKey: string): ScoreResult {
  const meta = SCORE_META[scoreKey];
  const inputs = SCORE_INPUT_SPECS[scoreKey] || [];
  const components: ScoreComponents = { found: [], missing: [] };

  inputs.forEach((inp, i) => {
    const seed = hadmId + i * 37 + scoreKey.length;
    const isFound = hashFloat(seed) < 0.35 && inp.kind !== "clinical";
    if (isFound) {
      let value: unknown = "present";
      if (inp.kind === "vital" || inp.kind === "lab") value = Math.round(hashFloat(seed + 1) * 100) / 10;
      if (inp.kind === "demo") value = 40 + Math.floor(hashFloat(seed + 2) * 45);
      components.found.push({ label: inp.label, value, points: Math.round(hashFloat(seed + 3) * 2), source: null });
    } else {
      components.missing.push({ label: inp.label, ask: `What is the ${inp.label.toLowerCase()}?`, why: "not recorded in the structured data" });
    }
  });

  return {
    score: scoreKey,
    name: meta?.name || scoreKey,
    purpose: meta?.purpose || "",
    citation: meta?.citation || "",
    complete: components.missing.length === 0,
    components,
  };
}

export function demoScore(hadmId: number, scoreKey: string): ScoreResult {
  const h = getHero(hadmId);
  const override = h?.scoreResults?.[scoreKey];
  const generic = genericScoreResult(hadmId, scoreKey);
  if (!override) return generic;
  return { ...generic, ...override, components: override.components || generic.components };
}

export function demoSubmitScore(hadmId: number, scoreKey: string, provided: Record<string, string>): ScoreResult {
  const current = demoScore(hadmId, scoreKey);
  const inputs = SCORE_INPUT_SPECS[scoreKey] || [];
  const stillMissing = current.components.missing.filter((m) => {
    const spec = inputs.find((i) => i.label === m.label);
    return !(spec && provided[spec.key] !== undefined && provided[spec.key] !== "");
  });
  const newlyFound = current.components.missing
    .filter((m) => !stillMissing.includes(m))
    .map((m) => {
      const spec = inputs.find((i) => i.label === m.label);
      const value = spec ? provided[spec.key] : "";
      const truthy = ["yes", "true", "1", "y", "present"].includes(String(value).toLowerCase());
      return { label: m.label, value, points: truthy ? 1 : 0, source: null };
    });

  return {
    ...current,
    complete: stillMissing.length === 0,
    components: { found: [...current.components.found, ...newlyFound], missing: stillMissing },
    note: stillMissing.length === 0 ? "Completed with clinician-supplied answers (demo)." : current.note,
  };
}
