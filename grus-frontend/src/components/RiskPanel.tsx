import { RiskPrediction, RiskResponse } from "@/lib/types";
import { formatPct } from "@/lib/format";

export function RiskPanel({ risk, loading }: { risk: RiskResponse | null; loading: boolean }) {
  if (loading) {
    return (
      <div className="rounded-lg glass px-4 py-4">
        <p className="mb-2 font-mono text-[11px] uppercase tracking-wide text-ink-500">Model predictions</p>
        <div className="space-y-2 animate-pulse">
          <div className="h-4 w-2/3 rounded bg-ink-900/5" />
          <div className="h-4 w-1/2 rounded bg-ink-900/5" />
        </div>
        <p className="mt-2 text-[11px] text-ink-400">
          First call can take 10–30s — the model endpoint may be waking from idle.
        </p>
      </div>
    );
  }

  if (!risk || !risk.available) {
    return (
      <div className="rounded-lg border border-sev-unknown/40 bg-sev-unknownBg px-4 py-4">
        <p className="mb-1 font-mono text-[11px] uppercase tracking-wide text-sev-unknown">Model predictions — unavailable</p>
        <p className="text-base text-ink-700">
          {risk?.reason || "Too little data to score. This is not low risk — it means the model could not assess this patient."}
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-lg glass px-4 py-4">
      <div className="mb-1 flex items-center justify-between">
        <p className="font-mono text-[11px] uppercase tracking-wide text-ink-500">Model predictions</p>
        {risk.scored_at_hour !== undefined && (
          <span className="font-mono text-[11px] text-ink-400">scored at {risk.scored_at_hour}h</span>
        )}
      </div>
      <p className="mb-3 text-[11px] text-ink-400">
        Separate from the rule engine on purpose — rules fire on thresholds, models weigh everything together. Shown
        side by side so a clinician can see when they disagree.
      </p>
      {risk.feature_coverage !== undefined && risk.feature_coverage < 0.6 && (
        <p className="mb-3 rounded border border-sev-warning/40 bg-sev-warningBg px-2.5 py-1.5 text-xs text-sev-warning">
          Feature coverage {formatPct(risk.feature_coverage * 100)} — {risk.coverage_note || "built from limited data, a weaker claim than a fuller record."}
        </p>
      )}
      <div className="space-y-3">
        {(risk.predictions || []).map((p) => (
          <PredictionRow key={p.label} p={p} />
        ))}
      </div>
    </div>
  );
}

function PredictionRow({ p }: { p: RiskPrediction }) {
  if (!p.available) {
    return (
      <div className="rounded border border-sev-unknown/30 bg-sev-unknownBg px-3 py-2">
        <p className="text-sm text-sev-unknown">{p.label} — unavailable</p>
        {p.reason && <p className="text-xs text-ink-400">{p.reason}</p>}
      </div>
    );
  }

  const pct = p.probability !== undefined ? Math.round(p.probability * 100) : null;
  const fires = !!p.alert;

  return (
    <div className={`rounded border px-3 py-2.5 ${fires ? "border-sev-critical/40 bg-sev-criticalBg" : "border-ink-900/10 bg-ink-900/5"}`}>
      <div className="flex items-center justify-between gap-2">
        <p className={`text-sm font-semibold ${fires ? "text-sev-critical" : "text-ink-900"}`}>{p.label}</p>
        {pct !== null && <p className="num text-sm font-bold text-ink-900">{pct}%</p>}
      </div>
      <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-ink-900/5">
        <div
          className={`h-full rounded-full ${fires ? "bg-sev-critical" : "bg-sev-info"}`}
          style={{ width: `${pct ?? 0}%` }}
        />
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-ink-400">
        {p.threshold !== undefined && <span>threshold {Math.round(p.threshold * 100)}%</span>}
        {p.confidence && <span>confidence: {p.confidence}</span>}
      </div>
      {p.action && <p className="mt-1.5 text-xs font-medium text-ink-900">→ {p.action}</p>}
      {p.model_performance?.note && (
        <p className="mt-1.5 text-[11px] italic text-ink-400">{p.model_performance.note}</p>
      )}
    </div>
  );
}
