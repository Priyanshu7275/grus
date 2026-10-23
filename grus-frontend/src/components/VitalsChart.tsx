import { DerivedSeries, VitalSeries } from "@/lib/types";
import { isStale } from "@/lib/format";
import { Activity, Heart, Thermometer, Wind, Droplets, Gauge, Brain, Scale, Percent } from "lucide-react";
import type { LucideIcon } from "lucide-react";

const WIDTH = 320;
const HEIGHT = 92;
const PAD_X = 6;
const PAD_Y = 12;

function scale(points: { hours: number; value: number }[]) {
  if (!points.length) return null;
  const xs = points.map((p) => p.hours);
  const ys = points.map((p) => p.value);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs, minX + 1);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys, minY + 1);
  const x = (v: number) => PAD_X + ((v - minX) / (maxX - minX || 1)) * (WIDTH - PAD_X * 2);
  const y = (v: number) => HEIGHT - PAD_Y - ((v - minY) / (maxY - minY || 1)) * (HEIGHT - PAD_Y * 2);
  return { x, y, minX, maxX, minY, maxY };
}

// Pick a glyph from the vital's label/code so each tile reads at a glance.
function iconFor(text: string): LucideIcon {
  const t = text.toLowerCase();
  if (t.includes("heart") || t.includes("pulse")) return Heart;
  if (t.includes("resp")) return Wind;
  if (t.includes("temp")) return Thermometer;
  if (t.includes("o2") || t.includes("oxygen") || t.includes("saturation") || t.includes("spo2")) return Percent;
  if (t.includes("pressure") || t.includes("bp") || t.includes("map")) return Droplets;
  if (t.includes("gcs") || t.includes("glasgow")) return Brain;
  if (t.includes("weight")) return Scale;
  if (t.includes("shock") || t.includes("index") || t.includes("gap")) return Gauge;
  return Activity;
}

function Sparkline({
  pts,
  stroke,
  fillId,
  band,
  guides,
}: {
  pts: string;
  stroke: string;
  fillId: string;
  band?: { y1: number; y2: number } | null;
  guides?: { y: number; color: string }[];
}) {
  const area = `${PAD_X},${HEIGHT - PAD_Y} ${pts} ${WIDTH - PAD_X},${HEIGHT - PAD_Y}`;
  return (
    <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="mt-3 w-full" preserveAspectRatio="none">
      <defs>
        <linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.22" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      {band && (
        <rect
          x={PAD_X}
          y={Math.min(band.y1, band.y2)}
          width={WIDTH - PAD_X * 2}
          height={Math.abs(band.y2 - band.y1)}
          fill="#16B981"
          opacity={0.08}
        />
      )}
      {guides?.map((g, i) => (
        <line key={i} x1={PAD_X} x2={WIDTH - PAD_X} y1={g.y} y2={g.y} stroke={g.color} strokeDasharray="3,3" strokeWidth={1} />
      ))}
      <polygon points={area} fill={`url(#${fillId})`} />
      <polyline points={pts} fill="none" stroke={stroke} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

export function VitalsMiniChart({ series }: { series: VitalSeries }) {
  const s = scale(series.points);
  const latest = series.points[series.points.length - 1];
  const stale = latest && isStale(latest.age_hours);
  const Icon = iconFor(`${series.label} ${series.code}`);

  if (!s || series.points.length === 0) {
    return (
      <div className="card p-4">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-leaf-soft text-brand-deep">
            <Icon className="h-4 w-4" />
          </span>
          <p className="text-sm font-medium text-ink-700">{series.label}</p>
        </div>
        <p className="mt-2 text-xs text-ink-400">No readings.</p>
      </div>
    );
  }

  const line = series.points.map((p) => `${s.x(p.hours)},${s.y(p.value)}`).join(" ");
  const [lo, hi] = series.normal_range || [];
  const band =
    hi !== undefined && lo !== undefined
      ? { y1: s.y(Math.min(hi, s.maxY)), y2: s.y(Math.max(lo, s.minY)) }
      : null;
  const stroke = stale ? "#7D86A8" : "#3D6BE6";

  return (
    <div className="card card-hover p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-leaf-soft text-brand-deep">
            <Icon className="h-4 w-4" />
          </span>
          <p className="text-sm font-medium text-ink-700">{series.label}</p>
        </div>
      </div>
      <p className={`mt-2 text-[1.7rem] font-extrabold leading-tight ${stale ? "text-ink-400" : "text-ink-900"}`}>
        {latest?.value}
        {series.unit && <span className="ml-1 text-xs font-medium text-ink-400">{series.unit}</span>}
      </p>
      {stale && <p className="text-[11px] text-sev-unknown">stale — {latest.age_hours?.toFixed(1)}h old</p>}
      <Sparkline pts={line} stroke={stroke} fillId={`v-${series.code}`} band={band} />
    </div>
  );
}

export function DerivedMiniChart({ series }: { series: DerivedSeries }) {
  const s = scale(series.points);
  const latest = series.points[series.points.length - 1];
  const Icon = iconFor(`${series.label} ${series.code}`);
  if (!s || series.points.length === 0) return null;

  const line = series.points.map((p) => `${s.x(p.hours)},${s.y(p.value)}`).join(" ");
  const concern = series.thresholds?.concern;
  const severe = series.thresholds?.severe;
  const isSevere = severe !== undefined && latest.value >= severe;
  const isConcern = !isSevere && concern !== undefined && latest.value >= concern;
  const stroke = isSevere ? "#F43F5E" : isConcern ? "#F59E0B" : "#3D6BE6";
  const guides = [
    concern !== undefined ? { y: s.y(concern), color: "#F59E0B" } : null,
    severe !== undefined ? { y: s.y(severe), color: "#F43F5E" } : null,
  ].filter(Boolean) as { y: number; color: string }[];

  return (
    <div className="card card-hover p-4">
      <div className="flex items-center gap-2">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-leaf-soft text-brand-deep">
          <Icon className="h-4 w-4" />
        </span>
        <p className="text-sm font-medium text-ink-700">{series.label}</p>
      </div>
      <p
        className={`mt-2 text-[1.7rem] font-extrabold leading-tight ${
          isSevere ? "text-sev-critical" : isConcern ? "text-sev-warning" : "text-ink-900"
        }`}
      >
        {latest?.value?.toFixed(2)}
      </p>
      <Sparkline pts={line} stroke={stroke} fillId={`d-${series.code}`} guides={guides} />
      {(concern !== undefined || severe !== undefined) && (
        <p className="mt-1 text-[11px] text-ink-400">
          concern {">"}
          {concern} · severe {">"}
          {severe}
        </p>
      )}
    </div>
  );
}
