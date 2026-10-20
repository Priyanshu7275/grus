"use client";

/**
 * Desk — the main desk / nursing station screen.
 *
 * When a doctor says "Alexa, ask the desk for two units of O negative",
 * the request appears here within a few seconds (stat first). The desk
 * taps Acknowledge, then Done; each tap is written to that patient's
 * room record, and the doctor can ask Alexa for the status. Reminders
 * that come due pop up at the top with a short beep.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { BellRing, Check, CheckCheck, Volume2, VolumeX, X } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { DeskRequest, DueReminder } from "@/lib/types";

const REQUESTS_MS = 4000;
const REMINDERS_MS = 15000;

const PRIORITY: Record<string, string> = {
  stat: "bg-sev-critical text-white",
  urgent: "bg-sev-warning text-white",
  routine: "bg-ink-900/10 text-ink-700",
};

export default function DeskPage() {
  const [requests, setRequests] = useState<DeskRequest[] | null>(null);
  const [due, setDue] = useState<DueReminder[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [soundOn, setSoundOn] = useState(true);
  const seen = useRef<Set<number>>(new Set());
  const first = useRef(true);

  const beep = useCallback(() => {
    if (!soundOn || typeof window === "undefined") return;
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const Ctx = window.AudioContext || (window as any).webkitAudioContext;
      const ctx = new Ctx();
      [0, 0.25].forEach((t) => {
        const o = ctx.createOscillator();
        const g = ctx.createGain();
        o.frequency.value = 880;
        g.gain.value = 0.15;
        o.connect(g).connect(ctx.destination);
        o.start(ctx.currentTime + t);
        o.stop(ctx.currentTime + t + 0.15);
      });
    } catch {
      // Sound is a nice-to-have; the banner still shows.
    }
  }, [soundOn]);

  const loadRequests = useCallback(async () => {
    try {
      const r = await api.deskRequests();
      // Beep for a request we haven't seen before (not on first load).
      const fresh = r.requests.filter((q) => !seen.current.has(q.request_id));
      r.requests.forEach((q) => seen.current.add(q.request_id));
      if (!first.current && fresh.some((q) => q.status === "open")) beep();
      first.current = false;
      setRequests(r.requests);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not reach the desk queue.");
    }
  }, [beep]);

  const checkReminders = useCallback(async () => {
    try {
      // Each due reminder is returned once, so keep what's on screen
      // until the desk dismisses it.
      const r = await api.deskCheckReminders();
      if (r.due.length) {
        setDue((old) => [...old, ...r.due.filter((d) => !old.some((o) => o.reminder_id === d.reminder_id))]);
        beep();
      }
    } catch {
      // Shown via the requests error banner if the backend is down.
    }
  }, [beep]);

  useEffect(() => {
    loadRequests();
    checkReminders();
    const a = setInterval(loadRequests, REQUESTS_MS);
    const b = setInterval(checkReminders, REMINDERS_MS);
    return () => {
      clearInterval(a);
      clearInterval(b);
    };
  }, [loadRequests, checkReminders]);

  const update = async (q: DeskRequest, status: "acknowledged" | "done" | "cancelled") => {
    setBusyId(q.request_id);
    try {
      await api.deskUpdate(q.request_id, status);
      await loadRequests();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not update the request.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="mx-auto max-w-[1000px] px-4 py-6">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-ink-900">Desk</h1>
          <p className="text-xs text-ink-400">Requests from rooms, live. Stat first, then oldest first.</p>
        </div>
        <button
          onClick={() => setSoundOn((s) => !s)}
          className="inline-flex items-center gap-1.5 rounded-full border border-ink-900/10 px-3 py-1.5 text-xs text-ink-500 hover:text-ink-900"
        >
          {soundOn ? <Volume2 size={14} /> : <VolumeX size={14} />}
          {soundOn ? "Sound on" : "Sound off"}
        </button>
      </div>

      {due.length > 0 && (
        <div className="mb-4 space-y-2">
          {due.map((d) => (
            <div
              key={d.reminder_id}
              className="flex items-center justify-between rounded-lg border border-sev-critical/40 bg-sev-criticalBg px-4 py-3 text-sev-critical"
            >
              <span className="flex items-center gap-2 text-sm font-medium">
                <BellRing size={16} />
                <Link href={`/patients/${d.hadm_id}/room`} className="font-mono underline-offset-2 hover:underline">
                  {d.short_id}
                </Link>
                {d.text}
                <span className="font-mono text-xs opacity-70">due {d.due_clock}</span>
              </span>
              <button
                onClick={() => setDue((old) => old.filter((o) => o.reminder_id !== d.reminder_id))}
                className="rounded p-1 hover:bg-sev-critical/10"
                title="Dismiss"
              >
                <X size={16} />
              </button>
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-lg border border-sev-critical/40 bg-sev-criticalBg px-4 py-3 text-sm text-sev-critical">
          {error}
        </div>
      )}

      {requests === null && !error && <div className="h-24 animate-pulse rounded-lg bg-white/70" />}

      {requests && requests.length === 0 && (
        <div className="rounded-lg glass px-4 py-10 text-center text-sm text-ink-400">
          No open requests. Try &ldquo;Alexa, ask grus assistant to ask the desk for a crossmatch for patient 5843&rdquo;.
        </div>
      )}

      <div className="space-y-2">
        {requests?.map((q) => (
          <div key={q.request_id} className="flex flex-wrap items-center gap-3 rounded-lg glass px-4 py-3">
            <span
              className={`w-16 shrink-0 rounded px-2 py-0.5 text-center text-[11px] font-semibold uppercase ${
                PRIORITY[q.priority] || PRIORITY.routine
              }`}
            >
              {q.priority}
            </span>
            <Link
              href={`/patients/${q.hadm_id}/room`}
              className="shrink-0 rounded-md bg-ink-900 px-2 py-0.5 font-mono text-sm text-white"
              title={`Admission ${q.hadm_id}`}
            >
              {q.short_id}
            </Link>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink-900">{q.item}</p>
              <p className="text-xs text-ink-400">
                {q.requested_by} · {q.requested_clock}
                {q.status === "acknowledged" && <span className="ml-2 text-sev-ok">acknowledged</span>}
              </p>
            </div>
            <div className="flex items-center gap-2">
              {q.status === "open" && (
                <button
                  disabled={busyId === q.request_id}
                  onClick={() => update(q, "acknowledged")}
                  className="inline-flex items-center gap-1 rounded border border-ink-900/10 bg-white px-3 py-1.5 text-xs font-medium text-ink-900 hover:border-ink-900/25 disabled:opacity-40"
                >
                  <Check size={14} /> Acknowledge
                </button>
              )}
              <button
                disabled={busyId === q.request_id}
                onClick={() => update(q, "done")}
                className="inline-flex items-center gap-1 rounded bg-sev-ok px-3 py-1.5 text-xs font-medium text-white disabled:opacity-40"
              >
                <CheckCheck size={14} /> Done
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
