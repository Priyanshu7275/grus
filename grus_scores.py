"""
GRUS — Clinical scores

Validated decision rules encoded as data rather than code.

Each score declares its inputs, where each input comes from, how it is
weighted, and what the result means. Adding a score is a dictionary
entry; there are several hundred published and hand-coding each as a
function does not scale.

The division of work matters:

    the model    picks which score applies to the question
    this file    says what the score needs and how it is weighted
    the engine   does the arithmetic
    the record   supplies what it has
    the doctor   supplies the rest

The model never computes a score and never invents an input. It selects.
That keeps every number checkable — which is the whole point, because a
plausible wrong risk score is more dangerous than no score.

Most scores need clinical findings that no database holds. PERC asks
about unilateral leg swelling; MIMIC has no column for that. So a score
is usually returned PARTIAL, with what was found, what is missing, and
where the missing parts have to come from. A partial score is an honest
answer. A score computed by assuming absent findings are negative is not.
"""

# Where an input can come from.
#   lab      a laboratory result
#   vital    a monitored observation
#   demo     age or sex
#   dx       a coded diagnosis
#   med      a prescribed drug
#   clinical examination or history — never in the structured record
SOURCE_KINDS = ("lab", "vital", "demo", "dx", "med", "clinical")


SCORES = {

    # ---------------------------------------------------------------
    # Pulmonary embolism
    # ---------------------------------------------------------------
    "PERC": {
        "name": "PERC Rule for Pulmonary Embolism",
        "purpose": "Rules OUT pulmonary embolism without imaging, in "
                   "patients already judged low risk.",
        "caution": "Only valid when clinical suspicion is already low. "
                   "Applying it to a high-risk patient is a misuse — a "
                   "negative PERC does not overrule clinical judgement.",
        "citation": "Kline JA et al. J Thromb Haemost 2004;2(8):1247-55",
        "type": "rule_out",
        "inputs": [
            {"key": "age_50_plus", "label": "Age 50 or over",
             "kind": "demo", "field": "age", "test": ">=50", "points": 1},
            {"key": "hr_100_plus", "label": "Heart rate 100 or over",
             "kind": "vital", "field": "hr", "test": ">=100", "points": 1},
            {"key": "spo2_under_95", "label": "SpO2 below 95%",
             "kind": "vital", "field": "spo2", "test": "<95", "points": 1},
            {"key": "leg_swelling", "label": "Unilateral leg swelling",
             "kind": "clinical", "points": 1,
             "ask": "Is there unilateral leg swelling?"},
            {"key": "haemoptysis", "label": "Haemoptysis",
             "kind": "clinical", "points": 1,
             "ask": "Any coughing of blood?"},
            {"key": "recent_surgery", "label": "Surgery or trauma in 4 weeks",
             "kind": "clinical", "points": 1,
             "ask": "Surgery or trauma requiring hospitalisation in the "
                    "last four weeks?"},
            {"key": "prior_vte", "label": "Prior DVT or PE",
             "kind": "dx", "codes_9": ["4151", "4534", "45340", "45341",
                                       "V1251"],
             "codes_10": ["I26", "I82", "Z8671"], "points": 1},
            {"key": "hormone_use", "label": "Oestrogen use",
             "kind": "med", "drug_class": None,
             "drugs": ["estradiol", "estrogen", "ethinyl"], "points": 1},
        ],
        "interpretation": [
            {"max": 0, "risk": "very low",
             "text": "All eight criteria negative. In a patient already "
                     "assessed as low risk, PE can be excluded without "
                     "further testing. Missed-PE rate under 2%."},
            {"min": 1, "risk": "not excluded",
             "text": "At least one criterion positive. PERC does not "
                     "exclude PE. Proceed to D-dimer or imaging as "
                     "clinical suspicion dictates."},
        ],
    },

    "WELLS_PE": {
        "name": "Wells Score for Pulmonary Embolism",
        "purpose": "Estimates the pre-test probability of PE.",
        "citation": "Wells PS et al. Thromb Haemost 2000;83(3):416-20",
        "type": "risk_stratify",
        "inputs": [
            {"key": "dvt_signs", "label": "Clinical signs of DVT",
             "kind": "clinical", "points": 3,
             "ask": "Leg swelling and pain on deep vein palpation?"},
            {"key": "pe_most_likely", "label": "PE is the most likely diagnosis",
             "kind": "clinical", "points": 3,
             "ask": "Is PE more likely than the alternatives?"},
            {"key": "hr_100_plus", "label": "Heart rate over 100",
             "kind": "vital", "field": "hr", "test": ">100", "points": 1.5},
            {"key": "immobilisation", "label": "Immobilised 3 days or surgery in 4 weeks",
             "kind": "clinical", "points": 1.5,
             "ask": "Immobilised for three days or more, or surgery within "
                    "four weeks?"},
            {"key": "prior_vte", "label": "Previous DVT or PE",
             "kind": "dx", "codes_9": ["4151", "45340", "45341", "V1251"],
             "codes_10": ["I26", "I82", "Z8671"], "points": 1.5},
            {"key": "haemoptysis", "label": "Haemoptysis",
             "kind": "clinical", "points": 1,
             "ask": "Any coughing of blood?"},
            {"key": "malignancy", "label": "Active malignancy",
             "kind": "dx", "codes_9": ["140-209"], "codes_10": ["C"],
             "points": 1},
        ],
        "interpretation": [
            {"max": 1.5, "risk": "low", "text": "Low probability (~1.3%). "
             "D-dimer is appropriate; a negative result excludes PE."},
            {"min": 2, "max": 6, "risk": "moderate",
             "text": "Moderate probability (~16%). D-dimer, then imaging "
                     "if positive."},
            {"min": 6.5, "risk": "high", "text": "High probability (~40%). "
             "Proceed to CT pulmonary angiography; do not rely on D-dimer."},
        ],
    },

    # ---------------------------------------------------------------
    # Chest pain
    # ---------------------------------------------------------------
    "HEART": {
        "name": "HEART Score for Major Cardiac Events",
        "purpose": "Six-week risk of myocardial infarction, intervention "
                   "or death in undifferentiated chest pain.",
        "citation": "Six AJ et al. Neth Heart J 2008;16(6):191-6",
        "type": "risk_stratify",
        "inputs": [
            {"key": "history", "label": "History",
             "kind": "clinical", "scale": {"slightly suspicious": 0,
                                           "moderately suspicious": 1,
                                           "highly suspicious": 2},
             "ask": "How suspicious is the history? "
                    "(slightly / moderately / highly)"},
            {"key": "ecg", "label": "ECG",
             "kind": "clinical", "scale": {"normal": 0,
                                           "non-specific repolarisation": 1,
                                           "significant ST deviation": 2},
             "ask": "ECG: normal, non-specific changes, or significant ST "
                    "deviation?"},
            {"key": "age", "label": "Age",
             "kind": "demo", "field": "age",
             "bands": [[45, 0], [65, 1], [999, 2]]},
            {"key": "risk_factors", "label": "Risk factors",
             "kind": "dx", "count_codes": {
                 "9": ["250", "401", "272", "3051", "V1582"],
                 "10": ["E11", "I10", "E78", "F17", "Z87891"]},
             "bands_from_count": [[0, 0], [2, 1], [99, 2]]},
            {"key": "troponin", "label": "Troponin",
             "kind": "lab", "field": "Troponin T",
             "bands": [[0.04, 0], [0.12, 1], [9999, 2]]},
        ],
        "interpretation": [
            {"max": 3, "risk": "low", "text": "1.7% risk of a major cardiac "
             "event at six weeks. Discharge is reasonable."},
            {"min": 4, "max": 6, "risk": "moderate",
             "text": "16.6% risk. Admit for observation and serial troponin."},
            {"min": 7, "risk": "high", "text": "50.1% risk. Early invasive "
             "strategy warranted."},
        ],
    },

    # ---------------------------------------------------------------
    # Sepsis
    # ---------------------------------------------------------------
    "QSOFA": {
        "name": "qSOFA",
        "purpose": "Identifies infected patients at risk of poor outcome, "
                   "without needing laboratory results.",
        "caution": "A screening tool, not a diagnosis. It is specific but "
                   "insensitive — a score of 0 does not exclude sepsis.",
        "citation": "Singer M et al. JAMA 2016;315(8):801-10",
        "type": "risk_stratify",
        "inputs": [
            {"key": "rr_22_plus", "label": "Respiratory rate 22 or over",
             "kind": "vital", "field": "rr", "test": ">=22", "points": 1},
            {"key": "sbp_100_less", "label": "Systolic BP 100 or below",
             "kind": "vital", "field": "sbp", "test": "<=100", "points": 1},
            {"key": "altered_mental", "label": "Altered mental state (GCS < 15)",
             "kind": "vital", "field": "gcs_total", "test": "<15", "points": 1},
        ],
        "interpretation": [
            {"max": 1, "risk": "low",
             "text": "Not suggestive of poor outcome from sepsis. Continue "
                     "usual assessment."},
            {"min": 2, "risk": "high",
             "text": "Associated with a tenfold increase in in-hospital "
                     "mortality among infected patients. Escalate."},
        ],
    },

    "SIRS": {
        "name": "SIRS Criteria",
        "purpose": "Systemic inflammatory response. Sensitive, not specific.",
        "caution": "Superseded by qSOFA and Sepsis-3 for sepsis screening. "
                   "Included because it remains in wide use.",
        "citation": "Bone RC et al. Chest 1992;101(6):1644-55",
        "type": "risk_stratify",
        "inputs": [
            {"key": "temp_abnormal", "label": "Temperature >38 or <36 C",
             "kind": "vital", "field": "temp_c", "test": "outside:36:38",
             "points": 1},
            {"key": "hr_90_plus", "label": "Heart rate over 90",
             "kind": "vital", "field": "hr", "test": ">90", "points": 1},
            {"key": "rr_20_plus", "label": "Respiratory rate over 20",
             "kind": "vital", "field": "rr", "test": ">20", "points": 1},
            {"key": "wbc_abnormal", "label": "WBC >12 or <4",
             "kind": "lab", "field": "White Blood Cells",
             "test": "outside:4:12", "points": 1},
        ],
        "interpretation": [
            {"max": 1, "risk": "low", "text": "SIRS criteria not met."},
            {"min": 2, "risk": "positive",
             "text": "SIRS criteria met. Consider infection, but note SIRS "
                     "is triggered by trauma, burns and pancreatitis too."},
        ],
    },

    # ---------------------------------------------------------------
    # Bleeding risk
    # ---------------------------------------------------------------
    "HAS_BLED": {
        "name": "HAS-BLED",
        "purpose": "Annual major bleeding risk on anticoagulation for "
                   "atrial fibrillation.",
        "caution": "A high score is a reason to correct modifiable risk "
                   "factors and review more often, not to withhold "
                   "anticoagulation.",
        "citation": "Pisters R et al. Chest 2010;138(5):1093-100",
        "type": "risk_stratify",
        "inputs": [
            {"key": "hypertension", "label": "Uncontrolled hypertension",
             "kind": "dx", "codes_9": ["401", "402", "403", "404", "405"],
             "codes_10": ["I10", "I11", "I12", "I13", "I15"], "points": 1},
            {"key": "renal", "label": "Abnormal renal function",
             "kind": "lab", "field": "Creatinine", "test": ">2.26",
             "points": 1},
            {"key": "liver", "label": "Abnormal liver function",
             "kind": "dx", "codes_9": ["571", "572"],
             "codes_10": ["K70", "K72", "K74"], "points": 1},
            {"key": "stroke", "label": "Prior stroke",
             "kind": "dx", "codes_9": ["430", "431", "434", "436", "V1254"],
             "codes_10": ["I60", "I61", "I63", "Z8673"], "points": 1},
            {"key": "bleeding", "label": "Prior major bleeding",
             "kind": "dx", "codes_9": ["578", "4560", "5693", "99811"],
             "codes_10": ["K92", "K922", "I8501"], "points": 1},
            {"key": "labile_inr", "label": "Labile INR",
             "kind": "clinical", "points": 1,
             "ask": "Has the INR been unstable, or time-in-range below 60%?"},
            {"key": "elderly", "label": "Age over 65",
             "kind": "demo", "field": "age", "test": ">65", "points": 1},
            {"key": "drugs", "label": "Antiplatelet or NSAID",
             "kind": "med", "drug_class": "antiplatelet", "points": 1},
            {"key": "alcohol", "label": "Alcohol, 8 or more units weekly",
             "kind": "clinical", "points": 1,
             "ask": "Eight or more alcoholic drinks per week?"},
        ],
        "interpretation": [
            {"max": 2, "risk": "low",
             "text": "1.0-1.9 major bleeds per 100 patient-years."},
            {"min": 3, "risk": "high",
             "text": "3.7 or more major bleeds per 100 patient-years. "
                     "Review modifiable factors; anticoagulate with closer "
                     "monitoring rather than withholding."},
        ],
    },

    "CHA2DS2_VASC": {
        "name": "CHA2DS2-VASc",
        "purpose": "Annual stroke risk in atrial fibrillation, and whether "
                   "anticoagulation is indicated.",
        "citation": "Lip GY et al. Chest 2010;137(2):263-72",
        "type": "risk_stratify",
        "inputs": [
            {"key": "chf", "label": "Congestive heart failure",
             "kind": "dx", "codes_9": ["428"], "codes_10": ["I50"],
             "points": 1},
            {"key": "hypertension", "label": "Hypertension",
             "kind": "dx", "codes_9": ["401", "402", "403", "404", "405"],
             "codes_10": ["I10", "I11", "I12", "I13"], "points": 1},
            {"key": "age_75_plus", "label": "Age 75 or over",
             "kind": "demo", "field": "age", "test": ">=75", "points": 2},
            {"key": "diabetes", "label": "Diabetes",
             "kind": "dx", "codes_9": ["250"], "codes_10": ["E10", "E11"],
             "points": 1},
            {"key": "stroke", "label": "Prior stroke, TIA or thromboembolism",
             "kind": "dx", "codes_9": ["430", "431", "434", "435", "V1254"],
             "codes_10": ["I60", "I61", "I63", "G45", "Z8673"], "points": 2},
            {"key": "vascular", "label": "Vascular disease",
             "kind": "dx", "codes_9": ["410", "412", "440", "4439"],
             "codes_10": ["I21", "I25", "I70", "I73"], "points": 1},
            {"key": "age_65_74", "label": "Age 65-74",
             "kind": "demo", "field": "age", "test": "between:65:74",
             "points": 1},
            {"key": "female", "label": "Female sex",
             "kind": "demo", "field": "sex", "test": "==F", "points": 1},
        ],
        "interpretation": [
            {"max": 0, "risk": "low",
             "text": "0.2% annual stroke risk. Anticoagulation not "
                     "recommended."},
            {"min": 1, "max": 1, "risk": "moderate",
             "text": "0.6% annual risk. Anticoagulation may be considered."},
            {"min": 2, "risk": "high",
             "text": "2.2% or greater annual risk. Anticoagulation "
                     "recommended."},
        ],
    },

    # ---------------------------------------------------------------
    # Respiratory
    # ---------------------------------------------------------------
    "CURB65": {
        "name": "CURB-65",
        "purpose": "Mortality risk in community-acquired pneumonia, and "
                   "whether admission is needed.",
        "citation": "Lim WS et al. Thorax 2003;58(5):377-82",
        "type": "risk_stratify",
        "inputs": [
            {"key": "confusion", "label": "Confusion",
             "kind": "vital", "field": "gcs_total", "test": "<15", "points": 1},
            {"key": "urea", "label": "Urea over 7 mmol/L (BUN > 19)",
             "kind": "lab", "field": "Urea Nitrogen", "test": ">19",
             "points": 1},
            {"key": "rr_30_plus", "label": "Respiratory rate 30 or over",
             "kind": "vital", "field": "rr", "test": ">=30", "points": 1},
            {"key": "low_bp", "label": "SBP below 90 or DBP 60 or below",
             "kind": "vital", "field": "sbp", "test": "<90", "points": 1},
            {"key": "age_65_plus", "label": "Age 65 or over",
             "kind": "demo", "field": "age", "test": ">=65", "points": 1},
        ],
        "interpretation": [
            {"max": 1, "risk": "low",
             "text": "1.5% 30-day mortality. Outpatient treatment."},
            {"min": 2, "max": 2, "risk": "moderate",
             "text": "9.2% mortality. Consider short admission."},
            {"min": 3, "risk": "high",
             "text": "22% or greater mortality. Admit; consider critical "
                     "care at 4 or 5."},
        ],
    },

    "NEWS2": {
        "name": "NEWS2",
        "purpose": "Aggregate physiological score for detecting "
                   "deterioration on the ward.",
        "citation": "Royal College of Physicians, 2017",
        "type": "risk_stratify",
        "inputs": [
            {"key": "rr", "label": "Respiratory rate", "kind": "vital",
             "field": "rr", "news_bands": [
                 [8, 3], [11, 1], [20, 0], [24, 2], [999, 3]]},
            {"key": "spo2", "label": "SpO2", "kind": "vital",
             "field": "spo2", "news_bands_desc": [
                 [91, 3], [93, 2], [95, 1], [100, 0]]},
            {"key": "sbp", "label": "Systolic BP", "kind": "vital",
             "field": "sbp", "news_bands_desc": [
                 [90, 3], [100, 2], [110, 1], [219, 0], [999, 3]]},
            {"key": "hr", "label": "Heart rate", "kind": "vital",
             "field": "hr", "news_bands": [
                 [40, 3], [50, 1], [90, 0], [110, 1], [130, 2], [999, 3]]},
            {"key": "temp", "label": "Temperature", "kind": "vital",
             "field": "temp_c", "news_bands": [
                 [35.0, 3], [36.0, 1], [38.0, 0], [39.0, 1], [999, 2]]},
            {"key": "consciousness", "label": "Consciousness",
             "kind": "vital", "field": "gcs_total",
             "test": "<15", "points": 3},
        ],
        "interpretation": [
            {"max": 4, "risk": "low", "text": "Continue routine monitoring."},
            {"min": 5, "max": 6, "risk": "medium",
             "text": "Urgent review by a clinician competent in acute "
                     "illness. Hourly observations."},
            {"min": 7, "risk": "high",
             "text": "Emergency assessment by a critical care team. "
                     "Continuous monitoring."},
        ],
    },

    # ---------------------------------------------------------------
    # Renal
    # ---------------------------------------------------------------
    "KDIGO_AKI": {
        "name": "KDIGO AKI Staging",
        "purpose": "Stages acute kidney injury by creatinine rise.",
        "citation": "KDIGO Clinical Practice Guideline, Kidney Int Suppl 2012",
        "type": "staging",
        "custom": "kdigo",
        "inputs": [
            {"key": "creat_now", "label": "Current creatinine",
             "kind": "lab", "field": "Creatinine", "raw": True},
            {"key": "creat_baseline", "label": "Admission creatinine",
             "kind": "lab", "field": "Creatinine", "raw": True,
             "aggregate": "first"},
            {"key": "creat_peak", "label": "Peak creatinine",
             "kind": "lab", "field": "Creatinine", "raw": True,
             "aggregate": "max"},
        ],
        "interpretation": [
            {"max": 0, "risk": "none", "text": "No AKI by creatinine criteria."},
            {"min": 1, "max": 1, "risk": "stage 1",
             "text": "1.5-1.9x the admission creatinine, or a rise of 0.3 "
                     "mg/dL or more. Avoid nephrotoxins; review dosing."},
            {"min": 2, "max": 2, "risk": "stage 2",
             "text": "2.0-2.9x the admission creatinine. Hold contrast and "
                     "nephrotoxic drugs. Adjust renally cleared doses."},
            {"min": 3, "risk": "stage 3",
             "text": "3x the admission value or more, or creatinine 4.0 or "
                     "above. Consider renal replacement."},
        ],
    },

    # ---------------------------------------------------------------
    # Shock
    # ---------------------------------------------------------------
    "SHOCK_INDEX": {
        "name": "Shock Index",
        "purpose": "Heart rate divided by systolic pressure. Rises before "
                   "blood pressure falls, which is why it detects "
                   "haemorrhage earlier than pressure alone.",
        "citation": "Allgower M, Burri C. Dtsch Med Wochenschr 1967",
        "type": "ratio",
        "custom": "shock_index",
        "inputs": [
            {"key": "hr", "label": "Heart rate", "kind": "vital",
             "field": "hr", "raw": True},
            {"key": "sbp", "label": "Systolic BP", "kind": "vital",
             "field": "sbp", "raw": True},
        ],
        "interpretation": [
            {"max": 0.9, "risk": "normal", "text": "Within normal range."},
            {"min": 0.9, "max": 1.3, "risk": "elevated",
             "text": "Concerning. Associated with occult hypoperfusion even "
                     "when blood pressure is still normal."},
            {"min": 1.3, "risk": "severe",
             "text": "Suggests significant volume loss. Predicts need for "
                     "massive transfusion in trauma."},
        ],
    },

    "GLASGOW_BLATCHFORD": {
        "name": "Glasgow-Blatchford Score",
        "purpose": "Whether upper GI bleeding needs intervention.",
        "citation": "Blatchford O et al. Lancet 2000;356(9238):1318-21",
        "type": "risk_stratify",
        "inputs": [
            {"key": "urea", "label": "Urea", "kind": "lab",
             "field": "Urea Nitrogen", "bands": [
                 [18.2, 0], [22.4, 2], [28, 3], [70, 4], [999, 6]]},
            {"key": "haemoglobin", "label": "Haemoglobin", "kind": "lab",
             "field": "Hemoglobin", "bands_desc": [
                 [10, 6], [12, 3], [13, 1], [999, 0]]},
            {"key": "sbp", "label": "Systolic BP", "kind": "vital",
             "field": "sbp", "bands_desc": [
                 [90, 3], [100, 2], [110, 1], [999, 0]]},
            {"key": "hr_100_plus", "label": "Heart rate 100 or over",
             "kind": "vital", "field": "hr", "test": ">=100", "points": 1},
            {"key": "melaena", "label": "Melaena",
             "kind": "clinical", "points": 1,
             "ask": "Black tarry stool?"},
            {"key": "syncope", "label": "Syncope",
             "kind": "clinical", "points": 2,
             "ask": "Any fainting?"},
            {"key": "liver_disease", "label": "Liver disease",
             "kind": "dx", "codes_9": ["571", "572"],
             "codes_10": ["K70", "K74"], "points": 2},
            {"key": "cardiac_failure", "label": "Cardiac failure",
             "kind": "dx", "codes_9": ["428"], "codes_10": ["I50"],
             "points": 2},
        ],
        "interpretation": [
            {"max": 0, "risk": "very low",
             "text": "Suitable for outpatient management."},
            {"min": 1, "max": 5, "risk": "low",
             "text": "Low risk of needing intervention."},
            {"min": 6, "risk": "high",
             "text": "50% or greater likelihood of needing transfusion, "
                     "endoscopy or surgery."},
        ],
    },

    "MEWS": {
        "name": "Modified Early Warning Score",
        "purpose": "Bedside deterioration score. Simpler than NEWS2 and "
                   "still widely used.",
        "citation": "Subbe CP et al. QJM 2001;94(10):521-6",
        "type": "risk_stratify",
        "inputs": [
            {"key": "sbp", "label": "Systolic BP", "kind": "vital",
             "field": "sbp", "news_bands_desc": [
                 [70, 3], [80, 2], [100, 1], [199, 0], [999, 2]]},
            {"key": "hr", "label": "Heart rate", "kind": "vital",
             "field": "hr", "news_bands": [
                 [40, 2], [50, 1], [100, 0], [110, 1], [129, 2], [999, 3]]},
            {"key": "rr", "label": "Respiratory rate", "kind": "vital",
             "field": "rr", "news_bands": [
                 [9, 2], [14, 0], [20, 1], [29, 2], [999, 3]]},
            {"key": "temp", "label": "Temperature", "kind": "vital",
             "field": "temp_c", "news_bands": [
                 [35, 2], [38.4, 0], [999, 2]]},
            {"key": "consciousness", "label": "Consciousness",
             "kind": "vital", "field": "gcs_total", "test": "<15",
             "points": 2},
        ],
        "interpretation": [
            {"max": 2, "risk": "low", "text": "Routine observation."},
            {"min": 3, "max": 4, "risk": "medium",
             "text": "Increase observation frequency; inform the nurse in "
                     "charge."},
            {"min": 5, "risk": "high",
             "text": "Urgent medical review. Consider critical care."},
        ],
    },

    "SOFA_RESP": {
        "name": "SOFA Respiratory Component",
        "purpose": "Respiratory organ dysfunction, by oxygenation.",
        "caution": "One component of the full SOFA score, which also "
                   "covers coagulation, liver, cardiovascular, CNS and "
                   "renal systems.",
        "citation": "Vincent JL et al. Intensive Care Med 1996;22(7):707-10",
        "type": "staging",
        "inputs": [
            {"key": "spo2", "label": "SpO2", "kind": "vital",
             "field": "spo2", "news_bands_desc": [
                 [80, 4], [88, 3], [92, 2], [95, 1], [100, 0]]},
        ],
        "interpretation": [
            {"max": 0, "risk": "none", "text": "No respiratory dysfunction."},
            {"min": 1, "max": 2, "risk": "mild",
             "text": "Mild respiratory dysfunction."},
            {"min": 3, "risk": "severe",
             "text": "Severe respiratory dysfunction. Likely needs "
                     "ventilatory support."},
        ],
    },

    "ANION_GAP": {
        "name": "Anion Gap",
        "purpose": "Distinguishes causes of metabolic acidosis.",
        "citation": "Standard clinical chemistry",
        "type": "ratio",
        "custom": "anion_gap",
        "inputs": [
            {"key": "sodium", "label": "Sodium", "kind": "lab",
             "field": "Sodium", "raw": True},
            {"key": "chloride", "label": "Chloride", "kind": "lab",
             "field": "Chloride", "raw": True},
            {"key": "bicarbonate", "label": "Bicarbonate", "kind": "lab",
             "field": "Bicarbonate", "raw": True},
        ],
        "interpretation": [
            {"max": 12, "risk": "normal",
             "text": "Normal. A normal-gap acidosis suggests bicarbonate "
                     "loss — diarrhoea or renal tubular acidosis."},
            {"min": 12, "max": 20, "risk": "raised",
             "text": "Raised. Consider lactate, ketones, renal failure."},
            {"min": 20, "risk": "high",
             "text": "High. Lactic acidosis, ketoacidosis, toxic alcohols "
                     "or severe renal failure."},
        ],
    },
}


