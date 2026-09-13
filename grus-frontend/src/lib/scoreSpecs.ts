// Auto-derived from the real grus_scores.py SCORES dict (Priyanshu7275/grus).
//
// Why this file exists: the backend's score-result JSON (components.missing)
// only returns {label, ask, why} for a missing criterion — it drops the
// internal `key` the POST endpoint needs (see grus_score_engine.py
// ScoreResult.to_dict(), which never serializes InputValue.key for missing
// items, even though the POST docstring says "the keys come from
// components.missing on the GET"). Keys are not derivable from labels by any
// simple rule (e.g. PERC's "age_50_plus" vs label "Age 50 or over").
//
// So this mirrors the real spec's label -> key mapping, per score, so the
// scores UI can still let a clinician answer missing criteria today. If the
// backend is fixed to include `key` directly, resolveMissingKey() below
// prefers that live value automatically and this table becomes a fallback.
// SCORE_META (name/purpose/citation) also backs the demo-data fallback layer
// so the scores panel still has real, correct copy when the backend is down.

export interface ScoreInputSpec {
  key: string;
  label: string;
  kind: "lab" | "vital" | "demo" | "dx" | "med" | "clinical";
  scale?: string[];
}

export interface ScoreMeta {
  name: string;
  purpose: string;
  citation: string;
}

export const SCORE_META: Record<string, ScoreMeta> = {
  PERC: { name: "PERC Rule for Pulmonary Embolism", purpose: "Rules OUT pulmonary embolism without imaging, in patients already judged low risk.", citation: "Kline JA et al. J Thromb Haemost 2004;2(8):1247-55" },
  WELLS_PE: { name: "Wells Score for Pulmonary Embolism", purpose: "Estimates the pre-test probability of PE.", citation: "Wells PS et al. Thromb Haemost 2000;83(3):416-20" },
  HEART: { name: "HEART Score for Major Cardiac Events", purpose: "Six-week risk of myocardial infarction, intervention or death in undifferentiated chest pain.", citation: "Six AJ et al. Neth Heart J 2008;16(6):191-6" },
  QSOFA: { name: "qSOFA", purpose: "Identifies infected patients at risk of poor outcome, without needing laboratory results.", citation: "Singer M et al. JAMA 2016;315(8):801-10" },
  SIRS: { name: "SIRS Criteria", purpose: "Systemic inflammatory response. Sensitive, not specific.", citation: "Bone RC et al. Chest 1992;101(6):1644-55" },
  HAS_BLED: { name: "HAS-BLED", purpose: "Annual major bleeding risk on anticoagulation for atrial fibrillation.", citation: "Pisters R et al. Chest 2010;138(5):1093-100" },
  CHA2DS2_VASC: { name: "CHA2DS2-VASc", purpose: "Annual stroke risk in atrial fibrillation, and whether anticoagulation is indicated.", citation: "Lip GY et al. Chest 2010;137(2):263-72" },
  CURB65: { name: "CURB-65", purpose: "Mortality risk in community-acquired pneumonia, and whether admission is needed.", citation: "Lim WS et al. Thorax 2003;58(5):377-82" },
  NEWS2: { name: "NEWS2", purpose: "Aggregate physiological score for detecting deterioration on the ward.", citation: "Royal College of Physicians, 2017" },
  KDIGO_AKI: { name: "KDIGO AKI Staging", purpose: "Stages acute kidney injury by creatinine rise.", citation: "KDIGO Clinical Practice Guideline, Kidney Int Suppl 2012" },
  SHOCK_INDEX: { name: "Shock Index", purpose: "Heart rate divided by systolic pressure. Rises before blood pressure falls, which is why it detects haemorrhage earlier than pressure alone.", citation: "Allgower M, Burri C. Dtsch Med Wochenschr 1967" },
  GLASGOW_BLATCHFORD: { name: "Glasgow-Blatchford Score", purpose: "Whether upper GI bleeding needs intervention.", citation: "Blatchford O et al. Lancet 2000;356(9238):1318-21" },
  MEWS: { name: "Modified Early Warning Score", purpose: "Bedside deterioration score. Simpler than NEWS2 and still widely used.", citation: "Subbe CP et al. QJM 2001;94(10):521-6" },
  SOFA_RESP: { name: "SOFA Respiratory Component", purpose: "Respiratory organ dysfunction, by oxygenation.", citation: "Vincent JL et al. Intensive Care Med 1996;22(7):707-10" },
  ANION_GAP: { name: "Anion Gap", purpose: "Distinguishes causes of metabolic acidosis.", citation: "Standard clinical chemistry" },
};

