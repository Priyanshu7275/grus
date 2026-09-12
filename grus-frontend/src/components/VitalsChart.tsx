import { DerivedSeries, VitalSeries } from "@/lib/types";
import { isStale } from "@/lib/format";

const WIDTH = 320;
const HEIGHT = 100;
const PAD_X = 8;
const PAD_Y = 10;

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

export function VitalsMiniChart({ series }: { series: VitalSeries }) {
  const s = scale(series.points);
  const latest = series.points[series.points.length - 1];
  const stale = latest && isStale(latest.age_hours);

  if (!s || series.points.length === 0) {
    return (
      <div className="rounded-lg glass px-3 py-3">
        <p className="text-xs text-ink-500">{series.label}</p>
        <p className="mt-1 text-xs text-ink-400">No readings.</p>
      </div>
    );
  }

  const line = series.points.map((p) => `${s.x(p.hours)},${s.y(p.value)}`).join(" ");
  const [lo, hi] = series.normal_range || [];
  const bandTop = hi !== undefined ? s.y(Math.min(hi, s.maxY)) : null;
  const bandBottom = lo !== undefined ? s.y(Math.max(lo, s.minY)) : null;

  return (
    <div className="rounded-lg glass px-3 py-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-ink-500">{series.label}</p>
        <p className={`num text-sm font-semibold ${stale ? "text-sev-unknown" : "text-ink-900"}`}>
          {latest?.value}
          <span className="ml-1 text-[10px] font-normal text-ink-400">{series.unit}</span>
        </p>
      </div>
      {stale && <p className="mt-0.5 text-[10px] text-sev-unknown">stale — {latest.age_hours?.toFixed(1)}h old</p>}
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="mt-1.5 w-full" preserveAspectRatio="none">
        {bandTop !== null && bandBottom !== null && (
          <rect
            x={PAD_X}
            y={Math.min(bandTop, bandBottom)}
            width={WIDTH - PAD_X * 2}
            height={Math.abs(bandBottom - bandTop)}
            fill="#22c55e"
            opacity={0.08}
          />
        )}
        <polyline points={line} fill="none" stroke="#3b82f6" strokeWidth={1.6} />
        {series.points.map((p, i) => (
          <circle key={i} cx={s.x(p.hours)} cy={s.y(p.value)} r={2.2} fill="#3b82f6" />
        ))}
      </svg>
    </div>
  );
}

export function DerivedMiniChart({ series }: { series: DerivedSeries }) {
  const s = scale(series.points);
  const latest = series.points[series.points.length - 1];
  if (!s || series.points.length === 0) return null;

  const line = series.points.map((p) => `${s.x(p.hours)},${s.y(p.value)}`).join(" ");
  const concern = series.thresholds?.concern;
  const severe = series.thresholds?.severe;
  const isSevere = severe !== undefined && latest.value >= severe;
  const isConcern = !isSevere && concern !== undefined && latest.value >= concern;

  return (
    <div className="rounded-lg glass px-3 py-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-ink-500">{series.label}</p>
        <p
          className={`num text-sm font-semibold ${
            isSevere ? "text-sev-critical" : isConcern ? "text-sev-warning" : "text-ink-900"
          }`}
        >
          {latest?.value?.toFixed(2)}
        </p>
      </div>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="mt-1.5 w-full" preserveAspectRatio="none">
        {concern !== undefined && (
          <line x1={PAD_X} x2={WIDTH - PAD_X} y1={s.y(concern)} y2={s.y(concern)} stroke="#f59e0b" strokeDasharray="3,3" strokeWidth={1} />
        )}
        {severe !== undefined && (
          <line x1={PAD_X} x2={WIDTH - PAD_X} y1={s.y(severe)} y2={s.y(severe)} stroke="#ef4444" strokeDasharray="3,3" strokeWidth={1} />
        )}
        <polyline points={line} fill="none" stroke={isSevere ? "#ef4444" : isConcern ? "#f59e0b" : "#3b82f6"} strokeWidth={1.8} />
      </svg>
      {(concern !== undefined || severe !== undefined) && (
        <p className="mt-1 text-[10px] text-ink-400">
          concern {'>'}{concern} · severe {'>'}{severe}
        </p>
      )}
    </div>
  );
}
