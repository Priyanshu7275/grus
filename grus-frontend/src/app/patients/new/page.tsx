"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  UserPlus,
  Loader2,
  CheckCircle2,
  ArrowRight,
  ArrowLeft,
  AlertTriangle,
  FlaskConical,
} from "lucide-react";
import { api } from "@/lib/api";
import { AdmissionResponse, PipelineStatus } from "@/lib/types";

/** The pipeline stages the backend reports, in the order they happen. */
const STAGE_ORDER = ["registered", "embedding", "rules", "brief", "ready"] as const;

const STAGE_LABEL: Record<string, string> = {
  registered: "Registered",
  embedding: "Embedding the note",
  rules: "Running rule checks",
  brief: "Generating the brief",
  ready: "Ready",
  failed: "Failed",
  unknown: "Working",
};

export default function NewPatientPage() {
  const router = useRouter();

  // form fields
  const [age, setAge] = useState("");
  const [gender, setGender] = useState<"M" | "F">("M");
  const [admissionType, setAdmissionType] = useState("EW EMER.");
  const [arrivalUnit, setArrivalUnit] = useState("Emergency Department");
  const [chiefComplaint, setChiefComplaint] = useState("");

  // submission + polling state
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [admission, setAdmission] = useState<AdmissionResponse | null>(null);
  const [status, setStatus] = useState<PipelineStatus | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stage = status?.stage ?? (admission ? "registered" : null);
  const isReady = stage === "ready";
  const isFailed = stage === "failed";
  const isWorking = admission !== null && !isReady && !isFailed;

  // Poll the pipeline every 2.5s once we have an hadm_id, until ready/failed.
  useEffect(() => {
    if (!admission) return;

    let cancelled = false;

    async function check() {
      try {
        const s = await api.getPipelineStatus(admission!.hadm_id);
        if (!cancelled) setStatus(s);
      } catch {
        // a single failed poll isn't fatal — keep trying
      }
    }

    check();
    pollRef.current = setInterval(check, 2500);
    tickRef.current = setInterval(() => setElapsed((e) => e + 1), 1000);

    return () => {
      cancelled = true;
      if (pollRef.current) clearInterval(pollRef.current);
      if (tickRef.current) clearInterval(tickRef.current);
    };
  }, [admission]);

  // Stop polling once the pipeline finishes.
  useEffect(() => {
    if (isReady || isFailed) {
      if (pollRef.current) clearInterval(pollRef.current);
      if (tickRef.current) clearInterval(tickRef.current);
    }
  }, [isReady, isFailed]);

  async function submit() {
    setError("");

    const ageNum = Number(age);
    if (!age.trim() || Number.isNaN(ageNum) || ageNum <= 0 || ageNum > 120) {
      setError("Enter an age between 1 and 120.");
      return;
    }

    setSubmitting(true);
    try {
      const res = await api.registerAdmission({
        age: ageNum,
        gender,
        admission_type: admissionType.trim() || undefined,
        arrival_unit: arrivalUnit.trim() || undefined,
        chief_complaint: chiefComplaint.trim() || undefined,
        simulated: true,
      });
      setAdmission(res);
      setElapsed(0);
    } catch {
      setError("Could not register the patient. Check that the backend is reachable.");
    } finally {
      setSubmitting(false);
    }
  }

  const stageIndex = stage ? STAGE_ORDER.indexOf(stage as (typeof STAGE_ORDER)[number]) : -1;

  return (
    <div className="mx-auto max-w-2xl px-4 py-8 sm:px-6">
      <Link
        href="/patients"
        className="mb-6 inline-flex items-center gap-1.5 text-sm font-medium text-ink-500 transition hover:text-ink-900"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to cohort board
      </Link>

      <div className="mb-6">
        <h1 className="flex items-center gap-2 text-xl font-bold text-ink-900">
          <UserPlus className="h-5 w-5 text-brand-deep" />
          Register a new patient
        </h1>
        <p className="mt-1 text-sm text-ink-500">
          Creates a simulated admission and runs it through the full agent pipeline — the same one
          used for every patient on the board.
        </p>
      </div>

      <div className="mb-5 flex items-start gap-2 rounded-xl border border-sev-warning/30 bg-sev-warningBg px-4 py-3 text-sm text-sev-warning">
        <FlaskConical className="mt-0.5 h-4 w-4 shrink-0" />
        <span>
          Patients registered here are flagged <strong>simulated</strong> — they are demo records,
          kept distinct from the historical dataset.
        </span>
      </div>

      {/* ---------------- form ---------------- */}
      {!admission && (
        <div className="rounded-2xl glass p-6 shadow-soft">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink-700">Age</label>
              <input
                type="number"
                min={1}
                max={120}
                value={age}
                onChange={(e) => setAge(e.target.value)}
                placeholder="34"
                className="field-input w-full"
              />
            </div>

            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink-700">Gender</label>
              <select
                value={gender}
                onChange={(e) => setGender(e.target.value as "M" | "F")}
                className="w-full rounded-xl border border-ink-900/10 bg-white/75 px-3 py-2.5 text-sm text-ink-900 outline-none transition focus:border-brand-mid"
              >
                <option value="M">M</option>
                <option value="F">F</option>
              </select>
            </div>

            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink-700">Admission type</label>
              <input
                value={admissionType}
                onChange={(e) => setAdmissionType(e.target.value)}
                placeholder="EW EMER."
                className="field-input w-full"
              />
            </div>

            <div>
              <label className="mb-1.5 block text-sm font-medium text-ink-700">Arrival unit</label>
              <input
                value={arrivalUnit}
                onChange={(e) => setArrivalUnit(e.target.value)}
                placeholder="Emergency Department"
                className="field-input w-full"
              />
            </div>

            <div className="sm:col-span-2">
              <label className="mb-1.5 block text-sm font-medium text-ink-700">
                Chief complaint
              </label>
              <textarea
                value={chiefComplaint}
                onChange={(e) => setChiefComplaint(e.target.value)}
                rows={3}
                placeholder="Chest pain radiating to left arm, onset 2 hours ago."
                className="w-full rounded-xl border border-ink-900/10 bg-white/75 px-3 py-2.5 text-sm leading-relaxed text-ink-900 outline-none transition focus:border-brand-mid"
              />
              <p className="mt-1.5 text-xs text-ink-400">
                Optional, but the agents have much more to work with when it&apos;s filled in.
              </p>
            </div>
          </div>

          {error && (
            <p className="mt-4 rounded-lg bg-sev-criticalBg px-3 py-2 text-sm text-sev-critical">
              {error}
            </p>
          )}

          <button
            onClick={submit}
            disabled={submitting}
            className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl brand-gradient py-2.5 text-sm font-semibold text-white shadow-soft transition hover:shadow-glow disabled:opacity-70"
          >
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <UserPlus className="h-4 w-4" />
            )}
            {submitting ? "Registering…" : "Register patient"}
          </button>
        </div>
      )}

      {/* ---------------- pipeline progress ---------------- */}
      {admission && (
        <div className="rounded-2xl glass p-6 shadow-soft">
          <p className="font-mono text-xs uppercase tracking-widest text-brand-deep">
            {isReady ? "Pipeline complete" : isFailed ? "Pipeline failed" : "Pipeline running"}
          </p>
          <h2 className="mt-2 text-lg font-bold text-ink-900">
            Patient #{admission.hadm_id} registered
          </h2>
          <p className="mt-1 text-sm text-ink-500">
            {isReady
              ? "The brief is ready."
              : isFailed
              ? status?.error || status?.brief_error || "The pipeline could not finish."
              : `The agents are working on this chart — this usually takes 10–30 seconds. ${elapsed}s elapsed.`}
          </p>

          <ol className="mt-6 space-y-3">
            {STAGE_ORDER.map((s, i) => {
              const done = stageIndex > i || isReady;
              const active = stageIndex === i && !isReady;
              return (
                <li key={s} className="flex items-center gap-3">
                  <span
                    className={[
                      "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-bold",
                      done
                        ? "brand-gradient text-white"
                        : active
                        ? "border-2 border-brand-mid text-brand-deep"
                        : "border border-ink-900/15 text-ink-400",
                    ].join(" ")}
                  >
                    {done ? (
                      <CheckCircle2 className="h-3.5 w-3.5" />
                    ) : active ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      i + 1
                    )}
                  </span>
                  <span
                    className={[
                      "text-sm",
                      done || active ? "font-medium text-ink-900" : "text-ink-400",
                    ].join(" ")}
                  >
                    {STAGE_LABEL[s]}
                  </span>
                </li>
              );
            })}
          </ol>

          {isFailed && (
            <p className="mt-5 flex items-start gap-2 rounded-lg bg-sev-criticalBg px-3 py-2 text-sm text-sev-critical">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{status?.note || "Check the backend logs for this admission."}</span>
            </p>
          )}

          <div className="mt-6 flex flex-wrap gap-3">
            <button
              onClick={() => router.push(`/patients/${admission.hadm_id}`)}
              disabled={isWorking}
              className="inline-flex items-center gap-2 rounded-xl brand-gradient px-5 py-2.5 text-sm font-semibold text-white shadow-soft transition hover:shadow-glow disabled:opacity-50"
            >
              View patient
              <ArrowRight className="h-4 w-4" />
            </button>
            <Link
              href="/patients"
              className="inline-flex items-center gap-2 rounded-xl border border-ink-900/10 bg-white/60 px-5 py-2.5 text-sm font-semibold text-ink-700 transition hover:bg-white"
            >
              Back to board
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
