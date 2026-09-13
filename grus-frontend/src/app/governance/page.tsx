"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { GovernanceResponse, ModelEntry } from "@/lib/types";
import { formatPct } from "@/lib/format";

export default function GovernancePage() {
  const [gov, setGov] = useState<GovernanceResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .getGovernance()
      .then(setGov)
      .catch((e) => setError(e instanceof ApiError ? e.message : "Could not load governance data."));
  }, []);

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <h1 className="text-lg font-semibold text-ink-900">Governance</h1>
      <p className="mt-1 text-base text-ink-700">
        Model registry state. Showing what was rejected, and why, is more convincing than showing only the winners.
      </p>

      {error && (
        <div className="mt-4 rounded-lg border border-sev-critical/40 bg-sev-criticalBg px-4 py-3 text-sm text-sev-critical">
          {error}
        </div>
      )}

      {!gov && !error && (
        <div className="mt-6 space-y-2 animate-pulse">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-20 rounded-lg bg-white/70" />
          ))}
        </div>
      )}

      {gov && (
        <div className="mt-6 space-y-8">
          {gov.policy && (
            <p className="rounded-lg glass px-4 py-3 text-base text-ink-700 italic">
              {gov.policy}
            </p>
          )}

          <section>
            <h2 className="mb-3 font-mono text-xs uppercase tracking-wide text-sev-ok">
              Shipped ({gov.models.length})
            </h2>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {gov.models.map((m) => (
                <ModelCard key={m.name} m={m} shipped />
              ))}
              {gov.models.length === 0 && <p className="text-sm text-ink-400">None shipped yet.</p>}
            </div>
          </section>

          <section>
            <h2 className="mb-3 font-mono text-xs uppercase tracking-wide text-sev-critical">
              Rejected ({gov.rejected.length})
            </h2>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {gov.rejected.map((m) => (
                <ModelCard key={m.name} m={m} shipped={false} />
              ))}
              {gov.rejected.length === 0 && <p className="text-sm text-ink-400">Nothing rejected on record.</p>}
            </div>
          </section>

          {gov.registry && gov.registry.length > 0 && (
            <section>
              <h2 className="mb-3 font-mono text-xs uppercase tracking-wide text-ink-500">SageMaker Model Registry</h2>
              <div className="overflow-x-auto rounded-lg border border-ink-900/10">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-ink-900/10 text-left text-xs text-ink-500">
                      <th className="px-3 py-2 font-normal">Version</th>
                      <th className="px-3 py-2 font-normal">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {gov.registry.map((r) => (
                      <tr key={r.version} className="border-b border-ink-900/8 last:border-0">
                        <td className="num px-3 py-2 text-ink-900">v{r.version}</td>
                        <td className="px-3 py-2 text-ink-500">{r.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          {gov.registry_error && <p className="text-xs text-ink-400">registry: {gov.registry_error}</p>}
        </div>
      )}
    </div>
  );
}

function ModelCard({ m, shipped }: { m: ModelEntry; shipped: boolean }) {
  return (
    <div
      className={`rounded-lg border px-4 py-3 ${
        shipped ? "border-sev-ok/30 bg-sev-okBg" : "border-sev-critical/30 bg-sev-criticalBg"
      }`}
    >
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-ink-900">{m.name}</p>
        {m.auc !== undefined && <span className="num text-xs text-ink-500">AUC {m.auc.toFixed(3)}</span>}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-ink-500">
        {m.precision !== undefined && <span>precision {formatPct(m.precision * 100)}</span>}
        {m.recall !== undefined && <span>recall {formatPct(m.recall * 100)}</span>}
        {m.calibration_error !== undefined && <span>calib err {m.calibration_error}</span>}
        {m.subgroup_auc_gap !== undefined && <span>subgroup gap {m.subgroup_auc_gap}</span>}
      </div>
      {m.rejected_because && m.rejected_because.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-sev-critical">
          {m.rejected_because.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
