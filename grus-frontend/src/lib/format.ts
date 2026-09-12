import { SourceRef, Severity } from "./types";

/** MIMIC dates are shifted and meaningless — always show hours-since-arrival. */
export function formatHours(hours: number | null | undefined): string {
  if (hours === null || hours === undefined || Number.isNaN(hours)) return "—";
  if (hours < 0) {
    return `pre-arrival (${Math.abs(hours).toFixed(1)}h before)`;
  }
  return `${hours.toFixed(1)}h after arrival`;
}

export function formatHoursShort(hours: number | null | undefined): string {
  if (hours === null || hours === undefined || Number.isNaN(hours)) return "—";
  if (hours < 0) return `-${Math.abs(hours).toFixed(1)}h`;
  return `${hours.toFixed(1)}h`;
}

/** Anything older than ~4h should render as stale, not current. */
export function isStale(ageHours: number | null | undefined): boolean {
  if (ageHours === null || ageHours === undefined) return false;
  return ageHours > 4;
}

export const SEVERITY_ORDER: Record<Severity, number> = {
  critical: 0,
  warning: 1,
  unknown: 2,
  info: 3,
};

export function sortBySeverity<T extends { severity: Severity }>(items: T[]): T[] {
  return [...items].sort((a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]);
}

export const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "Critical",
  warning: "Warning",
  info: "Info",
  unknown: "Unknown",
};

/** Chat/brief citations sometimes arrive as "labs#301694" strings, sometimes
 * as {table,id} objects. Normalize to one shape so components don't branch. */
export function normalizeSource(s: string | SourceRef): SourceRef {
  if (typeof s === "string") {
    const [table, idRaw] = s.split("#");
    return { table, id: Number(idRaw) };
  }
  return s;
}

export function formatPct(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return `${Math.round(n)}%`;
}

export function riskLevelColor(level: string): { text: string; bg: string; dot: string } {
  switch (level) {
    case "critical":
      return { text: "text-sev-critical", bg: "bg-sev-criticalBg", dot: "bg-sev-critical" };
    case "high":
      return { text: "text-sev-warning", bg: "bg-sev-warningBg", dot: "bg-sev-warning" };
    case "moderate":
      return { text: "text-amber-600", bg: "bg-sev-warningBg", dot: "bg-amber-500" };
    case "low":
      return { text: "text-sev-ok", bg: "bg-sev-okBg", dot: "bg-sev-ok" };
    default:
      // unknown — grey, never green
      return { text: "text-sev-unknown", bg: "bg-sev-unknownBg", dot: "bg-sev-unknown" };
  }
}

export function severityColor(sev: Severity): { text: string; bg: string; border: string; dot: string } {
  switch (sev) {
    case "critical":
      return { text: "text-sev-critical", bg: "bg-sev-criticalBg", border: "border-sev-critical/40", dot: "bg-sev-critical" };
    case "warning":
      return { text: "text-sev-warning", bg: "bg-sev-warningBg", border: "border-sev-warning/40", dot: "bg-sev-warning" };
    case "info":
      return { text: "text-sev-info", bg: "bg-sev-infoBg", border: "border-sev-info/40", dot: "bg-sev-info" };
    default:
      return { text: "text-sev-unknown", bg: "bg-sev-unknownBg", border: "border-sev-unknown/40", dot: "bg-sev-unknown" };
  }
}
