-- =====================================================================
-- GRUS — Aurora PostgreSQL Schema
-- Emergency-medicine agent, AWS Agents for Humans
--
-- Design driven by the twelve ETL findings in the Phase 1 conclusion.
-- Every table carries a stable row id so the Verifier agent can cite it.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE SCHEMA IF NOT EXISTS grus;
SET search_path TO grus, public;


-- =====================================================================
-- 1. PATIENTS
-- =====================================================================

CREATE TABLE patients (
    subject_id      BIGINT PRIMARY KEY,
    gender          CHAR(1),
    anchor_age      INT,
    dod             TIMESTAMP,              -- date of death, null if alive
    ingested_at     TIMESTAMP NOT NULL DEFAULT now()
);


-- =====================================================================
-- 2. ADMISSIONS
-- One hospital visit. Both durations stored (finding #6).
-- =====================================================================

CREATE TABLE admissions (
    hadm_id             BIGINT PRIMARY KEY,
    subject_id          BIGINT NOT NULL REFERENCES patients(subject_id),
    admittime           TIMESTAMP NOT NULL,     -- the anchor for all hours_since_admit
    dischtime           TIMESTAMP,
    admission_type      TEXT,
    admission_location  TEXT,
    discharge_location  TEXT,
    insurance           TEXT,
    hospital_expire_flag SMALLINT,
    hosp_days           NUMERIC(6,2),           -- derived, finding #6
    icu_days            NUMERIC(6,2),           -- derived, finding #6
    arrival_unit        TEXT,                   -- first careunit from transfers
    cohort              TEXT,                   -- trauma | cardiac | sepsis | respiratory | other
    ingested_at         TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX idx_adm_subject  ON admissions(subject_id);
CREATE INDEX idx_adm_cohort   ON admissions(cohort);
CREATE INDEX idx_adm_admittime ON admissions(admittime DESC);


-- =====================================================================
-- 3. ICU STAYS
-- Third ID level (finding #2). Vitals key on stay_id, never hadm_id.
-- =====================================================================

CREATE TABLE icu_stays (
    stay_id         BIGINT PRIMARY KEY,
    hadm_id         BIGINT NOT NULL REFERENCES admissions(hadm_id),
    subject_id      BIGINT NOT NULL REFERENCES patients(subject_id),
    first_careunit  TEXT,
    last_careunit   TEXT,
    intime          TIMESTAMP,
    outtime         TIMESTAMP,
    los_days        NUMERIC(6,2),
    stay_rank       SMALLINT,               -- 1 = first ICU stay of this admission
    ingested_at     TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX idx_icu_hadm ON icu_stays(hadm_id);


-- =====================================================================
-- 4. TRANSFERS
-- Ward-by-ward movement. This is what exposed the ED gap (finding #7).
-- =====================================================================

CREATE TABLE transfers (
    transfer_id         BIGSERIAL PRIMARY KEY,
    hadm_id             BIGINT NOT NULL REFERENCES admissions(hadm_id),
    subject_id          BIGINT NOT NULL,
    careunit            TEXT,                   -- nullable: real gaps exist (finding #8)
    intime              TIMESTAMP,
    outtime             TIMESTAMP,
    hours_since_admit   NUMERIC(8,2),           -- finding #3
    seq_num             SMALLINT
);

CREATE INDEX idx_tr_hadm ON transfers(hadm_id, hours_since_admit);


-- =====================================================================
-- 5. DIAGNOSES
-- Titles resolved at load so GRUS never joins a dictionary at runtime.
-- =====================================================================

CREATE TABLE diagnoses (
    diagnosis_id    BIGSERIAL PRIMARY KEY,
    hadm_id         BIGINT NOT NULL REFERENCES admissions(hadm_id),
    subject_id      BIGINT NOT NULL,
    seq_num         SMALLINT,
    icd_code        TEXT NOT NULL,
    icd_version     SMALLINT NOT NULL,
    long_title      TEXT,
    is_external_cause BOOLEAN DEFAULT FALSE     -- E8xx / V4x / W0x: how it happened
);

CREATE INDEX idx_dx_hadm ON diagnoses(hadm_id, seq_num);
CREATE INDEX idx_dx_code ON diagnoses(icd_code);


-- =====================================================================
-- 6. PROCEDURES
-- =====================================================================

CREATE TABLE procedures (
    procedure_id    BIGSERIAL PRIMARY KEY,
    hadm_id         BIGINT NOT NULL REFERENCES admissions(hadm_id),
    subject_id      BIGINT NOT NULL,
    seq_num         SMALLINT,
    icd_code        TEXT NOT NULL,
    icd_version     SMALLINT NOT NULL,
    long_title      TEXT,
    chartdate       DATE,
    hours_since_admit NUMERIC(8,2)
);

CREATE INDEX idx_proc_hadm ON procedures(hadm_id);


-- =====================================================================
-- 7. MEDICATIONS
-- Collapsed courses (finding #1), normalized names (finding #4),
-- status carried from pharmacy (finding #10), timestamps validated (finding #9).
-- =====================================================================

CREATE TABLE medications (
    medication_id       BIGSERIAL PRIMARY KEY,
    hadm_id             BIGINT NOT NULL REFERENCES admissions(hadm_id),
    subject_id          BIGINT NOT NULL,
    drug_raw            TEXT,                   -- as written: '*NF* Warfarin'
    drug_normalized     TEXT,                   -- 'warfarin'
    drug_class          TEXT,                   -- 'anticoagulant', 'nsaid', 'opioid'
    route               TEXT,
    dose_val_rx         TEXT,
    dose_unit_rx        TEXT,
    starttime           TIMESTAMP,
    stoptime            TIMESTAMP,
    start_hours         NUMERIC(8,2),
    stop_hours          NUMERIC(8,2),
    status              TEXT,                   -- active | discontinued | expired | unknown
    is_active           TEXT,              
    source_table        TEXT NOT NULL,          -- prescriptions | pharmacy | inputevents (finding #5)
    time_valid          BOOLEAN DEFAULT TRUE,   -- FALSE when stop < start (finding #9)
    n_orders            INT DEFAULT 1           -- how many rows collapsed into this course
);

CREATE INDEX idx_med_hadm       ON medications(hadm_id, start_hours);
CREATE INDEX idx_med_norm       ON medications(drug_normalized);
CREATE INDEX idx_med_class      ON medications(drug_class) WHERE drug_class IS NOT NULL;
CREATE INDEX idx_med_active ON medications(hadm_id) WHERE is_active = 'active';


-- =====================================================================
-- 8. LABS
-- =====================================================================

CREATE TABLE labs (
    lab_id              BIGSERIAL PRIMARY KEY,
    hadm_id             BIGINT NOT NULL REFERENCES admissions(hadm_id),
    subject_id          BIGINT NOT NULL,
    itemid              BIGINT,
    label               TEXT NOT NULL,          -- 'INR(PT)', resolved at load
    category            TEXT,                   -- 'coagulation', 'hematology', 'chemistry'
    charttime           TIMESTAMP,
    hours_since_admit   NUMERIC(8,2),
    valuenum            NUMERIC(12,4),
    valueuom            TEXT,
    ref_range_lower     NUMERIC(12,4),
    ref_range_upper     NUMERIC(12,4),
    flag                TEXT,                   -- 'abnormal' or null
    is_first_of_stay    BOOLEAN DEFAULT FALSE   -- the arrival value
);

CREATE INDEX idx_lab_hadm       ON labs(hadm_id, hours_since_admit);
CREATE INDEX idx_lab_label      ON labs(hadm_id, label, hours_since_admit);
CREATE INDEX idx_lab_first      ON labs(hadm_id, label) WHERE is_first_of_stay;


-- =====================================================================
-- 9. VITALS
-- Keyed on stay_id (finding #2). Hourly, not continuous (finding #7).
-- =====================================================================

CREATE TABLE vitals (
    vital_id            BIGSERIAL PRIMARY KEY,
    stay_id             BIGINT NOT NULL REFERENCES icu_stays(stay_id),
    hadm_id             BIGINT NOT NULL,
    subject_id          BIGINT NOT NULL,
    itemid              BIGINT,
    label               TEXT NOT NULL,          -- 'Heart Rate', resolved at load
    vital_code          TEXT,                   -- 'hr','sbp','dbp','map','spo2','rr','temp','gcs_v','gcs_m'
    charttime           TIMESTAMP,
    hours_since_admit   NUMERIC(8,2),
    valuenum            NUMERIC(12,4),
    valueuom            TEXT
);

CREATE INDEX idx_vit_stay   ON vitals(stay_id, hours_since_admit);
CREATE INDEX idx_vit_code   ON vitals(stay_id, vital_code, hours_since_admit);


-- =====================================================================
-- 10. OUTPUTS
-- Drain and urine volumes. Nearly missed; holds the 1000 ml (finding #11).
-- =====================================================================

CREATE TABLE outputs (
    output_id           BIGSERIAL PRIMARY KEY,
    stay_id             BIGINT REFERENCES icu_stays(stay_id),
    hadm_id             BIGINT NOT NULL REFERENCES admissions(hadm_id),
    subject_id          BIGINT NOT NULL,
    label               TEXT NOT NULL,          -- 'Pericardial', 'Void'
    charttime           TIMESTAMP,
    hours_since_admit   NUMERIC(8,2),
    value               NUMERIC(12,2),
    valueuom            TEXT
);

CREATE INDEX idx_out_hadm ON outputs(hadm_id, hours_since_admit);


-- =====================================================================
-- 11. NOTES + CHUNKS
-- Section-aware chunking, metadata filtering, pgvector index.
-- =====================================================================

CREATE TABLE notes (
    note_id             TEXT PRIMARY KEY,
    hadm_id             BIGINT REFERENCES admissions(hadm_id),
    subject_id          BIGINT NOT NULL,
    note_type           TEXT,                   -- 'discharge' | 'radiology'
    charttime           TIMESTAMP,
    hours_since_admit   NUMERIC(8,2),
    text                TEXT
);

CREATE INDEX idx_note_hadm ON notes(hadm_id);

CREATE TABLE note_chunks (
    chunk_id            BIGSERIAL PRIMARY KEY,
    note_id             TEXT NOT NULL REFERENCES notes(note_id),
    hadm_id             BIGINT,
    subject_id          BIGINT NOT NULL,
    note_type           TEXT,
    section             TEXT,                   -- 'HPI','PMH','Meds on Discharge'
    chunk_index         SMALLINT,
    charttime           TIMESTAMP,
    text                TEXT NOT NULL,
    embedding           VECTOR(768)
);

-- Metadata first: filter by patient before searching vectors.
CREATE INDEX idx_chunk_subject ON note_chunks(subject_id);
CREATE INDEX idx_chunk_hadm    ON note_chunks(hadm_id);
CREATE INDEX idx_chunk_section ON note_chunks(section);

CREATE INDEX idx_chunk_vec ON note_chunks
    USING hnsw (embedding vector_cosine_ops);


-- =====================================================================
-- 12. ALERT TRACES
-- Written BEFORE any text is generated. The chat reads this back,
-- so the model cannot invent a reason.
-- =====================================================================

CREATE TABLE alert_traces (
    trace_id            BIGSERIAL PRIMARY KEY,
    hadm_id             BIGINT NOT NULL REFERENCES admissions(hadm_id),
    subject_id          BIGINT NOT NULL,
    alert_code          TEXT NOT NULL,          -- 'ANTICOAG_TRAUMA','SHOCK_INDEX'
    severity            TEXT NOT NULL,          -- critical | warning | info | unknown
    rule_version        TEXT,
    fired_at            TIMESTAMP NOT NULL DEFAULT now(),
    inputs              JSONB NOT NULL,         -- the values the rule saw
    source_row_ids      JSONB NOT NULL,         -- [{"table":"labs","id":8812}]
    model_score         NUMERIC(6,4),
    shap_factors        JSONB,
    generated_text      TEXT,                   -- written after, from this trace
    acted_on            BOOLEAN,                -- clinician feedback
    dismissed_at        TIMESTAMP
);

CREATE INDEX idx_trace_hadm ON alert_traces(hadm_id, fired_at DESC);
CREATE INDEX idx_trace_code ON alert_traces(alert_code);


-- =====================================================================
-- 13. CRITICAL UNKNOWNS
-- The inverted output. What is missing and dangerous.
-- =====================================================================

CREATE TABLE critical_unknowns (
    unknown_id          BIGSERIAL PRIMARY KEY,
    hadm_id             BIGINT NOT NULL REFERENCES admissions(hadm_id),
    subject_id          BIGINT NOT NULL,
    unknown_code        TEXT NOT NULL,          -- 'REVERSAL_UNDOCUMENTED','NO_BASELINE_VITALS'
    description         TEXT NOT NULL,
    why_it_matters      TEXT,
    evidence            JSONB,                  -- what led us to suspect the gap
    tables_checked      JSONB,                  -- proves the search was exhaustive
    detected_at         TIMESTAMP NOT NULL DEFAULT now(),
    resolved            BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_unk_hadm ON critical_unknowns(hadm_id);


-- =====================================================================
-- 14. AUDIT LOG
-- Immutable. Every retrieval, every claim, who and when.
-- =====================================================================

CREATE TABLE audit_log (
    audit_id            BIGSERIAL PRIMARY KEY,
    occurred_at         TIMESTAMP NOT NULL DEFAULT now(),
    actor               TEXT NOT NULL,          -- cognito sub or agent name
    actor_type          TEXT NOT NULL,          -- clinician | agent | system
    action              TEXT NOT NULL,          -- retrieve | generate_brief | chat | break_glass
    subject_id          BIGINT,
    hadm_id             BIGINT,
    tables_accessed     JSONB,
    row_ids_returned    JSONB,
    claims_made         INT,
    claims_sourced      INT,
    claims_abstained    INT,                    -- feeds the trust panel
    break_glass         BOOLEAN DEFAULT FALSE,
    request_id          TEXT
);

CREATE INDEX idx_audit_time    ON audit_log(occurred_at DESC);
CREATE INDEX idx_audit_actor   ON audit_log(actor, occurred_at DESC);
CREATE INDEX idx_audit_patient ON audit_log(subject_id, occurred_at DESC);

REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC;


-- =====================================================================
-- 15. MODEL REGISTRY MIRROR
-- Champion/challenger state for the governance page.
-- =====================================================================

CREATE TABLE model_versions (
    model_version_id    BIGSERIAL PRIMARY KEY,
    model_name          TEXT NOT NULL,
    version             TEXT NOT NULL,
    role                TEXT NOT NULL,          -- champion | challenger | retired
    trained_at          TIMESTAMP,
    auc                 NUMERIC(6,4),
    calibration_error   NUMERIC(6,4),
    subgroup_metrics    JSONB,
    passed_gates        BOOLEAN,
    approved_by         TEXT,
    approved_at         TIMESTAMP,
    sagemaker_arn       TEXT,
    UNIQUE (model_name, version)
);


-- =====================================================================
-- CONVENIENCE VIEW — arrival snapshot
-- The brief is built from arrival values, so make that one query.
-- =====================================================================

CREATE VIEW v_arrival_labs AS
SELECT hadm_id, subject_id, label, valuenum, valueuom, flag, hours_since_admit
FROM labs
WHERE is_first_of_stay;