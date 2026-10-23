"use client";

/**
 * Patient vitals dashboard — presentation only.
 *
 * Takes the same VitalsResponse the page already fetches and lays it out
 * as a clinical dashboard: four headline tiles, a heart-rate trend with
 * the latest readings, a shock-index gauge and a trend bar chart. No new
 * data and no new API calls. When a value isn't in the record the tile
 * says so instead of showing a blank or a zero.
 */

import { useMemo, useState } from "react";
import { Activity, Brain, Droplets, Gauge, Heart, Info, Percent, Scale, Thermometer, Wind } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { DerivedSeries, VitalSeries, VitalsResponse } from "@/lib/types";

// ---------------------------------------------------------------- helpers

const find = (v: VitalsResponse | null, re: RegExp) =>
  v?.series.find((s) => re.test(`${s.label} ${s.code}`) && s.points.length > 0);

const latestOf = (s?: VitalSeries | DerivedSeries) => (s && s.points.length ? s.points[s.points.length - 1] : undefined);

function fmt(n: number | undefined, digits = 0) {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  if (digits === 0) return String(Math.round(n));
  return Number.isInteger(n) ? String(n) : n.toFixed(digits);
}

function ago(h?: number) {
  if (h === undefined || h === null) return "Latest reading";
  if (h < 1) return `Latest · ${Math.max(1, Math.round(h * 60))} min ago`;
  if (h < 48) return `Latest · ${h.toFixed(h < 10 ? 1 : 0)} h ago`;
  return `Latest · day ${Math.floor(h / 24) + 1}`;
}

function iconFor(text: string): LucideIcon {
  const t = text.toLowerCase();
  if (t.includes("heart") || t.includes("pulse")) return Heart;
  if (t.includes("resp")) return Wind;
  if (t.includes("temp")) return Thermometer;
  if (t.includes("o2") || t.includes("saturation") || t.includes("spo2")) return Percent;
  if (t.includes("pressure") || t.includes("bp")) return Droplets;
  if (t.includes("gcs") || t.includes("glasgow")) return Brain;
  if (t.includes("weight")) return Scale;
  if (t.includes("shock") || t.includes("index")) return Gauge;
  return Activity;
}

type Tone = "ok" | "high" | "low" | "none";

function toneFor(value: number | undefined, range?: [number, number] | null): Tone {
  if (value === undefined) return "none";
  if (!range) return "ok";
  if (value > range[1]) return "high";
  if (value < range[0]) return "low";
  return "ok";
}

const NOTE: Record<Tone, string> = {
  ok: "bg-sev-okBg text-[#0f7a55]",
  high: "bg-sev-criticalBg text-sev-critical",
  low: "bg-sev-warningBg text-[#a16207]",
  none: "bg-ink-900/[0.04] text-ink-500",
};

// Smooth path through points (Catmull-Rom → cubic Bézier).
function smoothPath(pts: [number, number][]) {
  if (pts.length < 2) return "";
  let d = `M ${pts[0][0]},${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] || p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C ${c1x},${c1y} ${c2x},${c2y} ${p2[0]},${p2[1]}`;
  }
  return d;
}