# Words a clinician might use, mapped to the score they mean. The model
# usually picks correctly from the name, but a direct request should not
# depend on it.
ALIASES = {
    "perc": "PERC", "perc rule": "PERC",
    "wells": "WELLS_PE", "wells pe": "WELLS_PE", "wells score": "WELLS_PE",
    "heart": "HEART", "heart score": "HEART",
    "qsofa": "QSOFA", "q-sofa": "QSOFA",
    "sirs": "SIRS",
    "hasbled": "HAS_BLED", "has-bled": "HAS_BLED", "has bled": "HAS_BLED",
    "cha2ds2": "CHA2DS2_VASC", "chadsvasc": "CHA2DS2_VASC",
    "cha2ds2-vasc": "CHA2DS2_VASC", "chads": "CHA2DS2_VASC",
    "curb": "CURB65", "curb65": "CURB65", "curb-65": "CURB65",
    "news": "NEWS2", "news2": "NEWS2", "news 2": "NEWS2",
    "mews": "MEWS",
    "kdigo": "KDIGO_AKI", "aki": "KDIGO_AKI", "aki staging": "KDIGO_AKI",
    "shock index": "SHOCK_INDEX", "si": "SHOCK_INDEX",
    "blatchford": "GLASGOW_BLATCHFORD",
    "glasgow blatchford": "GLASGOW_BLATCHFORD", "gbs": "GLASGOW_BLATCHFORD",
    "sofa": "SOFA_RESP",
    "anion gap": "ANION_GAP", "ag": "ANION_GAP",
}


