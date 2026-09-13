import { AlertItem } from "@/lib/types";
import { sortBySeverity } from "@/lib/format";
import { SeverityBadge } from "./SeverityBadge";
import { Citation } from "./SourceDrawer";

export function AlertsList({ alerts }: { alerts: AlertItem[] }) {
  if (!alerts.length) {
    return <p className="text-base text-ink-700">No active alerts at this point in time.</p>;
  }
  const sorted = sortBySeverity(alerts);
  return (
    <div className="space-y-2">
      {sorted.map((a, i) => (
        <div
          key={a.code || i}
          className="rounded-lg border border-ink-900/10 bg-ink-900/5 px-4 py-3"
        >
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm font-semibold text-ink-900">{a.title}</p>
            <SeverityBadge severity={a.severity} small />
          </div>
          {(a.detail || a.body) && <p className="mt-1 text-sm text-ink-900/90">{a.detail || a.body}</p>}
          {a.action && (
            <p className="mt-2 flex items-start gap-1.5 text-sm font-medium text-ink-900">
              <span className="text-ink-400">→</span>
              {a.action}
            </p>
          )}
          {a.note && <p className="mt-1 text-xs italic text-ink-400">{a.note}</p>}
          {a.sources && a.sources.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {a.sources.map((s, j) => (
                <Citation key={j} table={s.table} id={s.id} label={s.label} />
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
