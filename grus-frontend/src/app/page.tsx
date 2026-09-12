"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { GrusLogo } from "@/components/GrusLogo";
import { Mascot } from "@/components/Mascot";
import { HeroSlider, type Slide } from "@/components/HeroSlider";
import {
  ArrowRight,
  LogIn,
  Clock3,
  FileSearch,
  ShieldAlert,
  Link2,
  Quote,
  Eye,
  Stethoscope,
  ClipboardList,
  ActivitySquare,
  Gauge,
  BookOpenCheck,
  Sparkles,
} from "lucide-react";

const SLIDES: Slide[] = [
  {
    icon: Clock3,
    eyebrow: "The problem",
    title: "15–30 minutes of chart review, before every single call",
    body:
      "Vitals in one tab, labs in another, notes scattered across shifts. Clinicians re-read the same chart from scratch each time a patient status changes — precious minutes an emergency department rarely has.",
    gradient: "from-ink-900 via-brand-deep to-brand-mid",
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
     image: "/images/slide3.jpg",
  },
  {
    icon: Stethoscope,
    eyebrow: "Get started",
    title: "Sign in to see GRUS on a live patient board",
    body:
      "Enrolled facilities can access GRUS with a hospital code, clinician credentials, and a one-time passcode. Try the demo sign-in to explore the full patient workflow.",
    gradient: "from-brand-deep via-brand-mid to-brand-sky",
    image: "/images/slide4.jpg", 
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
    name: "Intake Agent",
    body: "Watches new labs, vitals, and notes land on a patient's chart and keeps the record current in real time.",
  },
  {
    icon: ShieldAlert,
    name: "Rules Agent",
    body: "Runs fast rule-based checks the moment new data arrives, so urgent alerts never wait on a slower model.",
  },
  {
    icon: ActivitySquare,
    name: "Risk Agent",
    body: "Applies trained risk models to the current chart, kept separate from the rule-based alerts for a second, independent read.",
  },
  {
    icon: Gauge,
    name: "Scoring Agent",
    body: "Computes validated clinical scores — PERC, Wells, HEART, qSOFA, NEWS2, and more — and asks for exactly the missing input needed to complete each one.",
  },
  {
    icon: BookOpenCheck,
    name: "Brief Agent",
    body: "Synthesizes every other agent's findings into one short, sourced brief a clinician can read in under 10 seconds.",
  },
];

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

