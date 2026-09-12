"use client";

import { useState } from "react";
import { formatHours } from "@/lib/format";

interface Props {
  maxHours: number;
  committedHours: number | null; // null = full record
  onCommit: (hours: number | null) => void;
  onDraftChange?: (hours: number) => void;
}

export function TimelineSlider({ maxHours, committedHours, onCommit, onDraftChange }: Props) {
  const [draft, setDraft] = useState<number>(committedHours ?? maxHours);
  const isFull = committedHours === null;

  return (
    <div className="rounded-lg glass px-4 py-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-mono text-[11px] uppercase tracking-wide text-ink-500">Point-in-time view</span>
        <span className="font-mono text-xs text-ink-900">
          {isFull ? "Full record" : `showing only what was known ${formatHours(draft)}`}
        </span>
      </div>
      <div className="flex items-center gap-3">
        <input
          type="range"
          min={0}
          max={maxHours}
          step={0.1}
          value={draft}
          disabled={isFull}
          onChange={(e) => {
            const v = Number(e.target.value);
            setDraft(v);
            onDraftChange?.(v);
          }}
          onMouseUp={() => !isFull && onCommit(draft)}
          onTouchEnd={() => !isFull && onCommit(draft)}
          onKeyUp={() => !isFull && onCommit(draft)}
          className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-ink-900/5 accent-sev-info disabled:opacity-40"
        />
        <button
          onClick={() => {
            if (isFull) {
              onCommit(draft);
            } else {
              onCommit(null);
            }
          }}
          className={`shrink-0 rounded border px-2.5 py-1 font-mono text-[11px] transition-colors ${
            isFull
              ? "border-ink-900/10 text-ink-500 hover:text-ink-900"
              : "border-sev-info/50 bg-sev-infoBg text-sev-info"
          }`}
        >
          {isFull ? "Use timeline slider" : "Back to full record"}
        </button>
      </div>
      <p className="mt-2 text-sm text-ink-700">
        Dragging re-checks alerts instantly; releasing regenerates the full brief. This is what stops the system
        reading the answer key.
      </p>
    </div>
  );
}