export const SCORE_INPUT_SPECS: Record<string, ScoreInputSpec[]> = {
  PERC: [
    { key: "age_50_plus", label: "Age 50 or over", kind: "demo" },
    { key: "hr_100_plus", label: "Heart rate 100 or over", kind: "vital" },
    { key: "spo2_under_95", label: "SpO2 below 95%", kind: "vital" },
    { key: "leg_swelling", label: "Unilateral leg swelling", kind: "clinical" },
    { key: "haemoptysis", label: "Haemoptysis", kind: "clinical" },
    { key: "recent_surgery", label: "Surgery or trauma in 4 weeks", kind: "clinical" },
    { key: "prior_vte", label: "Prior DVT or PE", kind: "dx" },
    { key: "hormone_use", label: "Oestrogen use", kind: "med" },
  ],
  WELLS_PE: [
    { key: "dvt_signs", label: "Clinical signs of DVT", kind: "clinical" },
    { key: "pe_most_likely", label: "PE is the most likely diagnosis", kind: "clinical" },
    { key: "hr_100_plus", label: "Heart rate over 100", kind: "vital" },
    { key: "immobilisation", label: "Immobilised 3 days or surgery in 4 weeks", kind: "clinical" },
    { key: "prior_vte", label: "Previous DVT or PE", kind: "dx" },
    { key: "haemoptysis", label: "Haemoptysis", kind: "clinical" },
    { key: "malignancy", label: "Active malignancy", kind: "dx" },
  ],
  HEART: [
    { key: "history", label: "History", kind: "clinical", scale: ["slightly suspicious", "moderately suspicious", "highly suspicious"] },
    { key: "ecg", label: "ECG", kind: "clinical", scale: ["normal", "non-specific repolarisation", "significant ST deviation"] },
    { key: "age", label: "Age", kind: "demo" },
    { key: "risk_factors", label: "Risk factors", kind: "dx" },
    { key: "troponin", label: "Troponin", kind: "lab" },
  ],
  QSOFA: [
    { key: "rr_22_plus", label: "Respiratory rate 22 or over", kind: "vital" },
    { key: "sbp_100_less", label: "Systolic BP 100 or below", kind: "vital" },
    { key: "altered_mental", label: "Altered mental state (GCS < 15)", kind: "vital" },
  ],
  SIRS: [
    { key: "temp_abnormal", label: "Temperature >38 or <36 C", kind: "vital" },
    { key: "hr_90_plus", label: "Heart rate over 90", kind: "vital" },
    { key: "rr_20_plus", label: "Respiratory rate over 20", kind: "vital" },
    { key: "wbc_abnormal", label: "WBC >12 or <4", kind: "lab" },
  ],
  HAS_BLED: [
    { key: "hypertension", label: "Uncontrolled hypertension", kind: "dx" },
    { key: "renal", label: "Abnormal renal function", kind: "lab" },
    { key: "liver", label: "Abnormal liver function", kind: "dx" },
    { key: "stroke", label: "Prior stroke", kind: "dx" },
    { key: "bleeding", label: "Prior major bleeding", kind: "dx" },
    { key: "labile_inr", label: "Labile INR", kind: "clinical" },
    { key: "elderly", label: "Age over 65", kind: "demo" },
    { key: "drugs", label: "Antiplatelet or NSAID", kind: "med" },
    { key: "alcohol", label: "Alcohol, 8 or more units weekly", kind: "clinical" },
  ],
  CHA2DS2_VASC: [
    { key: "chf", label: "Congestive heart failure", kind: "dx" },
    { key: "hypertension", label: "Hypertension", kind: "dx" },
    { key: "age_75_plus", label: "Age 75 or over", kind: "demo" },
    { key: "diabetes", label: "Diabetes", kind: "dx" },
    { key: "stroke", label: "Prior stroke, TIA or thromboembolism", kind: "dx" },
    { key: "vascular", label: "Vascular disease", kind: "dx" },
    { key: "age_65_74", label: "Age 65-74", kind: "demo" },
    { key: "female", label: "Female sex", kind: "demo" },
  ],
  CURB65: [
    { key: "confusion", label: "Confusion", kind: "vital" },
    { key: "urea", label: "Urea over 7 mmol/L (BUN > 19)", kind: "lab" },
    { key: "rr_30_plus", label: "Respiratory rate 30 or over", kind: "vital" },
    { key: "low_bp", label: "SBP below 90 or DBP 60 or below", kind: "vital" },
    { key: "age_65_plus", label: "Age 65 or over", kind: "demo" },
  ],
  NEWS2: [
    { key: "rr", label: "Respiratory rate", kind: "vital" },
    { key: "spo2", label: "SpO2", kind: "vital" },
    { key: "sbp", label: "Systolic BP", kind: "vital" },
    { key: "hr", label: "Heart rate", kind: "vital" },
    { key: "temp", label: "Temperature", kind: "vital" },
    { key: "consciousness", label: "Consciousness", kind: "vital" },
  ],
  KDIGO_AKI: [
    { key: "creat_now", label: "Current creatinine", kind: "lab" },
    { key: "creat_baseline", label: "Admission creatinine", kind: "lab" },
    { key: "creat_peak", label: "Peak creatinine", kind: "lab" },
  ],
  SHOCK_INDEX: [
    { key: "hr", label: "Heart rate", kind: "vital" },
    { key: "sbp", label: "Systolic BP", kind: "vital" },
  ],
  GLASGOW_BLATCHFORD: [
    { key: "urea", label: "Urea", kind: "lab" },
    { key: "haemoglobin", label: "Haemoglobin", kind: "lab" },
    { key: "sbp", label: "Systolic BP", kind: "vital" },
    { key: "hr_100_plus", label: "Heart rate 100 or over", kind: "vital" },
    { key: "melaena", label: "Melaena", kind: "clinical" },
    { key: "syncope", label: "Syncope", kind: "clinical" },
    { key: "liver_disease", label: "Liver disease", kind: "dx" },
    { key: "cardiac_failure", label: "Cardiac failure", kind: "dx" },
  ],
  MEWS: [
    { key: "sbp", label: "Systolic BP", kind: "vital" },
    { key: "hr", label: "Heart rate", kind: "vital" },
    { key: "rr", label: "Respiratory rate", kind: "vital" },
    { key: "temp", label: "Temperature", kind: "vital" },
    { key: "consciousness", label: "Consciousness", kind: "vital" },
  ],
  SOFA_RESP: [
    { key: "spo2", label: "SpO2", kind: "vital" },
  ],
  ANION_GAP: [
    { key: "sodium", label: "Sodium", kind: "lab" },
    { key: "chloride", label: "Chloride", kind: "lab" },
    { key: "bicarbonate", label: "Bicarbonate", kind: "lab" },
  ],
};

/** label -> key lookup, built once per score. */
const LABEL_MAPS: Record<string, Record<string, string>> = Object.fromEntries(
  Object.entries(SCORE_INPUT_SPECS).map(([score, inputs]) => [
    score,
    Object.fromEntries(inputs.map((i) => [i.label, i.key])),
  ])
);

/** Find the input spec (kind, scale options) for one missing criterion. */
export function findInputSpec(scoreKey: string, label: string): ScoreInputSpec | undefined {
  return SCORE_INPUT_SPECS[scoreKey]?.find((i) => i.label === label);
}

/**
 * Resolve the POST field key for a missing criterion. Prefers a `key`
 * the backend may include directly (in case that gap above gets fixed);
 * otherwise falls back to this file's label -> key table.
 */
export function resolveMissingKey(scoreKey: string, missing: { label: string; key?: string }): string | undefined {
  return missing.key || LABEL_MAPS[scoreKey]?.[missing.label];
}
