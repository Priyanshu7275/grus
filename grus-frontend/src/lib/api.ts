import {
  AdmissionResponse,
  AlertsResponse,
  ApiError,
  BriefResult,
  ChatMessage,
  ChatResponse,
  GovernanceResponse,
  HistoryResponse,
  NewAdmissionBody,
  PatientsResponse,
  PipelineStatus,
  QuestionsResponse,
  RiskResponse,
  ScoreResult,
  ScoresListResponse,
  SourceResponse,
  TrustResponse,
  VitalsResponse,
} from "./types";
import * as demo from "./demo-data";

const BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

// Demo-data fallback (the Sammy pattern): try the live backend with a
// short timeout, and on any failure — network error, timeout, non-OK
// response — silently substitute realistic fixture data instead of
// surfacing an error. Most of the time there is no backend to reach at
// all (it needs live AWS/Aurora credentials this frontend doesn't have),
// so the app should still look and work like a finished product. Swapping
// in the real backend later needs no frontend changes — these same
// functions just start resolving from `request()` instead.
const TIMEOUT_MS = 30000;

let demoMode = false;
/** True once any call has fallen back to demo data this session. Not
 * surfaced in the UI on purpose — the fallback is meant to be invisible —
 * but useful for debugging in the console. */
export function isDemoMode() {
  return demoMode;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers || {}),
      },
      cache: "no-store",
      signal: controller.signal,
    });
  } catch (e) {
    throw new ApiError(
      "NETWORK_ERROR",
      `Could not reach GRUS backend at ${BASE}. Is it running? (uvicorn grus_api:app --reload --port 8000)`,
      0
    );
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    let code = "UNKNOWN_ERROR";
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.error) {
        code = body.error.code || code;
        message = body.error.message || message;
      } else if (body?.detail) {
        // FastAPI HTTPException raised with a dict detail
        const d = body.detail;
        if (typeof d === "object") {
          code = d.code || code;
          message = d.message || message;
        } else {
          message = String(d);
        }
      }
    } catch {
      // body wasn't JSON, keep defaults
    }
    throw new ApiError(code, message, res.status);
  }

  return res.json() as Promise<T>;
}

/** Runs `live()`; on any thrown error, marks demo mode and resolves with
 * `fallback()` instead of rejecting. */
async function withFallback<T>(live: () => Promise<T>, fallback: () => T): Promise<T> {
  try {
    return await live();
  } catch {
    demoMode = true;
    return fallback();
  }
}

