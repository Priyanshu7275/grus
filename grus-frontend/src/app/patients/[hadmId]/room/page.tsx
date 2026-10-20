"use client";

/**
 * Room record — the live "black box" for one patient.
 *
 * Everything said to Alexa or the Talk to GRUS panel (log, ask the desk,
 * remind me, scores, handover) is written to a hash-chained record on the
 * backend. This page shows it as it happens (polls every 3 s for new
 * entries), proves it hasn't been edited ("Record intact"), lists
 * reminders, and lets staff add a typed entry to the same record.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ShieldCheck, ShieldAlert, Bell, Send } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { RoomEvent, RoomReminder, RoomTimeline, RoomVerify } from "@/lib/types";
import { VoicePanel } from "@/components/VoicePanel";

const POLL_MS = 3000;
const VERIFY_MS = 30000;

const KIND_STYLE: Record<string, { label: string; cls: string }> = {
  session_start: { label: "Room", cls: "bg-brand-deep/10 text-brand-deep" },
  session_end: { label: "Room", cls: "bg-brand-deep/10 text-brand-deep" },
  log: { label: "Log", cls: "bg-ink-900/5 text-ink-700" },
  request: { label: "Desk", cls: "bg-sev-warningBg text-sev-warning" },
  request_update: { label: "Desk", cls: "bg-sev-okBg text-sev-ok" },
  reminder: { label: "Reminder", cls: "bg-sev-infoBg text-sev-info" },
  reminder_fired: { label: "Reminder", cls: "bg-sev-criticalBg text-sev-critical" },
  score: { label: "Score", cls: "bg-brand-sky/20 text-brand-deep" },
  handover: { label: "Handover", cls: "bg-ink-900 text-white" },
};

export default function RoomPage() {
  const params = useParams<{ hadmId: string }>();
  const hadmId = Number(params.hadmId);

  const [room, setRoom] = useState<Omit<RoomTimeline, "events"> | null>(null);
  const [events, setEvents] = useState<RoomEvent[]>([]);
  const [verify, setVerify] = useState<RoomVerify | null>(null);
  const [reminders, setReminders] = useState<RoomReminder[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);

  const lastSeq = useRef(0);
  const endRef = useRef<HTMLDivElement>(null);

  const poll = useCallback(async () => {
    try {
      // Fetch until caught up (the backend returns at most 100 per call).
      for (let i = 0; i < 10; i++) {
        const r = await api.roomTimeline(hadmId, lastSeq.current);
        const { events: fresh, ...rest } = r;
        setRoom(rest);
        if (fresh.length) {
          lastSeq.current = r.last_seq;
          setEvents((old) => [...old, ...fresh.filter((e) => !old.some((o) => o.seq === e.seq))]);
        }
        if (fresh.length < 100) break;
      }
      const rem = await api.roomReminders(hadmId);
      // Upcoming ones, plus ones that went off in the last hour.
      const hourAgo = Date.now() - 60 * 60 * 1000;
      setReminders(rem.reminders.filter((x) => x.status === "pending" || Date.parse(x.due_at) > hourAgo));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not reach the room record.");
    }
  }, [hadmId]);

  const check = useCallback(() => {
    api
      .roomVerify(hadmId)
      .then(setVerify)
      .catch(() => {});
  }, [hadmId]);

  useEffect(() => {
    if (!hadmId) return;
    lastSeq.current = 0;
    setEvents([]);
    poll();
    check();
    const a = setInterval(poll, POLL_MS);
    const b = setInterval(check, VERIFY_MS);
    return () => {
      clearInterval(a);
      clearInterval(b);
    };
  }, [hadmId, poll, check]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [events.length]);

  const addNote = async () => {
    const t = note.trim();
    if (!t || saving) return;
    setSaving(true);
    try {
      await api.roomLog(hadmId, t);
      setNote("");
      await poll();
      check();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save the entry.");
    } finally {
      setSaving(false);
    }
  };

  const shortId = room?.short_id || String(hadmId).slice(-4);

  return (
    <div className="mx-auto max-w-[1200px] px-4 py-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-xs text-ink-400">
            <Link href="/patients" className="hover:text-ink-500">
              Patients
            </Link>
            <span>/</span>
            <Link href={`/patients/${hadmId}`} className="font-mono hover:text-ink-500">
              hadm #{hadmId}
            </Link>
            <span>/</span>
            <span>Room</span>
          </div>
          <h1 className="mt-1 flex items-center gap-2 text-lg font-semibold text-ink-900">
            <span className="rounded-md bg-ink-900 px-2 py-0.5 font-mono text-sm text-white">{shortId}</span>
            Room record
            {room && (
              <span
                className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium ${
                  room.room_open ? "bg-sev-okBg text-sev-ok" : "bg-ink-900/5 text-ink-500"
                }`}
              >
                {room.room_open ? `Open${room.session?.room ? ` · ${room.session.room}` : ""}` : "No open room"}
              </span>
            )}
          </h1>
        </div>
        <IntegrityBadge verify={verify} />
      </div>

      {error && (
        <div className="mb-4 rounded-lg border border-sev-critical/40 bg-sev-criticalBg px-4 py-3 text-sm text-sev-critical">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_360px]">
        <div className="rounded-lg glass px-4 py-3">
          <p className="mb-3 font-mono text-[11px] uppercase tracking-wide text-ink-500">
            Timeline · updates live · {events.length} entr{events.length === 1 ? "y" : "ies"}
          </p>

          <div className="max-h-[60vh] space-y-1.5 overflow-y-auto pr-1">
            {events.length === 0 && (
              <p className="py-6 text-center text-sm text-ink-400">
                Nothing recorded yet. Try &ldquo;Alexa, ask grus assistant to start a room for patient {shortId}&rdquo;,
                or type an entry below.
              </p>
            )}
            {events.map((e) => {
              const k = KIND_STYLE[e.kind] || { label: e.kind, cls: "bg-ink-900/5 text-ink-500" };
              return (
                <div key={e.seq} className="flex items-start gap-3 rounded-md px-2 py-1.5 hover:bg-white/60">
                  <span className="w-11 shrink-0 pt-0.5 font-mono text-xs text-ink-400">{e.clock}</span>
                  <span className={`w-20 shrink-0 rounded px-1.5 py-0.5 text-center text-[10px] font-medium ${k.cls}`}>
                    {k.label}
                  </span>
                  <span className="flex-1 text-sm text-ink-900">{stripClock(e.text)}</span>
                  <span className="shrink-0 pt-0.5 font-mono text-[10px] text-ink-400" title={e.tag}>
                    {e.actor} · #{e.seq}
                  </span>
                </div>
              );
            })}
            <div ref={endRef} />
          </div>

          <form
            className="mt-3 flex items-center gap-2 border-t border-ink-900/5 pt-3"
            onSubmit={(ev) => {
              ev.preventDefault();
              addNote();
            }}
          >
            <input
              value={note}
              onChange={(ev) => setNote(ev.target.value)}
              maxLength={500}
              placeholder="Add an entry, e.g. “Two units O negative started”"
              className="flex-1 rounded border border-ink-900/10 bg-white/70 px-3 py-2 text-sm outline-none focus:border-ink-900/30"
              disabled={saving}
            />
            <button
              type="submit"
              disabled={saving || !note.trim()}
              className="inline-flex items-center gap-1.5 rounded bg-brand-deep px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
            >
              <Send size={14} /> Log
            </button>
          </form>
          <p className="mt-1 text-[11px] text-ink-400">
            Entries can&apos;t be edited or deleted. A correction is a new entry.
          </p>
        </div>

        <div className="space-y-4">
          <VoicePanel patientShortId={shortId} />

          <div className="rounded-lg glass px-4 py-3">
            <p className="mb-2 flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wide text-ink-500">
              <Bell size={12} /> Reminders
            </p>
            {reminders.length === 0 && <p className="text-xs text-ink-400">No reminders set.</p>}
            <div className="space-y-1.5">
              {reminders.map((r) => (
                <div
                  key={r.reminder_id}
                  className={`flex items-center justify-between rounded-md px-2.5 py-1.5 text-sm ${
                    r.is_due ? "bg-sev-criticalBg text-sev-critical" : "bg-white/60 text-ink-900"
                  }`}
                >
                  <span>{r.text}</span>
                  <span className="font-mono text-xs">{r.is_due ? `due ${r.due_clock}` : r.due_clock}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function IntegrityBadge({ verify }: { verify: RoomVerify | null }) {
  if (!verify) return null;
  return verify.ok ? (
    <div
      className="inline-flex items-center gap-1.5 rounded-full border border-sev-ok/30 bg-sev-okBg px-3 py-1.5 text-xs font-medium text-sev-ok"
      title={verify.message}
    >
      <ShieldCheck size={14} /> Record intact · {verify.events} entries
    </div>
  ) : (
    <div
      className="inline-flex items-center gap-1.5 rounded-full border border-sev-critical/30 bg-sev-criticalBg px-3 py-1.5 text-xs font-medium text-sev-critical"
      title={verify.message}
    >
      <ShieldAlert size={14} /> Record altered at entry {verify.broken_at}
    </div>
  );
}

// The backend line starts with "10:42, ..."; the time already has a column.
function stripClock(text: string) {
  return text.replace(/^\d{1,2}:\d{2},\s*/, "");
}
