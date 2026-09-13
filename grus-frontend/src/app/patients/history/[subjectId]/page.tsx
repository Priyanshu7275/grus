"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { HistoryResponse } from "@/lib/types";
import { Citation } from "@/components/SourceDrawer";

export default function HistoryPage() {
  const params = useParams<{ subjectId: string }>();
  const subjectId = Number(params.subjectId);
  const [history, setHistory] = useState<HistoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getHistory(subjectId)
      .then(setHistory)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load history."));
  }, [subjectId]);

  return (
    <div className="mx-auto max-w-3xl px-4 py-6">
      <div className="mb-4 text-xs text-ink-400">
        <Link href="/patients" className="hover:text-ink-500">
          Patients
        </Link>
        <span> / </span>
        <span className="font-mono">subject #{subjectId}</span>
      </div>
      <h1 className="mb-4 text-lg font-semibold text-ink-900">Patient history</h1>

      {error && (
        <div className="rounded-lg border border-sev-critical/40 bg-sev-criticalBg px-4 py-3 text-sm text-sev-critical">
          {error}
        </div>
      )}

      {!history && !error && (
        <div className="space-y-2 animate-pulse">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-16 rounded-lg bg-white/70" />
          ))}
        </div>
      )}

      {history && (
        <div className="space-y-6">
          <div className="rounded-lg glass px-4 py-3">
            <p className="text-sm text-ink-900">
              <span className="num font-semibold">{history.prior_admissions}</span> prior admission
              {history.prior_admissions === 1 ? "" : "s"} at this facility
            </p>
            {history.note && <p className="mt-1 text-xs text-sev-unknown">{history.note}</p>}
          </div>

          {history.recurring_conditions.length > 0 && (
            <section>
              <p className="mb-2 font-mono text-[11px] uppercase tracking-wide text-ink-500">
                Recurring conditions — the pattern nobody looks up by hand
              </p>
              <div className="space-y-2">
                {history.recurring_conditions.map((c, i) => {
                  const [table, id] = c.source.split("#");
                  return (
                    <div key={i} className="flex items-center justify-between rounded-lg border border-sev-warning/30 bg-sev-warningBg px-4 py-2.5">
                      <span className="text-sm text-ink-900">{c.condition}</span>
                      <div className="flex items-center gap-2">
                        <span className="num text-xs text-sev-warning">{c.visits} visits</span>
                        <Citation table={table} id={Number(id)} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          <section>
            <p className="mb-2 font-mono text-[11px] uppercase tracking-wide text-ink-500">Admissions</p>
            <div className="space-y-2">
              {history.admissions.map((a) => (
                <div
                  key={a.hadm_id}
                  className={`rounded-lg border px-4 py-3 ${
                    a.is_current ? "border-sev-info/40 bg-sev-infoBg" : "border-ink-900/10 bg-white/70"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <Link href={`/patients/${a.hadm_id}?subject=${subjectId}`} className="font-mono text-sm text-ink-900 hover:underline">
                      hadm #{a.hadm_id}
                    </Link>
                    <div className="flex items-center gap-2">
                      {a.is_current && (
                        <span className="rounded border border-sev-info/40 bg-black/20 px-1.5 py-0.5 text-[10px] text-sev-info">
                          current
                        </span>
                      )}
                      <span className="text-xs text-ink-400">{a.admission_type}</span>
                      {a.length_of_stay_days != null && (
                        <span className="num text-xs text-ink-400">{a.length_of_stay_days}d stay</span>
                      )}
                    </div>
                  </div>
                  {a.diagnoses.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {a.diagnoses.map((d, i) => {
                        const [table, id] = d.source.split("#");
                        return <Citation key={i} table={table} id={Number(id)} label={d.title} />;
                      })}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
