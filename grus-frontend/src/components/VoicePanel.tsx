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
import { Mic, Send, Volume2, VolumeX } from "lucide-react";
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

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const getRecognition = (): any => {
  if (typeof window === "undefined") return null;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any;
  const SR = w.SpeechRecognition || w.webkitSpeechRecognition;
  return SR ? new SR() : null;
};

export function VoicePanel({ patientShortId }: { patientShortId?: string }) {
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
      let r = await post({ text: said });
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
    <div className="rounded-lg glass px-4 py-3">
      <div className="mb-2 flex items-center justify-between">
        <p className="font-mono text-[11px] uppercase tracking-wide text-ink-500">
          Talk to GRUS{patientShortId ? ` · patient ${patientShortId}` : ""}
        </p>
        <button
          onClick={() => setVoiceOn((v) => !v)}
          className="text-ink-400 hover:text-ink-900"
          title={voiceOn ? "Mute spoken replies" : "Speak replies aloud"}
        >
          {voiceOn ? <Volume2 size={16} /> : <VolumeX size={16} />}
        </button>
      </div>

      <div className="max-h-72 space-y-2 overflow-y-auto pr-1 text-sm">
        {msgs.length === 0 && (
          <p className="text-xs text-ink-400">
            Hold the mic and ask, for example: &ldquo;What was the INR on arrival?&rdquo;, &ldquo;Current
            vitals&rdquo;, &ldquo;Log: two units O negative started&rdquo;, &ldquo;Give me a handover&rdquo;.
          </p>
        )}
        {msgs.map((m, i) =>
          m.who === "you" ? (
            <div key={i} className="ml-8 rounded-lg bg-ink-900/5 px-3 py-2 text-ink-900">
              {m.text}
            </div>
          ) : m.who === "grus" ? (
            <div key={i} className="mr-8 rounded-lg border border-teal-500/30 bg-teal-50/60 px-3 py-2 text-ink-900">
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
        {busy && <p className="text-xs text-ink-400">GRUS is checking the record…</p>}
        <div ref={endRef} />
      </div>

      <div className="mt-3 flex items-center gap-2">
        {canListen && (
          <button
            onMouseDown={startListening}
            onMouseUp={stopListening}
            onMouseLeave={() => listening && stopListening()}
            onTouchStart={(e) => {
              e.preventDefault();
              startListening();
            }}
            onTouchEnd={stopListening}
            disabled={busy}
            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-white transition ${
              listening ? "scale-110 bg-sev-critical" : "bg-teal-600 hover:bg-teal-700"
            } disabled:opacity-40`}
            title="Hold to talk"
          >
            <Mic size={18} />
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
      {listening && <p className="mt-1 text-[11px] text-sev-critical">Listening… release to send</p>}
    </div>
  );
}
