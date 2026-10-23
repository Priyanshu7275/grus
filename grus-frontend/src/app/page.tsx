"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Mascot } from "@/components/Mascot";
import { HeroSlider, type Slide } from "@/components/HeroSlider";
import {
  ArrowUpRight,
  Clock3,
  FileSearch,
  ShieldAlert,
  Link2,
  Quote,
  Eye,
  Stethoscope,
  ActivitySquare,
  Gauge,
  BookOpenCheck,
  Sparkles,
  Mic,
  ShieldCheck,
  Plus,
  Star,
} from "lucide-react";

const SLIDES: Slide[] = [
  {
    icon: Clock3,
    eyebrow: "The problem",
    title: "15–30 minutes of chart review, before every single call",
    body:
      "Vitals in one tab, labs in another, notes scattered across shifts. Clinicians re-read the same chart from scratch each time a patient status changes — precious minutes an emergency department rarely has.",
    gradient: "from-brand-ink via-brand-deep to-brand-mid",
    video: "/videos/slide1.mp4",
  },
  {
    icon: Sparkles,
    eyebrow: "What GRUS does",
    title: "A clear clinical brief, ready in under 10 seconds",
    body:
      "GRUS reads the chart the moment it changes and hands back a short, structured brief — what's new, what's urgent, and what to check next — so the first read takes seconds, not minutes.",
    gradient: "from-brand-deep to-brand-mid",
    video: "/videos/slide2.mp4",
  },
  {
    icon: ShieldAlert,
    eyebrow: "What makes it different",
    title: "Critical unknowns are surfaced, never hidden",
    body:
      "When data is missing, GRUS says so instead of guessing. Every claim in the brief links straight back to the chart entry it came from, so nothing is taken on faith.",
    gradient: "from-brand-mid to-brand-sky",
    image: "/images/hero-tech.jpg",
  },
  {
    icon: Stethoscope,
    eyebrow: "Get started",
    title: "Sign in to see GRUS on a live patient board",
    body:
      "Enrolled facilities can access GRUS with a hospital code and clinician credentials. Try the demo sign-in to explore the full patient workflow.",
    gradient: "from-brand-deep via-brand-mid to-brand-sky",
    image: "/images/hero-doctor.jpg",
  },
];

const RULES = [
  {
    icon: Link2,
    title: "Sourced, not guessed",
    body: "Every claim in a GRUS brief links straight back to the chart entry — a lab, a vital, a note — that it came from. Click a citation, see the source.",
  },
  {
    icon: Eye,
    title: "Unknowns stay visible",
    body: "When information is missing, GRUS says so rather than filling the gap silently. Critical unknowns are called out, not smoothed over.",
  },
  {
    icon: Quote,
    title: "Decision support, not diagnosis",
    body: "GRUS assists a clinician's judgment — it summarizes, flags, and scores. The clinician still makes the call, always.",
  },
];

const AGENTS = [
  {
    icon: FileSearch,
    name: "Retriever",
    body: "Gathers the facts off the chart — labs, vitals, notes, home medications, prior admissions — and copies every value with the record it came from.",
  },
  {
    icon: ShieldAlert,
    name: "Reconciler",
    body: "Builds one timeline and surfaces contradictions: events out of order, a note that disagrees with the record, hours with no data at all.",
  },
  {
    icon: ActivitySquare,
    name: "Risk",
    body: "Reports risk from two independent sources — rules with published thresholds, and models trained on 20,000 admissions — and shows both when they disagree.",
  },
  {
    icon: Gauge,
    name: "Verifier",
    body: "Waits for the other three, then checks every claim against the source data. Anything without a valid source id is rejected before it reaches you.",
  },
  {
    icon: BookOpenCheck,
    name: "Composer",
    body: "Writes the final brief from the verified data, copying every value and source id exactly — one short, cited read in under 10 seconds.",
  },
];

const MARQUEE = ["Sourced briefs", "Alexa+ voice", "Room record", "Clinical scores", "Desk requests", "Handover"];

function Reveal({ children, delay = 0 }: { children: React.ReactNode; delay?: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ duration: 0.55, delay, ease: "easeOut" }}
    >
      {children}
    </motion.div>
  );
}