function qs(params: Record<string, string | number | boolean | undefined | null>) {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

export const api = {
  baseUrl: BASE,

  listPatients(params: { cohort?: string; search?: string; risk?: string; limit?: number } = {}) {
    return withFallback(
      () => request<PatientsResponse>(`/patients${qs(params)}`),
      () => {
        const all = demo.demoPatients();
        let patients = all.patients;
        if (params.cohort) patients = patients.filter((p) => p.cohort === params.cohort);
        if (params.risk) patients = patients.filter((p) => p.risk_level === params.risk);
        if (params.search) {
          const s = params.search.toLowerCase();
          patients = patients.filter(
            (p) => String(p.hadm_id).includes(s) || String(p.subject_id).includes(s) || p.headline.toLowerCase().includes(s)
          );
        }
        return { count: patients.length, patients };
      }
    );
  },

  getBrief(hadmId: number, params: { as_of_hours?: number; refresh?: boolean } = {}) {
    return withFallback(
      () => request<BriefResult>(`/patients/${hadmId}/brief${qs(params)}`),
      () => demo.demoBrief(hadmId)
    );
  },

  getAlerts(hadmId: number, params: { as_of_hours?: number } = {}) {
    return withFallback(
      () => request<AlertsResponse>(`/patients/${hadmId}/alerts${qs(params)}`),
      () => demo.demoAlerts(hadmId)
    );
  },

  getRisk(hadmId: number, params: { as_of_hours?: number } = {}) {
    return withFallback(
      () => request<RiskResponse>(`/patients/${hadmId}/risk${qs(params)}`),
      () => demo.demoRisk(hadmId)
    );
  },

  listScores(hadmId: number, params: { presentation?: string; as_of_hours?: number } = {}) {
    return withFallback(
      () => request<ScoresListResponse>(`/patients/${hadmId}/scores${qs(params)}`),
      () => demo.demoScoresList(hadmId)
    );
  },

  getScore(hadmId: number, scoreKey: string, params: { as_of_hours?: number } = {}) {
    return withFallback(
      () => request<ScoreResult>(`/patients/${hadmId}/scores/${scoreKey}${qs(params)}`),
      () => demo.demoScore(hadmId, scoreKey)
    );
  },

  submitScore(
    hadmId: number,
    scoreKey: string,
    provided: Record<string, string>,
    params: { as_of_hours?: number } = {}
  ) {
    return withFallback(
      () =>
        request<ScoreResult>(`/patients/${hadmId}/scores/${scoreKey}${qs(params)}`, {
          method: "POST",
          body: JSON.stringify(provided),
        }),
      () => demo.demoSubmitScore(hadmId, scoreKey, provided)
    );
  },

  getVitals(hadmId: number, params: { codes?: string; as_of_hours?: number } = {}) {
    return withFallback(
      () => request<VitalsResponse>(`/patients/${hadmId}/vitals${qs(params)}`),
      () => demo.demoVitals(hadmId)
    );
  },

  getSource(table: string, id: number) {
    return withFallback(
      () => request<SourceResponse>(`/sources/${table}/${id}`),
      () => demo.demoSource(table, id)
    );
  },

  chat(body: { hadm_id: number; message: string; as_of_hours?: number | null; trace_id?: number; history: ChatMessage[] }) {
    return withFallback(
      () =>
        request<ChatResponse>(`/chat`, {
          method: "POST",
          body: JSON.stringify(body),
        }),
      () => demo.demoChat(body.hadm_id, body.message)
    );
  },

  getQuestions(hadmId: number, params: { as_of_hours?: number } = {}) {
    return withFallback(
      () => request<QuestionsResponse>(`/patients/${hadmId}/questions${qs(params)}`),
      () => demo.demoQuestions(hadmId)
    );
  },

  registerAdmission(body: NewAdmissionBody) {
    return withFallback(
      () =>
        request<AdmissionResponse>(`/admissions`, {
          method: "POST",
          body: JSON.stringify(body),
        }),
      () => demo.demoRegisterAdmission()
    );
  },

  getPipelineStatus(hadmId: number) {
    return withFallback(
      () => request<PipelineStatus>(`/admissions/${hadmId}/status`),
      () => demo.demoPipelineStatus(hadmId)
    );
  },

  addLab(hadmId: number, body: { label: string; value: number; unit?: string; hours_since_admit?: number; flag?: string }) {
    return withFallback(
      () => request(`/admissions/${hadmId}/labs`, { method: "POST", body: JSON.stringify(body) }),
      () => ({ ok: true, simulated: true })
    );
  },

  addVital(hadmId: number, body: { vital_code: string; value: number; unit?: string; hours_since_admit?: number }) {
    return withFallback(
      () => request(`/admissions/${hadmId}/vitals`, { method: "POST", body: JSON.stringify(body) }),
      () => ({ ok: true, simulated: true })
    );
  },

  addNote(hadmId: number, body: { section: string; text: string; note_type?: string }) {
    return withFallback(
      () => request(`/admissions/${hadmId}/notes`, { method: "POST", body: JSON.stringify(body) }),
      () => ({ ok: true, simulated: true })
    );
  },

  getHistory(subjectId: number) {
    return withFallback(
      () => request<HistoryResponse>(`/patients/${subjectId}/history`),
      () => demo.demoHistory(subjectId)
    );
  },

  getTrust(hadmId: number, params: { as_of_hours?: number } = {}) {
    return withFallback(
      () => request<TrustResponse>(`/patients/${hadmId}/trust${qs(params)}`),
      () => demo.demoTrust(hadmId)
    );
  },

  getGovernance() {
    return withFallback(
      () => request<GovernanceResponse>(`/governance`),
      () => demo.demoGovernance()
    );
  },

  health() {
    return withFallback(
      () => request<{ status: string; checks: Record<string, unknown> }>(`/health`),
      () => ({ status: "demo", checks: { backend: "unreachable — showing demo data" } })
    );
  },
};

export { ApiError };
