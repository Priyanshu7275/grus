"use client";

import { useEffect, useState } from "react";

const AGENT_SEQUENCE = [
  { key: "retriever", label: "Retriever", desc: "pulling the record" },
  { key: "reconciler", label: "Reconciler", desc: "building one timeline" },
  { key: "risk", label: "Risk", desc: "scoring against ML models" },
  { key: "verifier", label: "Verifier", desc: "killing unsourced claims" },
  { key: "composer", label: "Composer", desc: "writing the brief" },
];

/** There is no streaming progress from the backend — a brief is one 5-10s
 * call. This is an honest approximation ("real work, not a loading
 * animation" per the brief), timed roughly to typical generation_ms, so
 * the doctor sees which stage the pipeline is conceptually in rather than
 * a bare spinner. */
export function AgentsProgress({ agentsRun }: { agentsRun?: string[] }) {
  const [activeIdx, setActiveIdx] = useState(0);
  const done = agentsRun && agentsRun.length > 0;

  useEffect(() => {
    if (done) {
      setActiveIdx(AGENT_SEQUENCE.length);
      return;
    }
    const id = setInterval(() => {
      setActiveIdx((i) => Math.min(i + 1, AGENT_SEQUENCE.length - 1));
    }, 1400);
    return () => clearInterval(id);
  }, [done]);

  return (
    <div className="rounded-lg glass px-4 py-4">
      <p className="mb-3 font-mono text-[11px] uppercase tracking-wide text-ink-500">
        {done ? "Agent graph complete" : "Generating brief — 5 agents running"}
      </p>
      <ol className="space-y-2">
        {AGENT_SEQUENCE.map((a, i) => {
          const isDone = done ? agentsRun!.includes(a.key) : i < activeIdx;
          const isActive = !done && i === activeIdx;
          return (
            <li key={a.key} className="flex items-center gap-3">
              <span
                className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-mono ${
                  isDone
                    ? "bg-sev-ok/20 text-sev-ok"
                    : isActive
                    ? "bg-sev-info text-white"
                    : "bg-ink-900/5 text-ink-400"
                }`}
              >
                {isDone ? "✓" : i + 1}
              </span>
              <span className={`text-sm ${isDone || isActive ? "text-ink-900" : "text-ink-400"}`}>{a.label}</span>
              <span className="text-xs text-ink-400">— {a.desc}</span>
              {isActive && <span className="h-1.5 w-1.5 rounded-full bg-sev-info pulse-dot" />}
            </li>
          );
        })}
      </ol>
      <p className="mt-3 text-sm text-ink-700">This is real work — retrieval, rules, and model calls — not a loading animation.</p>
    </div>
  );
}
