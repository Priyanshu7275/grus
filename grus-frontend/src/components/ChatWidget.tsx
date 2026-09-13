"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useActivePatient } from "@/lib/activePatientContext";
import { ChatMessage } from "@/lib/types";
import { Citation } from "./SourceDrawer";
import { tokenizeCitations } from "@/lib/briefParser";
import { normalizeSource } from "@/lib/format";

interface DisplayMessage extends ChatMessage {
  id: string;
  sources?: (string | { table: string; id: number })[];
  abstained?: { claim: string; reason: string }[];
  toolsCalled?: { tool: string; args?: Record<string, unknown>; found?: boolean }[];
  isError?: boolean;
}

const STARTER_QUESTIONS = [
  "What is missing from this record?",
  "What is the patient taking at home?",
  "Any history of bleeding?",
];

export function ChatWidget() {
  const { hadmId, asOfHours, headline } = useActivePatient();
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>(STARTER_QUESTIONS);
  const scrollRef = useRef<HTMLDivElement>(null);
  const prevHadmId = useRef<number | null>(null);

  // Reset the thread when the active patient changes.
  useEffect(() => {
    if (prevHadmId.current !== hadmId) {
      setMessages([]);
      prevHadmId.current = hadmId;
      if (hadmId) {
        api
          .getQuestions(hadmId, asOfHours ? { as_of_hours: asOfHours } : {})
          .then((r) => setSuggestions(r.questions?.length ? r.questions : STARTER_QUESTIONS))
          .catch(() => setSuggestions(STARTER_QUESTIONS));
      }
    }
  }, [hadmId, asOfHours]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, open, sending]);

  async function send(text: string) {
    if (!hadmId || !text.trim() || sending) return;
    const userMsg: DisplayMessage = { id: crypto.randomUUID(), role: "user", content: text.trim() };
    const history: ChatMessage[] = messages.map((m) => ({ role: m.role, content: m.content }));
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setSending(true);
    try {
      const res = await api.chat({
        hadm_id: hadmId,
        message: text.trim(),
        as_of_hours: asOfHours ?? undefined,
        history,
      });
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: res.answer,
          sources: res.sources,
          abstained: res.abstained,
          toolsCalled: res.tools_called,
        },
      ]);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Could not reach GRUS chat right now.";
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: "assistant", content: msg, isError: true },
      ]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="fixed bottom-5 right-5 z-40 flex flex-col items-end gap-3">
      {open && (
        <div className="flex h-[600px] max-h-[75vh] w-[380px] max-w-[92vw] flex-col overflow-hidden rounded-2xl border border-ink-900/10 bg-white shadow-lift fade-in">
          {/* Header */}
          <div className="brand-gradient px-5 py-4">
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-white">Ask GRUS</span>
                  <span className="rounded border border-white/30 bg-white/10 px-1.5 py-0.5 text-[10px] text-white/90">
                    built-in
                  </span>
                </div>
                <p className="mt-1 text-xs leading-snug text-white/80">
                  Reasoning chat, answered from stored traces. Every claim cites its source.
                </p>
              </div>
              <button
                aria-label="Minimize"
                onClick={() => setOpen(false)}
                className="rounded p-1 text-white/80 hover:bg-white/10 hover:text-white"
              >
                <MinusIcon />
              </button>
            </div>
            {hadmId && (
              <div className="mt-3 truncate rounded bg-black/20 px-2 py-1 font-mono text-[11px] text-white/80">
                hadm #{hadmId}
                {headline ? ` — ${headline}` : ""}
                {asOfHours !== null && asOfHours !== undefined ? ` · as of ${asOfHours}h` : ""}
              </div>
            )}
          </div>

          {/* Body */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4">
            {!hadmId ? (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
                <p className="text-base text-ink-700">Open a patient&rsquo;s brief to start asking questions.</p>
                <p className="text-sm text-ink-500">Chat is anchored to one patient record at a time.</p>
              </div>
            ) : messages.length === 0 ? (
              <div className="space-y-4">
                <p className="text-sm font-medium text-ink-900">Want help getting started?</p>
                <p className="text-xs text-ink-500">Questions derived from what the rules found on this patient:</p>
                <div className="flex flex-col gap-2">
                  {suggestions.map((q) => (
                    <button
                      key={q}
                      onClick={() => send(q)}
                      className="rounded-lg border border-sev-info/30 bg-sev-infoBg px-3 py-2 text-left text-sm text-ink-900 hover:border-sev-info/60 transition-colors"
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {messages.map((m) => (
                  <MessageBubble key={m.id} message={m} />
                ))}
                {sending && <TypingIndicator />}
              </div>
            )}
          </div>

          {/* Input */}
          <div className="border-t border-ink-900/10 px-3 py-3">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                send(input);
              }}
              className="flex items-center gap-2"
            >
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={!hadmId || sending}
                placeholder={hadmId ? "Ask about this patient…" : "Open a patient first"}
                className="flex-1 rounded-full border border-ink-900/10 bg-ink-900/5 px-4 py-2 text-sm text-ink-900 placeholder:text-ink-400 outline-none focus:border-sev-info/60 disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={!hadmId || sending || !input.trim()}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-sev-info text-white disabled:opacity-40"
                aria-label="Send"
              >
                <SendIcon />
              </button>
            </form>
            <p className="mt-2 text-center text-[10px] text-ink-400">Decision support, not diagnosis.</p>
          </div>
        </div>
      )}

      {/* Launcher bubble */}
      <button
        onClick={() => setOpen((o) => !o)}
        aria-label={open ? "Close chat" : "Open GRUS chat"}
        className="flex h-14 w-14 items-center justify-center rounded-full brand-gradient text-white shadow-lift transition hover:shadow-glow"
      >
        {open ? <ChevronDownIcon /> : <ChatIcon />}
      </button>
    </div>
  );
}