# Which scores are worth suggesting for which kind of presentation.
BY_PRESENTATION = {
    "chest pain": ["HEART", "WELLS_PE", "PERC"],
    "shortness of breath": ["WELLS_PE", "PERC", "CURB65", "NEWS2"],
    "bleeding": ["SHOCK_INDEX", "GLASGOW_BLATCHFORD", "HAS_BLED"],
    "trauma": ["SHOCK_INDEX", "NEWS2"],
    "infection": ["QSOFA", "SIRS", "CURB65", "NEWS2"],
    "atrial fibrillation": ["CHA2DS2_VASC", "HAS_BLED"],
    "confusion": ["NEWS2", "QSOFA", "CURB65"],
    "renal": ["KDIGO_AKI", "ANION_GAP"],
    "deterioration": ["NEWS2", "MEWS", "QSOFA", "SHOCK_INDEX"],
}


def resolve(name):
    """A clinician's wording to a score key."""
    if not name:
        return None
    n = name.strip().lower()
    if n.upper().replace("-", "_").replace(" ", "_") in SCORES:
        return n.upper().replace("-", "_").replace(" ", "_")
    return ALIASES.get(n)


def list_scores():
    return [{"key": k, "name": v["name"], "purpose": v["purpose"]}
            for k, v in sorted(SCORES.items())]