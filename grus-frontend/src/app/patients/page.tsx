"use client";

import { useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { PatientCard as PatientCardType } from "@/lib/types";
import { PatientCard, PatientCardSkeleton } from "@/components/PatientCard";
import { useActivePatient } from "@/lib/activePatientContext";

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

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-ink-900">Cohort board</h1>
          <p className="mt-1 text-sm text-ink-500">
            {patients ? `${count} patient${count === 1 ? "" : "s"}` : "Loading…"}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search className="field-icon" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search hadm_id or subject_id…"
              className="field-input w-56"
            />
          </div>
          <select
            value={cohort}
            onChange={(e) => setCohort(e.target.value)}
            className="rounded-xl border border-ink-900/10 bg-white/75 px-3 py-2.5 text-sm text-ink-900 outline-none transition focus:border-brand-mid"
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
            className="rounded-xl border border-ink-900/10 bg-white/75 px-3 py-2.5 text-sm text-ink-900 outline-none transition focus:border-brand-mid"
          >
            <option value="">All risk levels</option>
            {RISK_OPTIONS.filter(Boolean).map((r) => (
              <option key={r} value={r} className="capitalize">
                {r}
              </option>
            ))}
          </select>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded-xl border border-sev-critical/30 bg-sev-criticalBg px-4 py-3 text-sm text-sev-critical">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {patients === null && !error
          ? Array.from({ length: 12 }).map((_, i) => <PatientCardSkeleton key={i} />)
          : (patients || []).map((p) => <PatientCard key={p.hadm_id} patient={p} />)}
      </div>

      {patients && patients.length === 0 && !error && (
        <div className="mt-12 text-center text-sm text-ink-500">No patients match these filters.</div>
      )}
    </div>
  );
}