function MessageBubble({ message }: { message: DisplayMessage }) {
  const isUser = message.role === "user";
  if (isUser) {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-sev-info px-3.5 py-2 text-sm text-white">
          {message.content}
        </div>
      </div>
    );
  }

  const tokens = tokenizeCitations(message.content);

  return (
    <div className="flex justify-start">
      <div
        className={`max-w-[92%] rounded-2xl rounded-bl-sm border px-3.5 py-2.5 text-sm leading-relaxed ${
          message.isError
            ? "border-sev-critical/40 bg-sev-criticalBg text-sev-critical"
            : "border-ink-900/10 bg-ink-900/5 text-ink-900"
        }`}
      >
        <p className="whitespace-pre-wrap">
          {tokens.map((t, i) =>
            t.type === "text" ? <span key={i}>{t.value}</span> : <Citation key={i} table={t.table} id={t.id} />
          )}
        </p>

        {message.sources && message.sources.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {message.sources.map((s, i) => {
              const n = normalizeSource(s);
              return <Citation key={i} table={n.table} id={n.id} label={n.label} />;
            })}
          </div>
        )}

        {message.abstained && message.abstained.length > 0 && (
          <div className="mt-2 space-y-1 rounded border border-sev-unknown/40 bg-sev-unknownBg px-2.5 py-2">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-sev-unknown">Could not determine</p>
            {message.abstained.map((a, i) => (
              <p key={i} className="text-xs text-ink-500">
                <span className="text-ink-900">{a.claim}</span> — {a.reason}
              </p>
            ))}
          </div>
        )}

        {message.toolsCalled && message.toolsCalled.length > 0 && (
          <details className="mt-2 text-xs text-ink-400">
            <summary className="cursor-pointer select-none hover:text-ink-500">How did you find this?</summary>
            <ul className="mt-1 space-y-0.5 font-mono text-[11px]">
              {message.toolsCalled.map((t, i) => (
                <li key={i}>
                  {t.found ? "✓" : "✗"} {t.tool}
                  {t.args && Object.keys(t.args).length > 0 ? ` ${JSON.stringify(t.args)}` : ""}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex justify-start">
      <div className="flex items-center gap-1 rounded-2xl rounded-bl-sm border border-ink-900/10 bg-ink-900/5 px-4 py-3">
        <span className="h-1.5 w-1.5 rounded-full bg-ink-400 pulse-dot" style={{ animationDelay: "0ms" }} />
        <span className="h-1.5 w-1.5 rounded-full bg-ink-400 pulse-dot" style={{ animationDelay: "150ms" }} />
        <span className="h-1.5 w-1.5 rounded-full bg-ink-400 pulse-dot" style={{ animationDelay: "300ms" }} />
      </div>
    </div>
  );
}

function ChatIcon() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
      <path
        d="M4 4h16v12H8l-4 4V4z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
function ChevronDownIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
      <path d="M6 9l6 6 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function MinusIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
      <path d="M5 12h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
function SendIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
      <path d="M4 12l16-8-6 8 6 8-16-8z" fill="currentColor" />
    </svg>
  );
}
