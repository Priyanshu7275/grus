# GRUS Frontend

Next.js 14 + TypeScript + Tailwind. Light, glassmorphic clinical UI for the GRUS emergency-decision-support agent, built against the real `grus_api.py` contract (github.com/Priyanshu7275/grus).

It runs fully standalone — with no backend, no AWS credentials, nothing — because every API call has a realistic demo-data fallback. Point it at a live backend later and nothing in the UI needs to change.

## Run it

```
npm install
npm run dev
```

Open http://localhost:3000. Sign in with the demo credentials shown on the login screen (hospital code `GRUS-ED-4471`, `clinician@hospital-x.org` / `grus-demo`, OTP `424242` — auth is cosmetic, see below).

If your teammate's backend is running locally, it's used automatically — no build changes needed:

```
uvicorn grus_api:app --reload --port 8000
```

`api.ts` tries `NEXT_PUBLIC_API_BASE` (defaults to `http://localhost:8000`) first with a 5s timeout, and only falls back to demo data on failure. `cp .env.local.example .env.local` if the backend runs somewhere else.

`npm run build` runs clean with zero type errors, so it's ready for `vercel deploy` / `next build && next start` as-is.

## What's here

```
src/
  app/
    page.tsx                              public landing page — hero, slider, mascot, "how it works"
    login/page.tsx                        3-step gated login (Sammy's pattern, cosmetic — see Auth below)
    patients/page.tsx                     cohort board — GET /patients, filters, search, skeleton
    patients/[hadmId]/page.tsx            the hero brief screen (the main event)
    patients/history/[subjectId]/page.tsx GET /patients/{subject_id}/history
    governance/page.tsx                   GET /governance — shipped + rejected models
  components/
    Mascot.tsx, HeroSlider.tsx, GrusLogo.tsx    landing-page pieces
    ChatWidget.tsx                        floating chat bubble → panel ("Ask AWS"-style)
    SourceDrawer.tsx                      slide-in panel + <Citation> — every [table#id] opens this
    BriefRenderer.tsx                     parses the markdown /brief text into styled sections
    AgentsProgress.tsx                    "5 agents running" progress while /brief is generating
    RiskPanel.tsx                         GET /risk, shown separately from rule alerts on purpose
    ScoresPanel.tsx                       the 15 validated clinical scores — suggested, computed, fill-in-the-blank
    VitalsChart.tsx                       hand-rolled SVG line charts (no chart library needed)
    TimelineSlider.tsx                    as_of_hours point-in-time control
    TrustStrip.tsx                        "100% traceable · N sourced" strip
    AlertsList.tsx                        GET /alerts — instant fallback + degraded-brief rendering
    PatientCard.tsx / SeverityBadge.tsx   smaller shared pieces
  lib/
    api.ts          typed client for every endpoint in grus_api.py, each with a demo-data fallback
    demo-data.ts     realistic fixture data — 6 hand-authored "hero" patients + generated filler
    scoreSpecs.ts    the real 15-score input spec, extracted from grus_scores.py (see below)
    types.ts         response shapes (kept permissive — the contract has changed a few times)
    briefParser.ts   parses **HEADING** / [severity] / -> action / [table#id] out of the brief text
    format.ts        hours-since-arrival formatting, severity/risk color mapping, source normalizing
    auth.ts, activePatientContext.tsx     cosmetic session flag + "which patient is the chat talking about"
```

## Design decisions worth knowing about

- **Demo-data fallback, everywhere.** `api.ts` wraps every call: try the live backend (5s timeout) → on any failure, resolve with `demo-data.ts` instead of throwing. The UI never sees the difference — no red error banners, no empty screens. `demo-data.ts` isn't placeholder text: patient headlines, lab values, vital codes and clinical-score inputs are all grounded in the real backend's actual schema (verified against the cloned repo), not invented. This means the frontend can be built, demoed, and screen-recorded before AWS credentials exist, and swapping in the real backend later is a zero-code-change event.
- **`/brief` returns markdown, not JSON.** `briefParser.ts` splits it into sections by `**HEADING**`, groups `[severity] TITLE` / indented detail / `-> action` into blocks, and treats `RED FLAGS`, `CRITICAL UNKNOWNS`, and anything with "resolved" in the heading with distinct styling. Everything else (`SUMMARY`, `CURRENT STATE`, `HISTORY`) renders as plain paragraphs. Any `[table#id]` anywhere in the text becomes a clickable citation automatically.
- **No merged risk score.** `/risk` (ML models) is rendered in its own card next to the rule-engine alerts, never combined into one number — the disagreement between rules and models is meant to be visible.
- **Clinical scores are honest about what's missing.** `ScoresPanel.tsx` calls `GET /scores` for suggestions, `GET /scores/{name}` to compute, and `POST /scores/{name}` to submit clinician answers for whatever the record couldn't supply. One real gap in the current backend: `components.missing` in the score result never includes the internal `key` the POST endpoint needs (see the comment at the top of `scoreSpecs.ts`) — that file mirrors the real `grus_scores.py` label→key mapping as a client-side fallback so the feature works today regardless, and prefers a live `key` automatically if the backend adds one.
- **`unknown` is always grey**, never green, in risk levels, severities, and the risk panel's `available: false` state.
- **The "5 agents running" progress is an honest approximation, not real streaming** — the backend is one 5–10s call, not a progress stream. `AgentsProgress.tsx` advances the five known agent names on a timer, then reconciles against the real `agents_run` array once the response lands.
- **Timeline slider**: dragging calls `/alerts` (instant) on every move; releasing calls `/brief`, `/risk`, `/vitals` (the slow ones) once.
- **Chat widget**: collapsed circular launcher bottom-right, expands to a panel with a branded header, a patient context chip, suggested starter questions from `/questions`, a message thread, and a disclaimer line. Anchored to whichever patient's brief page you last opened. Assistant replies render `abstained` claims in their own callout and `tools_called` in a collapsible "how did you find this."
- **Auth is cosmetic**, a close port of the team's Sammy project's 3-step login (hospital code → credentials → OTP). `lib/auth.ts` is a one-file seam — swap `verifyHospital`/`verifyCredentials`/`verifyOtp` for real Cognito calls when that lands, nothing else needs to change.
- **Theme**: white base + the sky-blue sampled directly from the mascot video's own background (`brand.sky/mid/deep` in `tailwind.config.ts`), glassmorphic cards (`.glass` in `globals.css`), Framer Motion throughout.
- **`subject_id` travels via query string**, not a second API call — patient links carry `?subject=...&headline=...` straight through from the cohort board.

## Known gaps / next steps

- Patient registration (`POST /admissions`) and the live drip-feed endpoints (`/admissions/{id}/labs|vitals|notes`) aren't wired into the UI yet. `api.ts` already has typed methods (with demo fallbacks) for all of them — a form + polling `stage` would be the fastest way to add it.
- `npm audit` flags Next 14.2.x's remaining CVEs, fixed only by the Next 15/16 major (breaking change). Left on 14 for build stability this close to the deadline.
- Real Cognito JWT isn't attached to requests yet — `api.ts`'s `request()` helper is the one place to add an `Authorization: Bearer` header once tokens exist.
- The demo-data fallback for clinical scores doesn't replicate each score's full point-banding arithmetic (15 different scoring algorithms is a lot to hand-encode client-side) — it honestly shows found vs. missing criteria and lets you submit answers, but won't show a numeric total/risk-band until the real backend is connected. Everything else (brief, alerts, risk, vitals, sources, history, governance, chat) is fully realistic in demo mode.
