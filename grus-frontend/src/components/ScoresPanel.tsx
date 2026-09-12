"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Gauge, Loader2 } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { ScoreResult, ScoreSummary, ScoresListResponse } from "@/lib/types";
import { findInputSpec, resolveMissingKey } from "@/lib/scoreSpecs";
import { normalizeSource } from "@/lib/format";
import { Citation } from "./SourceDrawer";

/**
 * The 15 validated clinical risk-scoring tools (PERC, Wells, HEART,
 * qSOFA, SIRS, HAS-BLED, CHA2DS2-VASc, CURB-65, NEWS2, MEWS, KDIGO AKI,
 * Shock Index, Glasgow-Blatchford, SOFA respiratory, anion gap).
 *
 * GET /scores suggests which apply to this patient. GET on one score
 * computes what the record can supply and lists what's missing, each
 * with the question to ask. POST submits the clinician's answers and
 * returns the (possibly still-partial) result.
 */
export function ScoresPanel({ hadmId, asOfHours }: { hadmId: number; asOfHours: number | null }) {
  const [list, setList] = useState<ScoresListResponse | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [openKey, setOpenKey] = useState<string | null>(null);

  useEffect(() => {
    if (!hadmId) return;
    let cancelled = false;
    setList(null);
    setListError(null);
    setOpenKey(null);
    api
      .listScores(hadmId, asOfHours !== null ? { as_of_hours: asOfHours } : {})
      .then((r) => {
        if (!cancelled) setList(r);
      })
      .catch((e) => {
        if (!cancelled) setListError(e instanceof ApiError ? e.message : "Could not load scores.");
      });
    return () => {
      cancelled = true;
    };
  }, [hadmId, asOfHours]);

  if (listError) {
    return (
      <div className="rounded-lg border border-sev-unknown/40 bg-sev-unknownBg px-4 py-4">
        <p className="mb-1 font-mono text-[11px] uppercase tracking-wide text-sev-unknown">Clinical scores — unavailable</p>
        <p className="text-base text-ink-700">{listError}</p>
      </div>
    );
  }

  if (!list) {
    return (
      <div className="rounded-lg glass px-4 py-4">
        <p className="mb-2 font-mono text-[11px] uppercase tracking-wide text-ink-500">Clinical scores</p>
        <div className="space-y-2 animate-pulse">
          <div className="h-4 w-3/4 rounded bg-ink-900/5" />
          <div className="h-4 w-1/2 rounded bg-ink-900/5" />
        </div>
      </div>
    );
  }

  const suggestedKeys = new Set(list.suggested.map((s) => s.key));
  const rest = list.all_available.filter((s) => !suggestedKeys.has(s.key));

  return (
    <div className="rounded-lg glass px-4 py-4">
      <div className="mb-1 flex items-center gap-1.5">
        <Gauge className="h-3.5 w-3.5 text-brand-deep" />
        <p className="font-mono text-[11px] uppercase tracking-wide text-ink-500">Clinical scores</p>
      </div>
      <p className="mb-3 text-sm text-ink-700">
        Validated decision rules. GRUS never calculates one from a guess — every score is arithmetic
        over the record, with what's missing asked for honestly.
      </p>

      {list.suggested.length > 0 && (
        <div className="mb-3 space-y-1.5">
          <p className="text-[11px] font-medium uppercase tracking-wide text-ink-400">Suggested here</p>
          {list.suggested.map((s) => (
            <ScoreRow
              key={s.key}
              summary={s}
              hadmId={hadmId}
              asOfHours={asOfHours}
              open={openKey === s.key}
              onToggle={() => setOpenKey((k) => (k === s.key ? null : s.key))}
            />
          ))}
        </div>
      )}

      <button
        onClick={() => setShowAll((v) => !v)}
        className="flex w-full items-center gap-1 text-[11px] font-medium text-ink-500 hover:text-ink-900"
      >
        {showAll ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        {showAll ? "Hide" : "Browse"} all {list.all_available.length} scores
      </button>

      {showAll && (
        <div className="mt-2 space-y-1.5">
          {(list.suggested.length > 0 ? rest : list.all_available).map((s) => (
            <ScoreRow
              key={s.key}
              summary={s}
              hadmId={hadmId}
              asOfHours={asOfHours}
              open={openKey === s.key}
              onToggle={() => setOpenKey((k) => (k === s.key ? null : s.key))}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ScoreRow({
  summary,
  hadmId,
  asOfHours,
  open,
  onToggle,
}: {
  summary: ScoreSummary;
  hadmId: number;
  asOfHours: number | null;
  open: boolean;
  onToggle: () => void;
}) {
  const [result, setResult] = useState<ScoreResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || result) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .getScore(hadmId, summary.key, asOfHours !== null ? { as_of_hours: asOfHours } : {})
      .then((r) => {
        if (!cancelled) setResult(r);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? e.message : "Could not compute this score.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, hadmId, summary.key, asOfHours]);

  return (
    <div className="rounded-lg border border-ink-900/10 bg-white/60">
      <button onClick={onToggle} className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left">
        <span className="text-sm font-medium text-ink-900">{summary.name}</span>
        {open ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-ink-400" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-ink-400" />
        )}
      </button>

      {open && (
        <div className="border-t border-ink-900/10 px-3 py-3">
          <p className="mb-2 text-sm text-ink-700">{summary.purpose}</p>

          {loading && (
            <div className="flex items-center gap-2 text-xs text-ink-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Computing…
            </div>
          )}
          {error && <p className="text-xs text-sev-critical">{error}</p>}
          {result && (
            <ScoreResultView result={result} hadmId={hadmId} asOfHours={asOfHours} onUpdate={setResult} />
          )}
        </div>
      )}
    </div>
  );
}

function ScoreResultView({
  result,
  hadmId,
  asOfHours,
  onUpdate,
}: {
  result: ScoreResult;
  hadmId: number;
  asOfHours: number | null;
  onUpdate: (r: ScoreResult) => void;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  async function submit() {
    const provided: Record<string, string> = {};
    for (const m of result.components.missing) {
      const key = resolveMissingKey(result.score, m);
      const value = key ? answers[key] : undefined;
      if (key && value !== undefined && value !== "") provided[key] = value;
    }
    if (Object.keys(provided).length === 0) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const r = await api.submitScore(hadmId, result.score, provided, asOfHours !== null ? { as_of_hours: asOfHours } : {});
      onUpdate(r);
      setAnswers({});
    } catch (e) {
      setSubmitError(e instanceof ApiError ? e.message : "Could not submit answers.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-3">
      {result.caution && (
        <p className="rounded border border-sev-warning/40 bg-sev-warningBg px-2.5 py-1.5 text-xs text-sev-warning">
          {result.caution}
        </p>
      )}

      {result.complete && result.total !== undefined ? (
        <div className="rounded-lg border border-brand-mid/30 bg-brand-sky/10 px-3 py-2.5">
          <div className="flex items-baseline justify-between">
            <span className="num text-2xl font-extrabold text-brand-deep">
              {result.total}
              {result.max_possible !== undefined && (
                <span className="text-sm font-medium text-ink-400"> / {result.max_possible}</span>
              )}
            </span>
            {result.risk && (
              <span className="rounded-full bg-brand-deep px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-white">
                {result.risk}
              </span>
            )}
          </div>
          {result.interpretation && <p className="mt-1.5 text-sm text-ink-900">{result.interpretation}</p>}
        </div>
      ) : result.complete ? (
        <p className="rounded border border-sev-ok/40 bg-sev-okBg px-2.5 py-1.5 text-xs text-sev-ok">
          All criteria accounted for — no total returned for this score.
        </p>
      ) : (
        <p className="rounded border border-sev-unknown/40 bg-sev-unknownBg px-2.5 py-1.5 text-xs text-sev-unknown">
          Partial — {result.components.missing.length}{" "}
          {result.components.missing.length === 1 ? "criterion" : "criteria"} not in the record. Answer below to
          complete it.
        </p>
      )}

      {result.components.found.length > 0 && (
        <div>
          <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-ink-400">Found in the chart</p>
          <ul className="space-y-1">
            {result.components.found.map((f, i) => {
              const src = f.source ? normalizeSource(f.source) : null;
              return (
                <li key={i} className="flex items-center justify-between gap-2 text-sm">
                  <span className="text-ink-700">{f.label}</span>
                  <span className="flex items-center gap-1.5">
                    <span className="num text-ink-900">{String(f.value)}</span>
                    <span className="num text-xs text-ink-400">+{f.points}</span>
                    {src && <Citation table={src.table} id={src.id} label="src" />}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {result.components.missing.length > 0 && (
        <div>
          <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-400">
            Not in the record — ask the clinician
          </p>
          <div className="space-y-2">
            {result.components.missing.map((m, i) => {
              const spec = findInputSpec(result.score, m.label);
              const key = resolveMissingKey(result.score, m);
              return (
                <MissingRow
                  key={i}
                  missing={m}
                  spec={spec}
                  value={key ? answers[key] ?? "" : ""}
                  onChange={(v) => key && setAnswers((prev) => ({ ...prev, [key]: v }))}
                  disabled={!key}
                />
              );
            })}
          </div>
          {submitError && <p className="mt-2 text-xs text-sev-critical">{submitError}</p>}
          <button
            onClick={submit}
            disabled={submitting || Object.keys(answers).length === 0}
            className="mt-2.5 flex items-center gap-1.5 rounded-lg brand-gradient px-3 py-1.5 text-xs font-semibold text-white shadow-soft transition hover:shadow-glow disabled:opacity-50"
          >
            {submitting && <Loader2 className="h-3 w-3 animate-spin" />}
            Submit answers
          </button>
        </div>
      )}

      <p className="text-[10px] italic text-ink-400">{result.citation}</p>
    </div>
  );
}

function MissingRow({
  missing,
  spec,
  value,
  onChange,
  disabled,
}: {
  missing: { label: string; ask?: string };
  spec?: { kind: string; scale?: string[] };
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
}) {
  const isBoolean = !spec?.scale && (spec?.kind === "clinical" || spec?.kind === "dx" || spec?.kind === "med");

  return (
    <div className="rounded border border-ink-900/10 bg-ink-900/5 px-2.5 py-2">
      <p className="text-xs text-ink-700">{missing.ask || `${missing.label}?`}</p>
      {disabled ? (
        <p className="mt-1 text-[10px] italic text-ink-400">Answering this one isn&rsquo;t wired up yet.</p>
      ) : spec?.scale ? (
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="mt-1.5 w-full rounded border border-ink-900/10 bg-white px-2 py-1 text-xs text-ink-900 outline-none focus:border-brand-mid"
        >
          <option value="">Select…</option>
          {spec.scale.map((opt) => (
            <option key={opt} value={opt} className="capitalize">
              {opt}
            </option>
          ))}
        </select>
      ) : isBoolean ? (
        <div className="mt-1.5 flex gap-1.5">
          {["yes", "no"].map((opt) => (
            <button
              key={opt}
              onClick={() => onChange(opt)}
              className={`rounded-full px-3 py-1 text-xs font-medium capitalize transition ${
                value === opt
                  ? "brand-gradient text-white"
                  : "border border-ink-900/10 bg-white text-ink-700 hover:border-brand-mid/50"
              }`}
            >
              {opt}
            </button>
          ))}
        </div>
      ) : (
        <input
          type="text"
          inputMode="decimal"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Value"
          className="mt-1.5 w-full rounded border border-ink-900/10 bg-white px-2 py-1 text-xs text-ink-900 outline-none focus:border-brand-mid"
        />
      )}
    </div>
  );
}
