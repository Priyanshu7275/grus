"use client";

/**
 * Floating "Talk to GRUS" launcher — sits just above the Ask GRUS chat
 * bubble and opens the voice panel the same way the chat opens.
 * Opening one closes the other, so the two panels never overlap.
 */

import { useEffect, useState } from "react";
import { ChevronDown, Mic, Minus } from "lucide-react";
import { VoicePanel } from "./VoicePanel";

export function VoiceWidget({ patientShortId }: { patientShortId?: string }) {
  const [open, setOpen] = useState(false);

  // Close when the chat widget opens.
  useEffect(() => {
    const onOther = (e: Event) => {
      if ((e as CustomEvent).detail !== "voice") setOpen(false);
    };
    window.addEventListener("grus:widget-open", onOther);
    return () => window.removeEventListener("grus:widget-open", onOther);
  }, []);

  const toggle = () => {
    setOpen((o) => {
      if (!o) window.dispatchEvent(new CustomEvent("grus:widget-open", { detail: "voice" }));
      return !o;
    });
  };

  return (
    <div className="fixed bottom-[5.75rem] right-5 z-40 flex flex-col items-end gap-3">
      {open && (
        <div className="flex h-[520px] max-h-[70vh] w-[380px] max-w-[92vw] flex-col overflow-hidden rounded-2xl border border-ink-900/10 bg-white shadow-lift fade-in">
          <div className="bg-brand-ink px-5 py-4">
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-white">Talk to GRUS</span>
                  <span className="rounded border border-white/30 bg-white/10 px-1.5 py-0.5 text-[10px] text-white/90">
                    same agent as Alexa
                  </span>
                </div>
                <p className="mt-1 text-xs leading-snug text-white/75">
                  Click the mic, speak, then click again to send. Or type. Every number is checked against the record.
                </p>
              </div>
              <button
                aria-label="Minimize"
                onClick={() => setOpen(false)}
                className="rounded p-1 text-white/80 hover:bg-white/10 hover:text-white"
              >
                <Minus className="h-4 w-4" />
              </button>
            </div>
            {patientShortId && (
              <div className="mt-3 truncate rounded bg-white/10 px-2 py-1 text-[11px] text-white/85">
                Patient {patientShortId}
              </div>
            )}
          </div>
          <div className="min-h-0 flex-1">
            <VoicePanel patientShortId={patientShortId} bare />
          </div>
        </div>
      )}

      <button
        onClick={toggle}
        aria-label={open ? "Close Talk to GRUS" : "Open Talk to GRUS"}
        title="Talk to GRUS"
        className="flex h-14 w-14 items-center justify-center rounded-full bg-brand-ink text-white shadow-lift transition hover:shadow-glow"
      >
        {open ? <ChevronDown className="h-6 w-6" /> : <Mic className="h-6 w-6" />}
      </button>
    </div>
  );
}
