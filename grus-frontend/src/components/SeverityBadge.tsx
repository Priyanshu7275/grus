import { Severity } from "@/lib/types";
import { severityColor, SEVERITY_LABEL } from "@/lib/format";

export function SeverityDot({ severity }: { severity: Severity }) {
  const c = severityColor(severity);
  return <span className={`inline-block h-2 w-2 rounded-full ${c.dot}`} aria-hidden />;
}

export function SeverityBadge({ severity, small }: { severity: Severity; small?: boolean }) {
  const c = severityColor(severity);
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border ${c.border} ${c.bg} ${c.text} font-mono uppercase tracking-wide ${
        small ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-1 text-xs"
      }`}
    >
      <SeverityDot severity={severity} />
      {SEVERITY_LABEL[severity]}
    </span>
  );
}
