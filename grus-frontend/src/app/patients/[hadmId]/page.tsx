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
    <div className="mx-auto max-w-[1400px] px-4 py-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-xs text-ink-400">
            <Link href="/patients" className="hover:text-ink-500">
              Patients
            </Link>
            <span>/</span>
            <span className="font-mono">hadm #{hadmId}</span>
          </div>
          <h1 className="mt-1 text-lg font-semibold text-ink-900">
            {headlineFromQuery || "Patient brief"}
          </h1>
        </div>
        <div className="flex items-center gap-2">
          {subjectId && (
            <button
              onClick={() => router.push(`/patients/history/${subjectId}`)}
              className="rounded border border-ink-900/10 px-3 py-1.5 text-xs text-ink-500 hover:text-ink-900 hover:border-ink-900/15"
            >
              View history
            </button>
          )}
        </div>
      </div>

      <div className="mb-4">
        <TimelineSlider
          maxHours={MAX_TIMELINE_HOURS}
          committedHours={committedHours}
          onDraftChange={(h) => fetchAlerts(h)}
          onCommit={(h) => setCommittedHours(h)}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_340px]">
        {/* Main column */}
        <div className="space-y-4">
          {briefLoading && <AgentsProgress agentsRun={brief && !brief.degraded ? brief.agents_run : undefined} />}

          {!briefLoading && alerts && (
            <div className="rounded-lg glass px-4 py-3">
              <div className="mb-2 flex items-center justify-between">
                <p className="font-mono text-[11px] uppercase tracking-wide text-ink-500">
                  Rule-engine alerts (instant)
                </p>
                <CountsRow counts={alerts.counts} />
              </div>
            </div>
          )}

          {briefError && !isDegraded && (
            <div className="rounded-lg border border-sev-critical/40 bg-sev-criticalBg px-4 py-3 text-sm text-sev-critical">
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
              <div className="rounded-lg border border-sev-warning/40 bg-sev-warningBg px-4 py-3 text-sm text-sev-warning">
                Agent graph unavailable — showing rule engine output only. {brief.message}
              </div>
              <AlertsList alerts={brief.alerts} />
            </div>
          )}

          {!briefLoading && parsedBrief && brief && !brief.degraded && (
            <div className="space-y-4">
              <TrustStrip trust={brief.trust} generationMs={brief.generation_ms} cached={brief.cached} />
              <div className="rounded-lg glass px-5 py-5">
                <BriefRenderer parsed={parsedBrief} />
              </div>
              {brief.disclaimer && <p className="text-center text-[11px] text-ink-400">{brief.disclaimer}</p>}
            </div>
          )}
        </div>

        {/* Side column */}
        <div className="space-y-4">
          <RiskPanel risk={risk} loading={riskLoading} />

          {hadmId ? <ScoresPanel hadmId={hadmId} asOfHours={committedHours} /> : null}

          <div>
            <p className="mb-2 font-mono text-[11px] uppercase tracking-wide text-ink-500">Vitals</p>
            {!vitals && <div className="h-24 animate-pulse rounded-lg bg-white/70" />}
            {vitals && vitals.series.length === 0 && (
              <p className="rounded-lg border border-sev-unknown/40 bg-sev-unknownBg px-3 py-3 text-xs text-sev-unknown">
                {vitals.sampling_note || "No vitals recorded for this window."}
              </p>
            )}
            <div className="space-y-2">
              {vitals?.series.map((s) => (
                <VitalsMiniChart key={s.code} series={s} />
              ))}
              {vitals?.derived.map((s) => (
                <DerivedMiniChart key={s.code} series={s} />
              ))}
            </div>
            {vitals?.sampling_note && vitals.series.length > 0 && (
              <p className="mt-2 text-[11px] text-ink-400">{vitals.sampling_note}</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function CountsRow({ counts }: { counts: { critical: number; warning: number; unknown: number; info: number } }) {
  return (
    <div className="flex items-center gap-3 font-mono text-[11px]">
      <span className="text-sev-critical">{counts.critical} critical</span>
      <span className="text-sev-warning">{counts.warning} warning</span>
      <span className="text-sev-unknown">{counts.unknown} unknown</span>
      <span className="text-sev-info">{counts.info} info</span>
    </div>
  );
}

function summaryLine(briefText: string): string | null {
  const match = briefText.match(/\*\*SUMMARY\*\*\s*\n(.+)/i);
  return match ? match[1].trim() : null;
}