function IconChip({ icon: Icon, tone = "brand" }: { icon: LucideIcon; tone?: "brand" | "soft" }) {
  return (
    <span
      className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border ${
        tone === "brand" ? "border-brand-mid/15 bg-leaf-soft text-brand-deep" : "border-ink-900/10 bg-white text-ink-700"
      }`}
    >
      <Icon className="h-[18px] w-[18px]" />
    </span>
  );
}

// ---------------------------------------------------------------- KPI tile

function KpiTile({
  icon,
  title,
  value,
  unit,
  sub,
  tone,
  note,
}: {
  icon: LucideIcon;
  title: string;
  value: string;
  unit?: string | null;
  sub: string;
  tone: Tone;
  note: string;
}) {
  return (
    <div className="card flex flex-col p-5">
      <div className="flex items-start gap-3">
        <IconChip icon={icon} />
        <div className="min-w-0">
          <p className="text-[15px] font-bold text-ink-900">{title}</p>
          <p className="text-xs text-ink-400">{sub}</p>
        </div>
      </div>
      <p className="mt-5 flex items-baseline gap-1.5">
        <span className={`text-[2.6rem] font-extrabold leading-none tracking-tight ${tone === "none" ? "text-ink-400" : "text-ink-900"}`}>
          {value}
        </span>
        {unit && tone !== "none" && <span className="text-base font-medium text-ink-400">{unit}</span>}
      </p>
      <div className={`mt-4 flex items-start gap-2 rounded-xl px-3 py-2.5 text-[13px] leading-snug ${NOTE[tone]}`}>
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>{note}</span>
      </div>
    </div>
  );
}

function rangeNote(tone: Tone, range?: [number, number] | null, unit?: string | null) {
  const r = range ? `${range[0]}–${range[1]}${unit ? ` ${unit}` : ""}` : "";
  if (tone === "none") return "Not in the record for this window. Ask at the bedside.";
  if (tone === "high") return `Above the normal range${r ? ` (${r})` : ""}.`;
  if (tone === "low") return `Below the normal range${r ? ` (${r})` : ""}.`;
  return range ? `Within the normal range (${r}).` : "No reference range on file for this reading.";
}

// ---------------------------------------------------------------- trend card

function TrendCard({ series, others }: { series?: VitalSeries; others: VitalSeries[] }) {
  const W = 320;
  const H = 120;
  const pts = series?.points || [];
  const last = latestOf(series);

  const path = useMemo(() => {
    if (pts.length < 2) return null;
    const xs = pts.map((p) => p.hours);
    const ys = pts.map((p) => p.value);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs, minX + 1);
    const minY = Math.min(...ys) - 4;
    const maxY = Math.max(...ys) + 4;
    const sc: [number, number][] = pts.map((p) => [
      4 + ((p.hours - minX) / (maxX - minX || 1)) * (W - 8),
      H - 8 - ((p.value - minY) / (maxY - minY || 1)) * (H - 16),
    ]);
    const line = smoothPath(sc);
    return { line, area: `${line} L ${sc[sc.length - 1][0]},${H} L ${sc[0][0]},${H} Z` };
  }, [pts]);

  return (
    <div className="card flex h-full flex-col p-5">
      <div className="flex items-start gap-3">
        <IconChip icon={Heart} />
        <div>
          <p className="text-[15px] font-bold text-ink-900">Heart rate</p>
          <p className="text-xs text-ink-400">Trend this admission</p>
        </div>
      </div>

      {path ? (
        <svg viewBox={`0 0 ${W} ${H}`} className="mt-4 h-28 w-full" preserveAspectRatio="none">
          <defs>
            <linearGradient id="hr-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#3D6BE6" stopOpacity="0.28" />
              <stop offset="100%" stopColor="#3D6BE6" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={path.area} fill="url(#hr-fill)" />
          <path d={path.line} fill="none" stroke="#3D6BE6" strokeWidth={2.5} strokeLinecap="round" />
        </svg>
      ) : (
        <p className="mt-4 rounded-xl bg-ink-900/[0.04] px-3 py-6 text-center text-xs text-ink-500">
          Not enough heart-rate readings to draw a trend.
        </p>
      )}

      <p className="mt-3 text-xs text-ink-400">Latest heart rate</p>
      <p className="flex items-baseline gap-1.5">
        <span className="text-[2.2rem] font-extrabold leading-tight text-ink-900">{fmt(last?.value)}</span>
        {last && <span className="text-sm text-ink-400">bpm</span>}
      </p>

      {others.length > 0 && (
        <div className="mt-3 divide-y divide-ink-900/[0.06] border-t border-ink-900/[0.06]">
          {others.slice(0, 7).map((s) => {
            const l = latestOf(s);
            return (
              <div key={s.code} className="flex items-center justify-between gap-3 py-2 text-[13px]">
                <span className="truncate text-ink-500">{s.label}</span>
                <span className="shrink-0 font-semibold text-ink-900">
                  {fmt(l?.value, 1)} <span className="font-normal text-ink-400">{s.unit || ""}</span>
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- gauge

function ShockGauge({ series }: { series?: DerivedSeries }) {
  const last = latestOf(series);
  const concern = series?.thresholds?.concern ?? 0.9;
  const severe = series?.thresholds?.severe ?? 1.3;
  const max = severe * 1.45;
  const v = last?.value;

  // Half donut: angle 180° (left) → 0° (right).
  const R = 80;
  const cx = 100;
  const cy = 100;
  const arc = (from: number, to: number) => {
    const a0 = Math.PI * (1 - from / max);
    const a1 = Math.PI * (1 - to / max);
    const x0 = cx + R * Math.cos(a0);
    const y0 = cy - R * Math.sin(a0);
    const x1 = cx + R * Math.cos(a1);
    const y1 = cy - R * Math.sin(a1);
    return `M ${x0},${y0} A ${R},${R} 0 0 1 ${x1},${y1}`;
  };
  const zone = v === undefined ? "none" : v >= severe ? "severe" : v >= concern ? "concern" : "normal";
  const pos = v === undefined ? null : Math.min(v, max);
  const marker =
    pos === null
      ? null
      : { x: cx + R * Math.cos(Math.PI * (1 - pos / max)), y: cy - R * Math.sin(Math.PI * (1 - pos / max)) };

  const pill = {
    normal: "bg-sev-ok text-white",
    concern: "bg-sev-warning text-white",
    severe: "bg-sev-critical text-white",
    none: "bg-ink-900/10 text-ink-500",
  }[zone];

  return (
    <div className="card flex h-full flex-col p-5">
      <div className="flex items-start gap-3">
        <IconChip icon={Gauge} />
        <div>
          <p className="text-[15px] font-bold text-ink-900">Shock index</p>
          <p className="text-xs text-ink-400">Heart rate ÷ systolic BP</p>
        </div>
      </div>
      <div className="relative mx-auto mt-4 w-full max-w-[240px]">
        <svg viewBox="0 0 200 112" className="w-full">
          <path d={arc(0, concern)} stroke="#BBF0D9" strokeWidth={22} fill="none" />
          <path d={arc(concern, severe)} stroke="#FCD9A0" strokeWidth={22} fill="none" />
          <path d={arc(severe, max)} stroke="#FBB3C0" strokeWidth={22} fill="none" />
          {pos !== null && <path d={arc(0, pos)} stroke="#3D6BE6" strokeWidth={22} fill="none" opacity={0.9} />}
          {marker && <circle cx={marker.x} cy={marker.y} r={7} fill="#13195C" stroke="#fff" strokeWidth={3} />}
        </svg>
        <div className="absolute inset-x-0 bottom-0 text-center">
          <p className="text-[11px] text-ink-400">Latest</p>
          <p className="text-[2.1rem] font-extrabold leading-none text-ink-900">{v === undefined ? "—" : v.toFixed(2)}</p>
        </div>
      </div>
      <div className="mt-3 flex justify-center">
        <span className={`rounded-full px-3 py-1 text-xs font-bold capitalize ${pill}`}>
          {zone === "none" ? "Not enough data" : zone}
        </span>
      </div>
      <div className="mt-auto flex flex-wrap justify-center gap-x-3 gap-y-1 pt-4 text-[11px] text-ink-500">
        <span className="flex items-center gap-1"><i className="h-2 w-2 rounded-full bg-[#5DD3A4]" />Normal &lt;{concern}</span>
        <span className="flex items-center gap-1"><i className="h-2 w-2 rounded-full bg-sev-warning" />Concern</span>
        <span className="flex items-center gap-1"><i className="h-2 w-2 rounded-full bg-sev-critical" />Severe ≥{severe}</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- bar chart

function TrendBars({ all }: { all: VitalSeries[] }) {
  const options = all.filter((s) => s.points.length > 1);
  const preferred = options.find((s) => /systolic/i.test(s.label)) || options[0];
  const [code, setCode] = useState<string | undefined>(preferred?.code);
  const s = options.find((o) => o.code === code) || preferred;

  if (!s) {
    return (
      <div className="card flex h-full flex-col p-5">
        <div className="flex items-start gap-3">
          <IconChip icon={Activity} />
          <div>
            <p className="text-[15px] font-bold text-ink-900">Vital trend</p>
            <p className="text-xs text-ink-400">Recent readings</p>
          </div>
        </div>
        <p className="mt-6 rounded-xl bg-ink-900/[0.04] px-3 py-10 text-center text-xs text-ink-500">
          Not enough readings to chart.
        </p>
      </div>
    );
  }

  const pts = s.points.slice(-14);
  const vals = pts.map((p) => p.value);
  const top = Math.max(...vals);
  const base = Math.max(0, Math.min(...vals) - (top - Math.min(...vals)) * 0.6 - 1);
  const ticks = [top, base + (top - base) * 0.66, base + (top - base) * 0.33, base].map((t) => Math.round(t));
  const range = s.normal_range;

  return (
    <div className="card flex h-full flex-col p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <IconChip icon={iconFor(`${s.label} ${s.code}`)} />
          <div>
            <p className="text-[15px] font-bold text-ink-900">Vital trend</p>
            <p className="text-xs text-ink-400">Last {pts.length} readings{s.unit ? ` · ${s.unit}` : ""}</p>
          </div>
        </div>
        <select
          value={s.code}
          onChange={(e) => setCode(e.target.value)}
          className="max-w-[48%] truncate rounded-full border border-ink-900/10 bg-white px-3 py-1.5 text-xs font-medium text-ink-700 outline-none focus:border-brand-mid"
        >
          {options.map((o) => (
            <option key={o.code} value={o.code}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <div className="mt-5 flex flex-1 gap-3">
        <div className="flex flex-col justify-between pb-6 text-right text-[11px] text-ink-400">
          {ticks.map((t, i) => (
            <span key={i}>{t}</span>
          ))}
        </div>
        <div className="flex-1">
          <div className="flex h-44 items-end gap-2 border-b border-ink-900/10">
            {pts.map((p, i) => {
              const h = ((p.value - base) / (top - base || 1)) * 100;
              const isLast = i === pts.length - 1;
              const out = range && (p.value > range[1] || p.value < range[0]);
              return (
                <div key={i} className="group relative flex h-full flex-1 items-end">
                  <div
                    className={`w-full rounded-t-lg transition ${
                      isLast ? "bg-brand-mid" : out ? "bg-[#FBC4CE]" : "bg-[#E4E7F2] group-hover:bg-[#cdd3ea]"
                    }`}
                    style={{ height: `${Math.max(4, h)}%` }}
                  />
                  <span className="pointer-events-none absolute -top-6 left-1/2 hidden -translate-x-1/2 rounded bg-brand-ink px-1.5 py-0.5 text-[10px] text-white group-hover:block">
                    {p.value}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="mt-1.5 flex gap-2 text-[10px] text-ink-400">
            {pts.map((p, i) => (
              <span key={i} className="flex-1 text-center">
                {i % 2 === 0 || i === pts.length - 1 ? `${Math.round(p.hours)}h` : ""}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-500">
        <span className="flex items-center gap-1"><i className="h-2 w-2 rounded-sm bg-brand-mid" />Latest</span>
        {range && <span className="flex items-center gap-1"><i className="h-2 w-2 rounded-sm bg-[#FBC4CE]" />Outside {range[0]}–{range[1]}</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- small reading cards

function ReadingCard({ s }: { s: VitalSeries }) {
  const l = latestOf(s);
  const Icon = iconFor(`${s.label} ${s.code}`);
  const tone = toneFor(l?.value, s.normal_range);
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-ink-900/[0.07] bg-[#FAFBFE] p-3.5">
      <IconChip icon={Icon} tone="soft" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] font-semibold text-ink-900">{s.label}</p>
        <p className="text-[11px] text-ink-400">{ago(l?.age_hours)}</p>
      </div>
      <p className={`shrink-0 text-right text-[15px] font-bold ${tone === "high" ? "text-sev-critical" : tone === "low" ? "text-[#a16207]" : "text-ink-900"}`}>
        {fmt(l?.value, 1)} <span className="text-[11px] font-medium text-ink-400">{s.unit || ""}</span>
      </p>
    </div>
  );
}

// ---------------------------------------------------------------- main

export function PatientDashboard({ vitals }: { vitals: VitalsResponse | null }) {
  if (!vitals) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-52 animate-pulse rounded-[1.25rem] bg-white/80" />
        ))}
      </div>
    );
  }

  const hr = find(vitals, /heart rate|\bhr\b|pulse/i);
  const sys = find(vitals, /systolic/i);
  const dia = find(vitals, /diastolic/i);
  const spo2 = find(vitals, /o2 sat|spo2|saturation|oxygen/i);
  const rr = find(vitals, /resp/i);
  const shock = vitals.derived.find((d) => /shock/i.test(`${d.label} ${d.code}`));

  const hrL = latestOf(hr);
  const sysL = latestOf(sys);
  const diaL = latestOf(dia);
  const spL = latestOf(spo2);
  const rrL = latestOf(rr);

  const hrTone = toneFor(hrL?.value, hr?.normal_range);
  const bpTone = toneFor(sysL?.value, sys?.normal_range);
  const spTone = toneFor(spL?.value, spo2?.normal_range);
  const rrTone = toneFor(rrL?.value, rr?.normal_range);

  const used = new Set([hr, sys, dia, spo2, rr].filter(Boolean).map((s) => s!.code));
  const others = vitals.series.filter((s) => s.points.length > 0 && !used.has(s.code));
  const listForTrend = vitals.series.filter((s) => s.points.length > 0 && s.code !== hr?.code);

  return (
    <div className="space-y-4">
      {/* Row 1 — four headline tiles */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiTile
          icon={Heart}
          title="Heart rate"
          sub={ago(hrL?.age_hours)}
          value={fmt(hrL?.value)}
          unit="bpm"
          tone={hrTone}
          note={rangeNote(hrTone, hr?.normal_range, "bpm")}
        />
        <KpiTile
          icon={Droplets}
          title="Blood pressure"
          sub={ago(sysL?.age_hours)}
          value={sysL ? `${fmt(sysL.value)}${diaL ? `/${fmt(diaL.value)}` : ""}` : "—"}
          unit="mmHg"
          tone={bpTone}
          note={rangeNote(bpTone, sys?.normal_range, "mmHg systolic")}
        />
        <KpiTile
          icon={Percent}
          title="Oxygen saturation"
          sub={ago(spL?.age_hours)}
          value={fmt(spL?.value)}
          unit="%"
          tone={spTone}
          note={rangeNote(spTone, spo2?.normal_range, "%")}
        />
        <KpiTile
          icon={Wind}
          title="Respiratory rate"
          sub={ago(rrL?.age_hours)}
          value={fmt(rrL?.value)}
          unit="/min"
          tone={rrTone}
          note={rangeNote(rrTone, rr?.normal_range, "/min")}
        />
      </div>

      {/* Row 2 — trend + gauge + bars, then other readings */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <div className="lg:row-span-2">
          <TrendCard series={hr} others={listForTrend} />
        </div>
        <div>
          <ShockGauge series={shock} />
        </div>
        <div className="lg:col-span-2">
          <TrendBars all={vitals.series} />
        </div>
        <div className="card p-5 lg:col-span-3">
          <div className="mb-3 flex items-center justify-between">
            <div>
              <p className="text-[15px] font-bold text-ink-900">Other readings</p>
              <p className="text-xs text-ink-400">Latest value of everything else on the chart</p>
            </div>
          </div>
          {others.length === 0 ? (
            <p className="rounded-xl bg-ink-900/[0.04] px-3 py-4 text-center text-xs text-ink-500">
              No other readings recorded in this window.
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
              {others.map((s) => (
                <ReadingCard key={s.code} s={s} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