function Eyebrow({ children, dark }: { children: React.ReactNode; dark?: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-bold uppercase tracking-[0.12em] ${
        dark ? "border-white/15 bg-white/10 text-white/85" : "border-ink-900/[0.08] bg-white text-ink-700"
      }`}
    >
      <Plus className={`h-3 w-3 ${dark ? "text-brand-sky" : "text-brand-mid"}`} strokeWidth={3} />
      {children}
    </span>
  );
}

function ArrowPill({
  href,
  children,
  variant = "dark",
}: {
  href: string;
  children: React.ReactNode;
  variant?: "dark" | "light";
}) {
  return (
    <Link
      href={href}
      className={`pill-btn shadow-soft ${variant === "dark" ? "bg-brand-ink text-white" : "bg-white text-brand-ink"}`}
    >
      {children}
      <span className={`pill-arrow ${variant === "dark" ? "bg-white text-brand-ink" : "bg-brand-ink text-white"}`}>
        <ArrowUpRight className="h-4 w-4" />
      </span>
    </Link>
  );
}

// Circular rotating-text badge (as on the reference hero).
function RoundBadge() {
  return (
    <div className="relative h-32 w-32">
      <svg viewBox="0 0 120 120" className="absolute inset-0 h-full w-full animate-[spin_18s_linear_infinite]">
        <defs>
          <path id="badge-circle" d="M60,60 m-44,0 a44,44 0 1,1 88,0 a44,44 0 1,1 -88,0" />
        </defs>
        <circle cx="60" cy="60" r="58" fill="rgba(255,255,255,0.55)" stroke="rgba(255,255,255,0.9)" />
        <text fontSize="10.5" fontWeight="700" letterSpacing="2.6" fill="#13195C">
          <textPath href="#badge-circle">DECISION SUPPORT · NOT DIAGNOSIS ·</textPath>
        </text>
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/grus-logo.png" alt="" className="h-12 w-auto" />
      </div>
    </div>
  );
}

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-[#F7F8FC]">
      {/* Floating navbar */}
      <header className="sticky top-3 z-40 px-3 sm:px-6">
        <div className="mx-auto flex h-16 max-w-[1320px] items-center justify-between rounded-2xl bg-brand-ink px-4 shadow-lift sm:px-6">
          <Link href="/" className="flex items-center gap-2.5">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/grus-logo.png" alt="GRUS" className="h-10 w-auto" />
            <span className="text-2xl font-extrabold tracking-tight text-white">GRUS</span>
          </Link>
          <nav className="hidden items-center gap-8 text-sm font-semibold text-white/80 md:flex">
            <a href="#what" className="transition hover:text-white">What it does</a>
            <a href="#agents" className="transition hover:text-white">How it works</a>
            <a href="#voice" className="transition hover:text-white">Voice</a>
          </nav>
          <Link
            href="/login"
            className="inline-flex items-center gap-2 rounded-xl bg-white/10 px-4 py-2.5 text-sm font-bold uppercase tracking-wide text-white transition hover:bg-white/20"
          >
            Sign in
            <ArrowUpRight className="h-4 w-4" />
          </Link>
        </div>
      </header>

      {/* Hero */}
      <section className="px-3 pt-6 sm:px-6">
        <div className="relative mx-auto max-w-[1320px] overflow-hidden rounded-[2rem] bg-gradient-to-b from-[#F4F5FF] via-[#E3E7FE] to-[#BFC8FA]">
          <div className="grid items-end gap-6 lg:grid-cols-[1.05fr_1fr]">
            <div className="px-6 pb-10 pt-14 sm:px-12 lg:pb-44 lg:pt-24">
              <Reveal>
                <span className="inline-flex items-center gap-2 rounded-full bg-white px-3.5 py-1.5 text-xs font-bold text-brand-ink shadow-soft">
                  <span className="relative flex h-2 w-2">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand-mid opacity-60" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-brand-mid" />
                  </span>
                  Emergency decision support · now on Alexa+
                </span>
                <h1 className="mt-6 text-[2.6rem] font-semibold leading-[1.04] tracking-tight text-brand-ink sm:text-[4.1rem]">
                  Every second in the ED belongs to the{" "}
                  <span className="accent-serif text-[1.12em]">patient,</span> not the chart.
                </h1>
                <p className="mt-6 max-w-xl text-base leading-relaxed text-ink-700 sm:text-lg">
                  GRUS reads vitals, labs and notes the moment they land and turns them into a short, sourced clinical
                  brief — so the first read of a patient takes seconds, not a scroll through their whole history.
                </p>
                <div className="mt-8 flex flex-wrap items-center gap-3">
                  <ArrowPill href="/login">Sign in to GRUS</ArrowPill>
                  <a
                    href="#agents"
                    className="rounded-full border border-brand-ink/15 bg-white/60 px-5 py-3 text-sm font-semibold text-brand-ink transition hover:bg-white"
                  >
                    See how it works
                  </a>
                </div>
              </Reveal>
            </div>

            <div className="relative h-[340px] sm:h-[460px] lg:h-full lg:min-h-[640px]">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/images/hero-doctor.jpg"
                alt="A clinician taking a patient's blood pressure"
                className="absolute inset-0 h-full w-full object-cover lg:rounded-bl-[2rem]"
              />
              <div className="absolute inset-0 bg-gradient-to-r from-[#E3E7FE]/70 via-transparent to-transparent lg:from-[#E3E7FE]" />
              <div className="absolute bottom-8 left-6 hidden sm:block lg:bottom-auto lg:left-[-3.5rem] lg:top-[44%]">
                <RoundBadge />
              </div>
            </div>
          </div>
        </div>

        {/* Overlapping feature cards */}
        <div className="relative z-10 mx-auto -mt-6 grid max-w-[1240px] gap-4 px-3 sm:px-0 md:grid-cols-3 lg:-mt-32">
          <Reveal>
            <div className="card flex h-full gap-4 p-5">
              <div className="flex-1">
                <p className="text-lg font-bold text-ink-900">Sourced briefs</p>
                <p className="mt-1 text-sm text-ink-500">Every claim carries the chart row it came from.</p>
                <a href="#what" className="mt-4 inline-flex items-center gap-1 text-sm font-semibold text-brand-deep">
                  Learn more <ArrowUpRight className="h-4 w-4" />
                </a>
              </div>
              <div className="flex w-28 flex-col justify-between rounded-2xl bg-leaf-soft p-3">
                <Clock3 className="h-5 w-5 text-brand-deep" />
                <div>
                  <p className="text-[11px] text-ink-500">First brief in</p>
                  <p className="text-2xl font-extrabold text-brand-ink">&lt;10s</p>
                </div>
              </div>
            </div>
          </Reveal>
          <Reveal delay={0.08}>
            <div className="card h-full p-5">
              <p className="text-lg font-bold text-ink-900">15+ clinical scores</p>
              <p className="mt-1 text-sm text-ink-500">Computed in code, never by the model. Missing findings are asked, not assumed.</p>
              <div className="mt-4 flex flex-wrap items-center gap-2">
                {["NEWS2", "HEART", "PERC", "qSOFA"].map((t) => (
                  <span key={t} className="rounded-full bg-[#F1F3FB] px-2.5 py-1 text-[11px] font-bold text-brand-ink">
                    {t}
                  </span>
                ))}
              </div>
            </div>
          </Reveal>
          <Reveal delay={0.16}>
            <div className="card h-full p-5">
              <div className="flex items-center gap-1 text-[#F5B400]">
                {Array.from({ length: 5 }).map((_, i) => (
                  <Star key={i} className="h-4 w-4 fill-current" />
                ))}
              </div>
              <p className="mt-3 text-[15px] font-medium leading-snug text-ink-900">
                &ldquo;Alexa, what was the INR on arrival?&rdquo; — the answer is only spoken if every number checks out.
              </p>
              <p className="mt-3 flex items-center gap-2 text-sm font-semibold text-brand-deep">
                <Mic className="h-4 w-4" /> Hands-free on Alexa+
              </p>
            </div>
          </Reveal>
        </div>
      </section>

      {/* Marquee */}
      <section className="mt-20 overflow-hidden py-4">
        <div className="marquee flex w-max items-center gap-10 whitespace-nowrap">
          {[...MARQUEE, ...MARQUEE].map((t, i) => (
            <span key={i} className="flex items-center gap-10">
              <span className="outline-text text-6xl font-extrabold tracking-tight sm:text-7xl">{t}</span>
              <span className="flex h-12 w-12 items-center justify-center rounded-full border border-ink-900/10 bg-white text-brand-mid shadow-soft">
                <Plus className="h-5 w-5" strokeWidth={3} />
              </span>
            </span>
          ))}
        </div>
      </section>

      {/* Why GRUS */}
      <section id="what" className="mx-auto max-w-[1320px] px-4 pt-20 sm:px-6">
        <div className="rounded-[2rem] bg-white px-6 py-14 shadow-soft sm:px-12">
          <Reveal>
            <div className="flex flex-wrap items-end justify-between gap-6">
              <div>
                <Eyebrow>Why GRUS</Eyebrow>
                <h2 className="mt-4 max-w-2xl text-3xl font-semibold leading-tight text-brand-ink sm:text-[2.8rem]">
                  Three rules GRUS <span className="accent-serif text-[1.12em]">never</span> breaks
                </h2>
              </div>
              <p className="max-w-md text-base text-ink-500">
                A fast brief is only useful if a clinician can trust it. GRUS is built around three non-negotiable
                principles.
              </p>
            </div>
          </Reveal>
          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {RULES.map((r, i) => (
              <Reveal key={r.title} delay={i * 0.08}>
                <div className="h-full rounded-3xl border border-ink-900/[0.06] bg-[#FAFBFE] p-6 transition hover:shadow-lift">
                  <div className="mb-6 flex h-14 w-14 items-center justify-center rounded-full bg-white text-brand-deep shadow-soft">
                    <r.icon className="h-6 w-6" />
                  </div>
                  <h3 className="text-lg font-bold text-brand-ink">{r.title}</h3>
                  <p className="mt-2 text-[15px] leading-relaxed text-ink-500">{r.body}</p>
                </div>
              </Reveal>
            ))}
            <Reveal delay={0.24}>
              <div className="relative h-full min-h-[260px] overflow-hidden rounded-3xl">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src="/images/hero-tech.jpg" alt="" className="absolute inset-0 h-full w-full object-cover" />
                <div className="absolute inset-0 bg-gradient-to-t from-brand-ink/90 via-brand-ink/30 to-transparent" />
                <div className="absolute bottom-0 p-6">
                  <ShieldCheck className="mb-2 h-6 w-6 text-brand-sky" />
                  <p className="text-lg font-bold leading-snug text-white">Every number checked against the record</p>
                </div>
              </div>
            </Reveal>
          </div>
        </div>
      </section>

      {/* Story slider */}
      <section className="mx-auto max-w-[1320px] px-4 pt-20 sm:px-6">
        <Reveal>
          <div className="mb-8 text-center">
            <Eyebrow>The story</Eyebrow>
            <h2 className="mt-4 text-3xl font-semibold text-brand-ink sm:text-[2.6rem]">
              From a scroll through the chart to a <span className="accent-serif text-[1.12em]">ten-second</span> read
            </h2>
          </div>
          <HeroSlider slides={SLIDES} />
        </Reveal>
      </section>

      {/* Five agents */}
      <section id="agents" className="mx-auto max-w-[1320px] px-4 pt-20 sm:px-6">
        <div className="relative overflow-hidden rounded-[2rem] bg-brand-ink px-6 py-16 sm:px-12">
          <div
            className="pointer-events-none absolute inset-0 opacity-[0.07]"
            style={{
              backgroundImage: "radial-gradient(circle at 1px 1px, white 1px, transparent 0)",
              backgroundSize: "26px 26px",
            }}
          />
          <Reveal>
            <div className="relative flex flex-wrap items-end justify-between gap-6">
              <div>
                <Eyebrow dark>How it works</Eyebrow>
                <h2 className="mt-4 max-w-2xl text-3xl font-semibold leading-tight text-white sm:text-[2.8rem]">
                  Five specialist agents, <span className="accent-serif text-[1.12em] text-brand-sky">one</span> clear brief
                </h2>
              </div>
              <p className="max-w-md text-base text-white/70">
                Each agent has one job. Together they turn a raw chart into something a clinician can act on at a glance.
              </p>
            </div>
          </Reveal>
          <div className="relative mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            {AGENTS.map((a, i) => (
              <Reveal key={a.name} delay={i * 0.06}>
                <div className="group h-full rounded-3xl border border-white/10 bg-white/[0.06] p-6 transition hover:bg-white">
                  <div className="flex items-center justify-between">
                    <span className="text-4xl font-extrabold text-white/25 transition group-hover:text-brand-mid/30">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span className="flex h-11 w-11 items-center justify-center rounded-full bg-white/10 text-brand-sky transition group-hover:bg-leaf-soft group-hover:text-brand-deep">
                      <a.icon className="h-5 w-5" />
                    </span>
                  </div>
                  <h3 className="mt-8 text-lg font-bold text-white transition group-hover:text-brand-ink">{a.name}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-white/65 transition group-hover:text-ink-500">{a.body}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* Voice — three steps */}
      <section id="voice" className="mx-auto max-w-[1320px] px-4 pt-20 sm:px-6">
        <div className="grid items-center gap-10 lg:grid-cols-2">
          <Reveal>
            <div className="relative">
              <Mascot className="h-[360px] sm:h-[460px]" />
              <div className="absolute -bottom-6 right-6 hidden rounded-2xl bg-white p-4 shadow-lift sm:block">
                <p className="text-xs text-ink-400">Room record</p>
                <p className="flex items-center gap-2 text-sm font-bold text-sev-ok">
                  <ShieldCheck className="h-4 w-4" /> Record intact · every entry checked
                </p>
              </div>
            </div>
          </Reveal>
          <Reveal delay={0.1}>
            <Eyebrow>New · Alexa+</Eyebrow>
            <h2 className="mt-4 text-3xl font-semibold leading-tight text-brand-ink sm:text-[2.6rem]">
              Ask, log and hand over — <span className="accent-serif text-[1.12em]">without</span> touching a screen
            </h2>
            <p className="mt-4 text-base text-ink-500">
              In a resus bay your hands are busy. GRUS Room puts the same checked answers behind your voice, and keeps a
              tamper-proof record of everything said in the room.
            </p>
            <div className="mt-8 space-y-3">
              {[
                ["01", "Ask Alexa", "“Current vitals for patient 5843”, “Ask the desk for two units of O negative.”"],
                ["02", "GRUS checks every number", "A value that can't be traced to the record is removed before it's spoken."],
                ["03", "It's written to the room record", "Hash-chained and append-only, with a one-tap integrity check."],
              ].map(([n, t, d]) => (
                <div key={n} className="flex gap-4 rounded-2xl border border-ink-900/[0.06] bg-white p-4 shadow-soft">
                  <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-brand-ink text-sm font-bold text-white">
                    {n}
                  </span>
                  <div>
                    <p className="font-bold text-brand-ink">{t}</p>
                    <p className="mt-0.5 text-sm text-ink-500">{d}</p>
                  </div>
                </div>
              ))}
            </div>
          </Reveal>
        </div>
      </section>

      {/* Final CTA */}
      <section className="mx-auto max-w-[1320px] px-4 pb-20 pt-24 sm:px-6">
        <Reveal>
          <div className="relative overflow-hidden rounded-[2rem] bg-gradient-to-br from-[#E9ECFF] via-[#D6DCFD] to-[#B9C3F8] px-8 py-16 text-center sm:px-16">
            <h2 className="mx-auto max-w-3xl text-3xl font-semibold leading-tight text-brand-ink sm:text-[2.8rem]">
              See the chart. Trust the brief. <span className="accent-serif text-[1.12em]">Treat</span> the patient.
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-base text-ink-700">
              Sign in with the demo hospital access code to explore GRUS on a live patient board.
            </p>
            <div className="mt-8 flex justify-center">
              <ArrowPill href="/login">Sign in to GRUS</ArrowPill>
            </div>
          </div>
        </Reveal>
      </section>

      {/* Footer */}
      <footer className="bg-brand-ink px-4 pb-8 pt-14 text-white sm:px-6">
        <div className="mx-auto grid max-w-[1320px] gap-10 md:grid-cols-[1.4fr_1fr_1fr]">
          <div>
            <div className="flex items-center gap-2.5">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/grus-logo.png" alt="" className="h-10 w-auto" />
              <span className="text-2xl font-extrabold">GRUS</span>
            </div>
            <p className="mt-4 max-w-sm text-sm leading-relaxed text-white/60">
              Emergency decision support that cites its sources and says what it doesn&apos;t know. Named after the crane —
              <em> Grus</em> in Latin.
            </p>
          </div>
          <div>
            <p className="text-sm font-bold">Product</p>
            <ul className="mt-4 space-y-2.5 text-sm text-white/60">
              <li><a href="#what" className="hover:text-white">What it does</a></li>
              <li><a href="#agents" className="hover:text-white">How it works</a></li>
              <li><a href="#voice" className="hover:text-white">Voice on Alexa+</a></li>
              <li><Link href="/login" className="hover:text-white">Sign in</Link></li>
            </ul>
          </div>
          <div>
            <p className="text-sm font-bold">Built with</p>
            <ul className="mt-4 space-y-2.5 text-sm text-white/60">
              <li>Amazon Bedrock &amp; AgentCore</li>
              <li>Alexa+ &amp; Model Context Protocol</li>
              <li>Aurora PostgreSQL</li>
              <li>MIMIC-IV (de-identified)</li>
            </ul>
          </div>
        </div>
        <div className="mx-auto mt-12 flex max-w-[1320px] flex-wrap justify-between gap-3 border-t border-white/10 pt-6 text-xs text-white/50">
          <span>© GRUS · Decision support, not diagnosis.</span>
          <span>Demo data only — not for clinical use.</span>
        </div>
      </footer>
    </main>
  );
}