export default function LandingPage() {
  return (
    <main className="app-bg min-h-screen">
      {/* Public navbar */}
      <header className="sticky top-0 z-40 w-full">
        <div className="glass border-b border-ink-900/5">
          <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6">
            <GrusLogo />
            <nav className="hidden items-center gap-1 sm:flex">
              <a href="#what" className="rounded-full px-3.5 py-1.5 text-sm font-medium text-ink-500 transition hover:text-ink-900">
                What it does
              </a>
              <a href="#agents" className="rounded-full px-3.5 py-1.5 text-sm font-medium text-ink-500 transition hover:text-ink-900">
                How it works
              </a>
            </nav>
            <Link
              href="/login"
              className="inline-flex items-center gap-1.5 rounded-full brand-gradient px-5 py-2 text-sm font-semibold text-white shadow-soft transition hover:shadow-glow"
            >
              <LogIn className="h-4 w-4" />
              Sign in
            </Link>
          </div>
        </div>
      </header>

      {/* Hero */}
      <section className="mx-auto max-w-7xl px-4 pt-14 sm:px-6 sm:pt-20">
        <div className="grid items-center gap-12 lg:grid-cols-2">
          <Reveal>
           
            <h1 className="mt-5 text-4xl font-extrabold leading-tight text-ink-900 sm:text-5xl">
              Every second in the ED
              <br />
              belongs to the patient, <span className="brand-text">not the chart</span>.
            </h1>
            <p className="mt-5 max-w-xl text-base leading-relaxed text-ink-500 sm:text-lg">
              GRUS reads vitals, labs, and notes the moment they land and turns them into a short,
              sourced clinical brief — so the first read of a patient takes seconds, not a scroll
              through their whole history.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link
                href="/login"
                className="inline-flex items-center gap-2 rounded-xl brand-gradient px-6 py-3 text-sm font-semibold text-white shadow-soft transition hover:shadow-glow"
              >
                Sign in to GRUS
                <ArrowRight className="h-4 w-4" />
              </Link>
              <a
                href="#what"
                className="inline-flex items-center gap-2 rounded-xl border border-ink-900/10 bg-white/60 px-6 py-3 text-sm font-semibold text-ink-700 transition hover:bg-white"
              >
                See what it does
              </a>
            </div>
            <div className="mt-10 grid max-w-md grid-cols-3 gap-4">
              <Stat value="<10s" label="to a first brief" />
              <Stat value="15+" label="validated clinical scores" />
              <Stat value="5" label="specialist agents" />
            </div>
          </Reveal>

          <Reveal delay={0.1}>
            <Mascot className="h-[380px] sm:h-[460px]" />
          </Reveal>
        </div>
      </section>

      {/* Slider */}
      <section className="mx-auto max-w-7xl px-4 pt-20 sm:px-6">
        <Reveal>
          <HeroSlider slides={SLIDES} />
        </Reveal>
      </section>

      {/* What GRUS does / 3 rules */}
      <section id="what" className="mx-auto max-w-7xl px-4 pt-24 sm:px-6">
        <Reveal>
          <div className="mx-auto max-w-2xl text-center">
            <p className="font-mono text-xs font-semibold uppercase tracking-widest text-brand-deep">
              What GRUS does
            </p>
            <h2 className="mt-2 text-3xl font-extrabold text-ink-900 sm:text-4xl">
              Three rules GRUS never breaks
            </h2>
            <p className="mt-3 text-lg text-ink-700">
              A fast brief is only useful if a clinician can trust it. GRUS is built around three
              non-negotiable principles.
            </p>
          </div>
        </Reveal>
        <div className="mt-12 grid gap-6 sm:grid-cols-3">
          {RULES.map((r, i) => (
            <Reveal key={r.title} delay={i * 0.08}>
              <div className="h-full rounded-3xl glass p-6 shadow-soft transition hover:shadow-lift">
                <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-2xl bg-brand-mid/10 text-brand-deep">
                  <r.icon className="h-5 w-5" />
                </div>
                <h3 className="text-lg font-bold text-ink-900">{r.title}</h3>
                <p className="mt-2 text-base leading-relaxed text-ink-700">{r.body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* Five agents */}
      <section id="agents" className="mx-auto max-w-7xl px-4 pt-24 sm:px-6">
        <Reveal>
          <div className="mx-auto max-w-2xl text-center">
            <p className="font-mono text-xs font-semibold uppercase tracking-widest text-brand-deep">
              How it works
            </p>
            <h2 className="mt-2 text-3xl font-extrabold text-ink-900 sm:text-4xl">
              Five specialist agents, one clear brief
            </h2>
            <p className="mt-3 text-lg text-ink-700">
              Each agent has one job. Together they turn a raw chart into something a clinician can
              act on at a glance.
            </p>
          </div>
        </Reveal>
        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-5">
          {AGENTS.map((a, i) => (
            <Reveal key={a.name} delay={i * 0.06}>
              <div className="h-full rounded-2xl glass p-5 shadow-soft transition hover:shadow-lift">
                <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-xl bg-brand-mid/10 text-brand-deep">
                  <a.icon className="h-5 w-5" />
                </div>
                <h3 className="text-sm font-bold text-ink-900">{a.name}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-ink-700">{a.body}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* Final CTA */}
      <section className="mx-auto max-w-7xl px-4 pb-24 pt-24 sm:px-6">
        <Reveal>
          <div className="relative overflow-hidden rounded-3xl brand-gradient px-8 py-14 text-center shadow-lift sm:px-16">
            <div
              className="absolute inset-0 opacity-[0.14]"
              style={{
                backgroundImage:
                  "radial-gradient(circle at 15% 25%, white 2px, transparent 2px), radial-gradient(circle at 80% 65%, white 2px, transparent 2px)",
                backgroundSize: "60px 60px, 90px 90px",
              }}
            />
            <div className="relative z-10">
              <h2 className="text-2xl font-extrabold text-white sm:text-3xl">
                See the chart. Trust the brief. Treat the patient.
              </h2>
              <p className="mx-auto mt-3 max-w-xl text-sm text-white/85 sm:text-base">
                Sign in with the demo hospital access code to explore GRUS on a live patient board.
              </p>
              <Link
                href="/login"
                className="mt-7 inline-flex items-center gap-2 rounded-xl bg-white px-6 py-3 text-sm font-semibold text-brand-deep shadow-soft transition hover:shadow-glow"
              >
                Sign in to GRUS
                <ArrowRight className="h-4 w-4" />
              </Link>
            </div>
          </div>
        </Reveal>
      </section>

      <footer className="border-t border-ink-900/5 px-4 py-8 text-center text-xs text-ink-400 sm:px-6">
        <p className="flex items-center justify-center gap-1.5">
          <GrusLogo size={18} withWordmark={false} />
          GRUS — built for the AWS "Agents for Humans" hackathon. Decision support, not diagnosis.
        </p>
      </footer>
    </main>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div>
      <p className="text-2xl font-extrabold brand-text">{value}</p>
      <p className="mt-0.5 text-xs leading-tight text-ink-500">{label}</p>
    </div>
  );
}
