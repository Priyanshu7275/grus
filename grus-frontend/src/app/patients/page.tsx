"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, HelpCircle, Search, Siren, UserPlus, Users } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { PatientCard as PatientCardType } from "@/lib/types";
import { PatientCard, PatientCardSkeleton } from "@/components/PatientCard";
import { useActivePatient } from "@/lib/activePatientContext";
import { VoiceWidget } from "@/components/VoiceWidget";

const RISK_OPTIONS = ["", "critical", "high", "moderate", "low", "unknown"];

export default function PatientsPage() {
  const [patients, setPatients] = useState<PatientCardType[] | null>(null);
  const [count, setCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [cohort, setCohort] = useState("");
  const [risk, setRisk] = useState("");
  const { clearActivePatient } = useActivePatient();

  useEffect(() => {
    clearActivePatient();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    const handle = setTimeout(() => {
      api
        .listPatients({ search: search || undefined, cohort: cohort || undefined, risk: risk || undefined })
        .then((res) => {
          if (!cancelled) {
            setPatients(res.patients);
            setCount(res.count);
          }
        })
        .catch((e) => {
          if (!cancelled) setError(e instanceof ApiError ? e.message : "Could not load patients.");
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [search, cohort, risk]);

  const cohorts = useMemo(() => {
    const set = new Set<string>();
    (patients || []).forEach((p) => p.cohort && set.add(p.cohort));
    return Array.from(set).sort();
  }, [patients]);

  const tally = useMemo(() => {
    const list = patients || [];
    return {
      critical: list.filter((p) => p.risk_level === "critical").length,
      high: list.filter((p) => p.risk_level === "high").length,
      alerts: list.reduce((n, p) => n + (p.alert_count || 0), 0),
      unknowns: list.reduce((n, p) => n + (p.unknown_count || 0), 0),
    };
  }, [patients]);

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6">
      <div className="mb-6">
        <span className="inline-flex items-center gap-1.5 rounded-full border border-ink-900/[0.07] bg-white px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-ink-500">
          <span className="h-1.5 w-1.5 rounded-full bg-brand-mid" /> Emergency department
        </span>
        <h1 className="mt-3 text-3xl font-bold text-ink-900 sm:text-[2.4rem]">
          Cohort <span className="accent-serif text-[1.15em] text-brand-deep">board</span>
        </h1>
        <p className="mt-1 text-sm text-ink-500">
          {patients ? `${count} patient${count === 1 ? "" : "s"} on the board right now` : "Loading…"}
        </p>
      </div>

      <div className="mb-7 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <BoardStat icon={Users} label="Patients" sub="On the board" value={patients ? count : "—"} />
        <BoardStat icon={Siren} label="Critical" sub="Need attention now" value={patients ? tally.critical : "—"} tone="critical" />
        <BoardStat icon={AlertTriangle} label="Open alerts" sub="Across all patients" value={patients ? tally.alerts : "—"} tone="warning" />
        <BoardStat icon={HelpCircle} label="Unknowns" sub="Asked, not guessed" value={patients ? tally.unknowns : "—"} />
      </div>

      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <p className="text-lg font-bold text-ink-900">All patients</p>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="field-icon" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search hadm_id or subject_id…"
              className="field-input w-60 !rounded-full !bg-white"
            />
          </div>
          <select
            value={cohort}
            onChange={(e) => setCohort(e.target.value)}
            className="rounded-full border border-ink-900/10 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none transition focus:border-brand-mid"
          >
            <option value="">All cohorts</option>
            {cohorts.map((c) => (
              <option key={c} value={c} className="capitalize">
                {c}
              </option>
            ))}
          </select>
          <select
            value={risk}
            onChange={(e) => setRisk(e.target.value)}
            className="rounded-full border border-ink-900/10 bg-white px-4 py-2.5 text-sm text-ink-900 outline-none transition focus:border-brand-mid"
          >
            <option value="">All risk levels</option>
            {RISK_OPTIONS.filter(Boolean).map((r) => (
              <option key={r} value={r} className="capitalize">
                {r}
              </option>
            ))}
            </select>
          <Link
            href="/patients/new"
            className="inline-flex items-center gap-2 rounded-full bg-brand-ink px-5 py-2.5 text-sm font-semibold text-white shadow-soft transition hover:bg-brand-deep"
          >
            <UserPlus className="h-4 w-4" />
            New patient
          </Link>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-xl border border-sev-critical/30 bg-sev-criticalBg px-4 py-3 text-sm text-sev-critical">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {patients === null && !error
          ? Array.from({ length: 12 }).map((_, i) => <PatientCardSkeleton key={i} />)
          : (patients || []).map((p) => <PatientCard key={p.hadm_id} patient={p} />)}
      </div>

      {patients && patients.length === 0 && !error && (
                <div className="mt-12 text-center text-sm text-ink-500">No patients match these filters.</div>
      )}

      <VoiceWidget />
    </div>
  );
}

function BoardStat({
  icon: Icon,
  label,
  sub,
  value,
  tone,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  sub: string;
  value: number | string;
  tone?: "critical" | "warning";
}) {
  const chip =
    tone === "critical"
      ? "bg-sev-criticalBg text-sev-critical border-sev-critical/15"
      : tone === "warning"
      ? "bg-sev-warningBg text-[#a16207] border-sev-warning/20"
      : "bg-leaf-soft text-brand-deep border-brand-mid/15";
  return (
    <div className="card p-5">
      <div className="flex items-start gap-3">
        <span className={`flex h-10 w-10 items-center justify-center rounded-xl border ${chip}`}>
          <Icon className="h-[18px] w-[18px]" />
        </span>
        <div>
          <p className="text-[15px] font-bold text-ink-900">{label}</p>
          <p className="text-xs text-ink-400">{sub}</p>
        </div>
      </div>
      <p className="mt-4 text-[2.4rem] font-extrabold leading-none text-ink-900">{value}</p>
    </div>
  );
}
