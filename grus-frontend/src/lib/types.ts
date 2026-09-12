// Types mirror grus_api.py response shapes (and the frontend brief doc).
// Kept permissive (optional fields) because the backend is actively
// changing — see "What changed from the earlier contract" in the brief.

export type Severity = "critical" | "warning" | "info" | "unknown";
export type RiskLevel = "critical" | "high" | "moderate" | "low" | "unknown";

export interface SourceRef {
  table: string;
  id: number;
  label?: string;
  section?: string;
  hours?: number;
}

// -------------------- /patients --------------------
export interface PatientCard {
  hadm_id: number;
  subject_id: number;
  stay_id?: number | null;
  age?: number | null;
  gender?: string | null;
  cohort?: string | null;
  arrival_unit?: string | null;
  admission_type?: string | null;
  hours_since_arrival?: number | null;
  risk_level: RiskLevel;
  risk_score?: number;
  alert_count: number;
  unknown_count: number;
  headline: string;
  prior_admissions: number;
  status?: string;
}

export interface PatientsResponse {
  count: number;
  patients: PatientCard[];
}

// -------------------- /brief --------------------
export interface TrustBlock {
  citations_valid?: number;
  citations_invalid?: number;
  traceable_pct?: number;
  rejected?: string[];
  // older contract shape
  claims_made?: number;
  claims_sourced?: number;
  claims_abstained?: number;
}

export interface AlertItem {
  code?: string;
  severity: Severity;
  title: string;
  detail?: string;
  body?: string;
  action?: string;
  inputs?: Record<string, unknown>;
  sources: SourceRef[];
  trace_id?: number;
  note?: string;
}

export interface BriefResponse {
  hadm_id: number;
  as_of_hours?: number | null;
  generated_at?: string;
  generation_ms?: number;
  brief: string;
  agents_run?: string[];
  trust?: TrustBlock;
  cached?: boolean;
  disclaimer?: string;
  degraded?: false;
}

export interface BriefDegraded {
  hadm_id: number;
  as_of_hours?: number | null;
  degraded: true;
  error?: string;
  message?: string;
  alerts: AlertItem[];
  disclaimer?: string;
}

export type BriefResult = BriefResponse | BriefDegraded;

// -------------------- /alerts --------------------
export interface AlertsResponse {
  hadm_id: number;
  as_of_hours?: number | null;
  alerts: AlertItem[];
  counts: { critical: number; warning: number; unknown: number; info: number };
}

// -------------------- /risk --------------------
export interface RiskPrediction {
  label: string;
  available: boolean;
  probability?: number;
  threshold?: number;
  alert?: boolean;
  confidence?: string;
  action?: string;
  reason?: string;
  model_performance?: {
    auc?: number;
    precision?: number;
    recall?: number;
    note?: string;
  };
}

export interface RiskResponse {
  available: boolean;
  scored_at_hour?: number;
  feature_coverage?: number;
  coverage_note?: string;
  predictions?: RiskPrediction[];
  reason?: string;
}

// -------------------- /vitals --------------------
export interface VitalPoint {
  hours: number;
  value: number;
  age_hours?: number;
}

export interface VitalSeries {
  code: string;
  label: string;
  unit?: string | null;
  normal_range?: [number, number] | null;
  points: VitalPoint[];
}

export interface DerivedSeries {
  code: string;
  label: string;
  thresholds?: { concern?: number; severe?: number };
  points: VitalPoint[];
}

export interface VitalsResponse {
  hadm_id: number;
  sampling?: string;
  sampling_note?: string;
  series: VitalSeries[];
  derived: DerivedSeries[];
}

// -------------------- /sources --------------------
export interface SourceResponse {
  table: string;
  id: number;
  row: Record<string, unknown>;
  provenance?: { source_dataset?: string; source_table?: string; ingested_at?: string; note_id?: string };
  context?: string;
  highlight?: string[];
}

// -------------------- /chat --------------------
export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatRequestBody {
  hadm_id: number;
  message: string;
  as_of_hours?: number | null;
  trace_id?: number;
  history: ChatMessage[];
}

