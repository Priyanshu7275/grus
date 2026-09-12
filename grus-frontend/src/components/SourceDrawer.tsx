"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import { SourceResponse } from "@/lib/types";
import { formatHours } from "@/lib/format";

interface SourceDrawerContextValue {
  openSource: (table: string, id: number) => void;
  closeSource: () => void;
}

const SourceDrawerContext = createContext<SourceDrawerContextValue | null>(null);

export function useSourceDrawer() {
  const ctx = useContext(SourceDrawerContext);
  if (!ctx) throw new Error("useSourceDrawer must be used within SourceDrawerProvider");
  return ctx;
}

/** A clickable [table#id] citation — the whole point of the "sources are
 * clickable" rule. Never render a source reference as plain text. */
export function Citation({ table, id, label }: { table: string; id: number; label?: string }) {
  const { openSource } = useSourceDrawer();
  return (
    <button
      type="button"
      onClick={() => openSource(table, id)}
      className="inline-flex items-center gap-1 rounded border border-sev-info/40 bg-sev-infoBg px-1.5 py-0.5 font-mono text-[11px] text-sev-info hover:bg-sev-info/20 hover:border-sev-info transition-colors align-middle"
      title={`Open source: ${table}#${id}`}
    >
      {label || `${table}#${id}`}
    </button>
  );
}

export function SourceDrawerProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<{ table: string; id: number } | null>(null);
  const [data, setData] = useState<SourceResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const openSource = useCallback((table: string, id: number) => {
    setTarget({ table, id });
  }, []);
  const closeSource = useCallback(() => setTarget(null), []);

  useEffect(() => {
    if (!target) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setData(null);
    api
      .getSource(target.table, target.id)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? e.message : "Could not load source.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [target]);

  const value = useMemo(() => ({ openSource, closeSource }), [openSource, closeSource]);

  return (
    <SourceDrawerContext.Provider value={value}>
      {children}
      {target && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <button
            aria-label="Close source drawer"
            className="absolute inset-0 bg-black/60 backdrop-blur-[1px]"
            onClick={closeSource}
          />
          <div className="relative h-full w-full max-w-md overflow-y-auto border-l border-ink-900/10 bg-white shadow-lift fade-in">
            <div className="sticky top-0 z-10 flex items-center justify-between border-b border-ink-900/10 bg-white px-5 py-4">
              <div>
                <div className="font-mono text-xs text-ink-500 uppercase tracking-wide">Source</div>
                <div className="font-mono text-sm text-ink-900">
                  {target.table}#{target.id}
                </div>
              </div>
              <button
                onClick={closeSource}
                className="rounded border border-ink-900/10 px-2 py-1 text-xs text-ink-500 hover:text-ink-900 hover:border-ink-900/15"
              >
                Close
              </button>
            </div>

            <div className="px-5 py-4">
              {loading && <SourceSkeleton />}
              {error && (
                <div className="rounded border border-sev-critical/40 bg-sev-criticalBg px-3 py-2 text-sm text-sev-critical">
                  {error}
                </div>
              )}
              {data && <SourceBody data={data} />}
            </div>
          </div>
        </div>
      )}
    </SourceDrawerContext.Provider>
  );
}

function SourceSkeleton() {
  return (
    <div className="space-y-2 animate-pulse">
      {[...Array(6)].map((_, i) => (
        <div key={i} className="h-4 rounded bg-ink-900/5" style={{ width: `${60 + (i % 3) * 15}%` }} />
      ))}
    </div>
  );
}

function SourceBody({ data }: { data: SourceResponse }) {
  const isNote = data.table === "note_chunks";

  if (isNote) {
    const row = data.row as { section?: string; note_type?: string; hours_since_admit?: number | null; text?: string };
    const highlight = data.highlight || [];
    return (
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          {row.section && (
            <span className="rounded border border-ink-900/10 bg-ink-900/5 px-2 py-0.5 text-xs text-ink-500">{row.section}</span>
          )}
          {row.note_type && (
            <span className="rounded border border-ink-900/10 bg-ink-900/5 px-2 py-0.5 text-xs text-ink-500">{row.note_type}</span>
          )}
          {typeof row.hours_since_admit === "number" && (
            <span className="font-mono text-xs text-ink-400">{formatHours(row.hours_since_admit)}</span>
          )}
        </div>
        {data.context && <p className="text-xs text-ink-500 italic">{data.context}</p>}
        <div className="rounded border border-ink-900/10 bg-ink-900/5 px-4 py-3 text-sm leading-relaxed text-ink-900 whitespace-pre-wrap">
          <HighlightedText text={row.text || ""} highlights={highlight} />
        </div>
        {data.provenance && <Provenance provenance={data.provenance} />}
      </div>
    );
  }

  const row = data.row || {};
  return (
    <div className="space-y-4">
      {data.context && <p className="text-xs text-ink-500 italic">{data.context}</p>}
      <dl className="divide-y divide-ink-900/8 rounded border border-ink-900/10">
        {Object.entries(row).map(([k, v]) => (
          <div key={k} className="flex items-start justify-between gap-4 px-3 py-2">
            <dt className="shrink-0 font-mono text-xs text-ink-500">{k}</dt>
            <dd className="text-right font-mono text-sm text-ink-900 break-words">{renderValue(k, v)}</dd>
          </div>
        ))}
      </dl>
      {data.provenance && <Provenance provenance={data.provenance} />}
    </div>
  );
}

function renderValue(key: string, v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (key.toLowerCase().includes("hours_since_admit") && typeof v === "number") {
    return formatHours(v);
  }
  return String(v);
}

function Provenance({ provenance }: { provenance: SourceResponse["provenance"] }) {
  if (!provenance) return null;
  return (
    <div className="rounded border border-ink-900/8 bg-ink-900/5 px-3 py-2 text-[11px] text-ink-400 font-mono">
      {provenance.source_dataset && <div>dataset: {provenance.source_dataset}</div>}
      {provenance.source_table && <div>table: {provenance.source_table}</div>}
      {provenance.note_id && <div>note_id: {provenance.note_id}</div>}
      {provenance.ingested_at && <div>ingested: {provenance.ingested_at}</div>}
    </div>
  );
}

function HighlightedText({ text, highlights }: { text: string; highlights: string[] }) {
  if (!highlights.length) return <>{text}</>;
  // Build one regex that matches any highlight phrase, longest first so
  // overlapping phrases don't get partially matched.
  const sorted = [...highlights].filter(Boolean).sort((a, b) => b.length - a.length);
  if (!sorted.length) return <>{text}</>;
  const escaped = sorted.map((h) => h.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const re = new RegExp(`(${escaped.join("|")})`, "gi");
  const parts = text.split(re);
  return (
    <>
      {parts.map((part, i) =>
        sorted.some((h) => h.toLowerCase() === part.toLowerCase()) ? (
          <mark key={i} className="rounded bg-sev-warning/30 text-sev-warning px-0.5">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  );
}
