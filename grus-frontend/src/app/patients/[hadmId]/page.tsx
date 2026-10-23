"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useSearchParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api, ApiError } from "@/lib/api";
import { AlertsResponse, BriefResult, RiskResponse, VitalsResponse } from "@/lib/types";
import { parseBrief } from "@/lib/briefParser";
import { useActivePatient } from "@/lib/activePatientContext";
import { BriefRenderer } from "@/components/BriefRenderer";
import { AlertsList } from "@/components/AlertsList";
import { AgentsProgress } from "@/components/AgentsProgress";
import { TrustStrip } from "@/components/TrustStrip";
import { TimelineSlider } from "@/components/TimelineSlider";
import { RiskPanel } from "@/components/RiskPanel";
import { ScoresPanel } from "@/components/ScoresPanel";
import { VitalsMiniChart, DerivedMiniChart } from "@/components/VitalsChart";
import { VoiceWidget } from "@/components/VoiceWidget";
import { PatientDashboard } from "@/components/PatientDashboard";
import { ArrowUpRight, FileText, History } from "lucide-react";

const MAX_TIMELINE_HOURS = 168;

export default function BriefPage() {

  const params = useParams<{ hadmId: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const hadmId = Number(params.hadmId);
  const subjectIdFromQuery = searchParams.get("subject");
  const subjectId = subjectIdFromQuery ? Number(subjectIdFromQuery) : null;
  const headlineFromQuery = searchParams.get("headline");

  const { setActivePatient } = useActivePatient();

  const [committedHours, setCommittedHours] = useState<number | null>(null);
  const [alerts, setAlerts] = useState<AlertsResponse | null>(null);
  const [alertsError, setAlertsError] = useState<string | null>(null);

  const [brief, setBrief] = useState<BriefResult | null>(null);
  const [briefLoading, setBriefLoading] = useState(true);
  const [briefError, setBriefError] = useState<string | null>(null);

  const [risk, setRisk] = useState<RiskResponse | null>(null);
  const [riskLoading, setRiskLoading] = useState(true);

  const [vitals, setVitals] = useState<VitalsResponse | null>(null);
  const [shortId, setShortId] = useState<string>(String(hadmId).slice(-4));

  // The number a doctor says to Alexa ("patient 5843").
  useEffect(() => {
    if (!hadmId) return;
    api
      .getShortId(hadmId)
      .then((r) => r.short_id && setShortId(r.short_id))
      .catch(() => {});
  }, [hadmId]);

  // Fast alerts fetch — used for instant feedback while dragging the slider.
  const fetchAlerts = useCallback(
    (hours: number | null) => {
      api
        .getAlerts(hadmId, hours !== null ? { as_of_hours: hours } : {})
        .then((r) => {
          setAlerts(r);
          setAlertsError(null);
        })
        .catch((e) => setAlertsError(e instanceof ApiError ? e.message : "Could not load alerts."));
    },
    [hadmId]
  );

  // Full pipeline — brief, risk, vitals — fetched whenever the committed
  // point-in-time changes (i.e. when the slider is released, not while dragging).
  useEffect(() => {
    if (!hadmId) return;
    let cancelled = false;
    setBriefLoading(true);
    setBriefError(null);
    setRiskLoading(true);

    fetchAlerts(committedHours);

    const briefParams = committedHours !== null ? { as_of_hours: committedHours } : {};
    api
      .getBrief(hadmId, briefParams)
      .then((r) => {
        if (!cancelled) setBrief(r);
      })
      .catch((e) => {
        if (!cancelled) setBriefError(e instanceof ApiError ? e.message : "Could not generate brief.");
      })
      .finally(() => {
        if (!cancelled) setBriefLoading(false);
      });

    api
      .getRisk(hadmId, briefParams)
      .then((r) => {
        if (!cancelled) setRisk(r);
      })
      .catch(() => {
        if (!cancelled) setRisk({ available: false, reason: "Risk endpoint unreachable." });
      })
      .finally(() => {
        if (!cancelled) setRiskLoading(false);
      });

    api
      .getVitals(hadmId, briefParams)
      .then((r) => {
        if (!cancelled) setVitals(r);
      })
      .catch(() => {
        if (!cancelled) setVitals(null);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hadmId, committedHours]);

  // Keep the chat widget anchored to this patient.
  useEffect(() => {
    setActivePatient({
      hadmId,
      subjectId,
      asOfHours: committedHours,
      headline: headlineFromQuery || (brief && !brief.degraded ? summaryLine(brief.brief) : null),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hadmId, subjectId, committedHours, brief]);

  const parsedBrief = brief && !brief.degraded ? parseBrief(brief.brief) : null;
  const isDegraded = brief && brief.degraded === true;

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-7 sm:px-6">
      {/* Header */}
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-xs text-ink-400">
            <Link href="/patients" className="transition hover:text-brand-deep">
              Patients
            </Link>
            <span>/</span>
            <span>Admission {hadmId}</span>
          </div>
          <div className="mt-3 flex items-center gap-4">
            <div
              className="flex h-14 w-14 shrink-0 flex-col items-center justify-center rounded-2xl bg-brand-ink text-white shadow-lift"
              title="Say this number to Alexa"
            >
              <span className="text-[9px] font-semibold uppercase tracking-wider text-white/60">ID</span>
              <span className="text-lg font-extrabold leading-none">{shortId}</span>
            </div>
            <div>
              <h1 className="text-2xl font-bold text-ink-900 sm:text-[1.75rem]">{headlineFromQuery || "Patient brief"}</h1>
              <p className="text-sm text-ink-500">
                Say <span className="font-semibold text-ink-700">&ldquo;patient {shortId}&rdquo;</span> to Alexa to ask about this patient
              </p>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {subjectId && (
            <button
              onClick={() => router.push(`/patients/history/${subjectId}`)}
              className="inline-flex items-center gap-2 rounded-full border border-ink-900/10 bg-white px-4 py-2.5 text-sm font-semibold text-ink-700 transition hover:border-ink-900/25"
            >
              <History className="h-4 w-4" />
              View history
            </button>
          )}
          <Link href={`/patients/${hadmId}/room`} className="pill-btn bg-brand-ink text-white shadow-soft">
            Room record
            <span className="pill-arrow bg-white text-brand-ink">
              <ArrowUpRight className="h-4 w-4" />
            </span>
          </Link>
        </div>
      </div>

      <div className="mb-5">
        <TimelineSlider
          maxHours={MAX_TIMELINE_HOURS}
          committedHours={committedHours}
          onDraftChange={(h) => fetchAlerts(h)}
          onCommit={(h) => setCommittedHours(h)}
        />
      </div>

      {/* Vitals dashboard */}
      <div className="mb-6">
        {vitals && vitals.series.length === 0 && vitals.derived.length === 0 ? (
          <p className="rounded-2xl border border-sev-unknown/40 bg-sev-unknownBg px-5 py-4 text-sm text-sev-unknown">
            {vitals.sampling_note || "No vitals recorded for this window."}
          </p>
        ) : (
          <PatientDashboard vitals={vitals} />
        )}
        {vitals?.sampling_note && vitals.series.length > 0 && (
          <p className="mt-2 text-[11px] text-ink-400">{vitals.sampling_note}</p>
        )}
      </div>

      {/* Brief + Talk to GRUS */}
      <div>
        <div className="space-y-4">
          {briefLoading && <AgentsProgress agentsRun={brief && !brief.degraded ? brief.agents_run : undefined} />}

          {briefError && !isDegraded && (
            <div className="rounded-2xl border border-sev-critical/40 bg-sev-criticalBg px-5 py-4 text-sm text-sev-critical">
              {briefError}
              {alerts && (
                <div className="mt-3">
                  <p className="mb-2 text-sm text-ink-700">
                    Falling back to rule-engine alerts — unformatted data beats a blank screen.
                  </p>
                  <AlertsList alerts={alerts.alerts} />
                </div>
              )}
            </div>
          )}

          {isDegraded && brief && brief.degraded && (
            <div className="space-y-3">
              <div className="rounded-2xl border border-sev-warning/40 bg-sev-warningBg px-5 py-4 text-sm text-sev-warning">
                Agent graph unavailable — showing rule engine output only. {brief.message}
              </div>
              <AlertsList alerts={brief.alerts} />
            </div>
          )}

          {!briefLoading && parsedBrief && brief && !brief.degraded && (
            <div className="card overflow-hidden">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-900/[0.06] px-6 py-4">
                <div className="flex items-center gap-3">
                  <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-brand-mid/15 bg-leaf-soft text-brand-deep">
                    <FileText className="h-[18px] w-[18px]" />
                  </span>
                  <div>
                    <p className="text-[15px] font-bold text-ink-900">Clinical brief</p>
                    <p className="text-xs text-ink-400">Every claim links to the chart row it came from</p>
                  </div>
                </div>
                {alerts && <CountsRow counts={alerts.counts} />}
              </div>
              <div className="px-6 pt-4">
                <TrustStrip trust={brief.trust} generationMs={brief.generation_ms} cached={brief.cached} />
              </div>
              <div className="px-6 py-6">
                <BriefRenderer parsed={parsedBrief} />
              </div>
              {brief.disclaimer && (
                <p className="border-t border-ink-900/[0.06] px-6 py-3 text-[11px] text-ink-400">{brief.disclaimer}</p>
              )}
            </div>
          )}

          {!briefLoading && !parsedBrief && !briefError && !isDegraded && alerts && (
            <div className="card px-5 py-3.5">
              <div className="flex items-center justify-between">
                <p className="section-label">Rule-engine alerts · instant</p>
                <CountsRow counts={alerts.counts} />
              </div>
            </div>
          )}
        </div>

             </div>

      <VoiceWidget patientShortId={shortId} />

      {/* Predictions + scores */}
      <div className="mt-5 grid grid-cols-1 gap-5 xl:grid-cols-2">
        <RiskPanel risk={risk} loading={riskLoading} />
        {hadmId ? <ScoresPanel hadmId={hadmId} asOfHours={committedHours} /> : null}
      </div>

      {/* All vital-sign charts */}
      {vitals && (vitals.series.length > 0 || vitals.derived.length > 0) && (
        <div className="mt-8">
          <div className="mb-3">
            <p className="text-lg font-bold text-ink-900">All vital signs</p>
            <p className="text-xs text-ink-400">Every series on the chart, with the normal range shaded</p>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {vitals.derived.map((s) => (
              <DerivedMiniChart key={s.code} series={s} />
            ))}
            {vitals.series.map((s) => (
              <VitalsMiniChart key={s.code} series={s} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function CountsRow({ counts }: { counts: { critical: number; warning: number; unknown: number; info: number } }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-semibold">
      <span className="rounded-full bg-sev-criticalBg px-2.5 py-1 text-sev-critical">{counts.critical} critical</span>
      <span className="rounded-full bg-sev-warningBg px-2.5 py-1 text-[#a16207]">{counts.warning} warning</span>
      <span className="rounded-full bg-sev-unknownBg px-2.5 py-1 text-sev-unknown">{counts.unknown} unknown</span>
      <span className="rounded-full bg-sev-infoBg px-2.5 py-1 text-sev-info">{counts.info} info</span>
    </div>
  );
}

function summaryLine(briefText: string): string | null {
  const match = briefText.match(/\*\*SUMMARY\*\*\s*\n(.+)/i);
  return match ? match[1].trim() : null;
}