export interface ChatAbstained {
  claim: string;
  reason: string;
}

export interface ToolCall {
  tool: string;
  args?: Record<string, unknown>;
  found?: boolean;
}

export interface ChatResponse {
  answer: string;
  sources?: (string | SourceRef)[];
  abstained?: ChatAbstained[];
  tools_called?: ToolCall[];
  retrieval?: Record<string, unknown>;
  confidence?: number;
  latency_ms?: number;
  trace_id?: number;
}

export interface QuestionsResponse {
  hadm_id: number;
  questions: string[];
  note?: string;
}

// -------------------- /admissions --------------------
export interface NewAdmissionBody {
  age: number;
  gender: "M" | "F";
  admission_type?: string;
  arrival_unit?: string;
  chief_complaint?: string;
  subject_id?: number;
  simulated?: boolean;
}

export interface AdmissionResponse {
  hadm_id: number;
  subject_id: number;
  status: string;
  simulated: boolean;
  pipeline: string;
  note?: string;
}

export interface PipelineStatus {
  hadm_id: number;
  stage: "registered" | "embedding" | "rules" | "brief" | "ready" | "failed" | "unknown";
  alerts?: number;
  elapsed_s?: number;
  chunks_embedded?: number;
  embedding_error?: string;
  brief_error?: string;
  error?: string;
  note?: string;
  started?: number;
}

// -------------------- /history --------------------
export interface HistoryAdmission {
  hadm_id: number;
  is_current: boolean;
  admission_type?: string;
  length_of_stay_days?: number | null;
  diagnoses: { title: string; source: string }[];
}

export interface RecurringCondition {
  condition: string;
  visits: number;
  source: string;
}

export interface HistoryResponse {
  subject_id: number;
  prior_admissions: number;
  admissions: HistoryAdmission[];
  recurring_conditions: RecurringCondition[];
  note: string | null;
}

// -------------------- /trust --------------------
export interface TrustResponse {
  hadm_id: number;
  available?: boolean;
  citations_valid?: number;
  citations_invalid?: number;
  traceable_pct?: number;
  rejected?: string[];
  claims_made?: number;
  claims_sourced?: number;
  claims_abstained?: number;
  last_updated?: string;
  note?: string;
}

// -------------------- /scores --------------------
export interface ScoreSummary {
  key: string;
  name: string;
  purpose: string;
}

export interface ScoresListResponse {
  hadm_id: number;
  suggested: ScoreSummary[];
  all_available: ScoreSummary[];
}

export interface ScoreFoundItem {
  label: string;
  value: unknown;
  points: number;
  source?: string | null; // "table#id"
}

export interface ScoreMissingItem {
  label: string;
  ask?: string;
  why?: string;
  key?: string; // not currently sent by the backend — see lib/scoreSpecs.ts
}

export interface ScoreComponents {
  found: ScoreFoundItem[];
  missing: ScoreMissingItem[];
}

export interface ScoreResult {
  score: string;
  name: string;
  purpose: string;
  citation: string;
  complete: boolean;
  components: ScoreComponents;
  total?: number;
  max_possible?: number;
  risk?: string;
  interpretation?: string;
  caution?: string;
  note?: string;
  sources?: SourceRef[];
}

// -------------------- /governance --------------------
export interface ModelEntry {
  name: string;
  auc?: number;
  average_precision?: number;
  precision?: number;
  recall?: number;
  threshold?: number;
  threshold_policy?: string;
  calibration_error?: number;
  alert_rate?: number;
  positive_rate?: number;
  subgroup_auc_gap?: number;
  subgroups?: Record<string, unknown>;
  top_features?: unknown[];
  status?: string;
  rejected_because?: string[];
}

export interface GovernanceResponse {
  models: ModelEntry[];
  rejected: ModelEntry[];
  registry?: { version: number; status: string }[];
  registry_error?: string;
  policy?: string;
  note?: string;
}

// -------------------- errors --------------------
export interface ApiErrorShape {
  error: { code: string; message: string; status?: number };
}

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}
