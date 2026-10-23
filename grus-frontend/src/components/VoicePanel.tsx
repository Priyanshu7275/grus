"use client";

/**
 * "Talk to GRUS" — the same voice agent as the Alexa skill, on the website.
 *
 * Hold the mic, speak, release. The browser turns speech into text
 * (Chrome's Web Speech API), the backend forwards it to the Alexa bridge
 * agent (Nova 2 Lite on Bedrock AgentCore), which calls the GRUS MCP
 * tools. The reply is spoken aloud and shown with the tool that answered.
 * Typing works too, for browsers without speech recognition.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Mic, Send, Square, Volume2, VolumeX } from "lucide-react";
import { api } from "@/lib/api";

type Tool = { name: string; status: string; ms: number | null };
type Msg =
  | { who: "you"; text: string }
  | { who: "grus"; text: string; tools: Tool[]; ms?: number }
  | { who: "note"; text: string };

type TurnReply = {
  status: "done" | "pending" | "question" | "error";
  speech: string;
  question?: string | null;
  tools: Tool[];
  elapsed_ms: number;
};

// On a patient's page, questions are about that patient unless they name
// another patient's number or ask about all patients.
const NUM_WORD =
  "zero|oh|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety";
const NAMES_A_PATIENT = new RegExp(
  `\\b\\d{4,}\\b|\\bpatient\\s+(?:ending\\s+(?:in\\s+)?|number\\s+)?(?:\\d|(?:${NUM_WORD})\\b)`,
  "i"
);
const ABOUT_EVERYONE = /\bpatients\b|\bboard\b|\beveryone\b|\bwho needs\b/i;

export function withPatient(text: string, shortId?: string): string {
  if (!shortId || NAMES_A_PATIENT.test(text) || ABOUT_EVERYONE.test(text)) return text;
  return `For patient ${shortId}: ${text}`;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const getRecognition = (): any => {
  if (typeof window === "undefined") return null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any;
  const SR = w.SpeechRecognition || w.webkitSpeechRecognition;
  return SR ? new SR() : null;
};

export function VoicePanel({ patientShortId, bare = false }: { patientShortId?: string; bare?: boolean }) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [listening, setListening] = useState(false);
  const [busy, setBusy] = useState(false);
  const [interim, setInterim] = useState("");
  const [typed, setTyped] = useState("");
  const [voiceOn, setVoiceOn] = useState(true);
  const [canListen, setCanListen] = useState(true);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const recRef = useRef<any>(null);
  const heardRef = useRef("");
  const sessionRef = useRef(
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `web-${Date.now()}-${Math.random().toString(36).slice(2)}`
  );
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setCanListen(!!getRecognition());
  }, []);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [msgs, interim]);

  const speak = useCallback(
    (text: string) => {
      if (!voiceOn || typeof window === "undefined" || !window.speechSynthesis) return;
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "en-US";
      const v = window.speechSynthesis
        .getVoices()
        .find((x) => x.lang === "en-US" && /female|samantha|zira|aria|jenny|google us/i.test(x.name));
      if (v) u.voice = v;
      u.rate = 1.0;
      window.speechSynthesis.speak(u);
    },
    [voiceOn]
  );

  const post = async (body: Record<string, unknown>): Promise<TurnReply> => {
    const res = await fetch(`${api.baseUrl}/voice/turn`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionRef.current, patient: patientShortId, ...body }),
    });
    if (!res.ok) throw new Error(`Voice request failed (${res.status})`);
    return res.json();
  };

  const send = async (text: string) => {
    const said = text.trim();
    if (!said || busy) return;
    setMsgs((m) => [...m, { who: "you", text: said }]);
    setBusy(true);
    try {
      let r = await post({ text: withPatient(said, patientShortId) });
      // The agent can answer "still working": ask again, as Alexa would.
      for (let i = 0; r.status === "pending" && i < 8; i++) {
        await new Promise((ok) => setTimeout(ok, 2000));
        r = await post({ poll: true });
      }
      const reply = r.question || r.speech || "I didn't get an answer. Please try again.";
      setMsgs((m) => [...m, { who: "grus", text: reply, tools: r.tools || [], ms: r.elapsed_ms }]);
      speak(reply);
    } catch {
      setMsgs((m) => [...m, { who: "note", text: "Couldn't reach the voice agent. Please try again." }]);
    } finally {
      setBusy(false);
    }
  };

  const startListening = () => {
    if (busy || listening) return;
    const rec = getRecognition();
    if (!rec) {
      setCanListen(false);
      return;
    }
    window.speechSynthesis?.cancel();
    heardRef.current = "";
    setInterim("");
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.continuous = true;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    rec.onresult = (e: any) => {
      let finalText = "";
      let partial = "";
      for (let i = 0; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalText += t;
        else partial += t;
      }
      heardRef.current = finalText;
      setInterim((finalText + " " + partial).trim());
    };
    rec.onerror = () => setListening(false);
    rec.onend = () => {
      setListening(false);
      const said = (heardRef.current || "").trim();
      setInterim("");
      if (said) send(said);
    };
    recRef.current = rec;
    rec.start();
    setListening(true);
  };

  const stopListening = () => {
    recRef.current?.stop();
  };

  return (
    <div className={bare ? "flex h-full flex-col px-4 pb-4 pt-3" : "card px-5 py-4"}>
      <div className={`mb-2 flex items-center ${bare ? "justify-end" : "justify-between"}`}>
        {!bare && (
          <p className="section-label">
            Talk to GRUS{patientShortId ? ` · patient ${patientShortId}` : ""}
          </p>
        )}
        <button
          onClick={() => setVoiceOn((v) => !v)}
          className="text-ink-400 hover:text-ink-900"
          title={voiceOn ? "Mute spoken replies" : "Speak replies aloud"}
        >
          {voiceOn ? <Volume2 size={16} /> : <VolumeX size={16} />}
        </button>
      </div>

      <div className={`${bare ? "min-h-0 flex-1" : "max-h-72"} space-y-2 overflow-y-auto pr-1 text-sm`}>
        {msgs.length === 0 && (
          <p className="text-xs text-ink-400">
            Click the mic and ask, for example: &ldquo;What was the INR on arrival?&rdquo;, &ldquo;Current
            vitals&rdquo;, &ldquo;Log: two units O negative started&rdquo;, &ldquo;Give me a handover&rdquo;.
          </p>
        )}
        {msgs.map((m, i) =>
          m.who === "you" ? (
            <div key={i} className="ml-8 rounded-lg bg-ink-900/5 px-3 py-2 text-ink-900">
              {m.text}
            </div>
          ) : m.who === "grus" ? (
            <div key={i} className="mr-8 rounded-lg border border-brand-mid/25 bg-leaf-soft/70 px-3 py-2 text-ink-900">
              <p>{m.text}</p>
              {m.tools.length > 0 && (
                <p className="mt-1 font-mono text-[10px] text-ink-400">
                  {m.tools.map((t) => `${t.name}${t.ms != null ? ` ${t.ms}ms` : ""}`).join(" · ")}
                  {m.ms != null ? ` · total ${(m.ms / 1000).toFixed(1)}s` : ""}
                </p>
              )}
            </div>
          ) : (
            <p key={i} className="text-xs text-sev-critical">
              {m.text}
            </p>
          )
        )}
        {interim && <div className="ml-8 rounded-lg border border-dashed border-ink-900/15 px-3 py-2 text-ink-500">{interim}…</div>}
        {busy && (
          <div className="mr-8 flex items-center gap-2 rounded-lg border border-brand-mid/20 bg-leaf-soft/60 px-3 py-2 text-xs text-ink-500">
            <Loader2 size={14} className="animate-spin text-brand-deep" />
            GRUS is checking the record…
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="mt-3 flex items-center gap-2">
        {canListen && (
          <button
            type="button"
            onClick={() => (listening ? stopListening() : startListening())}
            disabled={busy}
            className={`relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-white transition ${
              listening ? "bg-sev-critical" : "bg-brand-deep hover:bg-brand-mid"
            } disabled:cursor-wait disabled:opacity-60`}
            title={busy ? "GRUS is answering…" : listening ? "Click to stop and send" : "Click to talk"}
            aria-label={busy ? "GRUS is answering" : listening ? "Stop and send" : "Start talking"}
          >
            {listening && <span className="absolute inset-0 animate-ping rounded-full bg-sev-critical opacity-40" />}
            {busy ? (
              <Loader2 size={18} className="animate-spin" />
            ) : listening ? (
              <Square size={14} className="relative fill-current" />
            ) : (
              <Mic size={18} />
            )}
          </button>
        )}
        <form
          className="flex flex-1 items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const t = typed;
            setTyped("");
            send(t);
          }}
        >
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={canListen ? "…or type a question" : "Type a question"}
            className="flex-1 rounded border border-ink-900/10 bg-white/70 px-3 py-2 text-sm outline-none focus:border-ink-900/30"
            disabled={busy}
          />
          <button type="submit" disabled={busy || !typed.trim()} className="text-ink-500 hover:text-ink-900 disabled:opacity-30">
            <Send size={16} />
          </button>
        </form>
      </div>
      {listening && (
        <p className="mt-1 flex items-center gap-1.5 text-[11px] font-medium text-sev-critical">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-sev-critical" />
          Listening… click the button again to send
        </p>
      )}
    </div>
  );
}
