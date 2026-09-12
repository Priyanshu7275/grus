import Link from "next/link";
import { PatientCard as PatientCardType } from "@/lib/types";
import { riskLevelColor, formatHours } from "@/lib/format";

export function PatientCard({ patient }: { patient: PatientCardType }) {
  const c = riskLevelColor(patient.risk_level);
  return (
    <Link
      href={`/patients/${patient.hadm_id}?subject=${patient.subject_id}&headline=${encodeURIComponent(
        patient.headline || ""
      )}`}
      className="group block rounded-2xl glass p-4 shadow-soft transition hover:shadow-lift"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${c.dot}`} />
          <span className={`font-mono text-[11px] uppercase tracking-wide ${c.text}`}>{patient.risk_level}</span>
        </div>
        <span className="font-mono text-[11px] text-ink-400">#{patient.hadm_id}</span>
      </div>

      <p className="mt-3 text-sm leading-snug text-ink-900 line-clamp-2">{patient.headline || "No headline yet"}</p>

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-500">
        <span>
          {patient.age ?? "—"}
          {patient.gender ?? ""}
        </span>
        {patient.cohort && <span className="capitalize">{patient.cohort}</span>}
        {patient.hours_since_arrival != null && <span className="num">{formatHours(patient.hours_since_arrival)}</span>}
      </div>

      <div className="mt-3 flex items-center gap-2">
        {patient.alert_count > 0 && (
          <span className="rounded-md border border-sev-critical/30 bg-sev-criticalBg px-1.5 py-0.5 font-mono text-[11px] text-sev-critical">
            {patient.alert_count} alert{patient.alert_count === 1 ? "" : "s"}
          </span>
        )}
        {patient.unknown_count > 0 && (
          <span className="rounded-md border border-sev-unknown/30 bg-sev-unknownBg px-1.5 py-0.5 font-mono text-[11px] text-sev-unknown">
            {patient.unknown_count} unknown{patient.unknown_count === 1 ? "" : "s"}
          </span>
        )}
        {patient.prior_admissions > 0 && (
          <span className="rounded-md border border-ink-900/10 bg-ink-900/5 px-1.5 py-0.5 font-mono text-[11px] text-ink-500">
            {patient.prior_admissions} prior visit{patient.prior_admissions === 1 ? "" : "s"}
          </span>
        )}
      </div>
    </Link>
  );
}

export function PatientCardSkeleton() {
  return (
    <div className="rounded-2xl glass p-4">
      <div className="flex items-center gap-2">
        <span className="skeleton h-2.5 w-2.5 rounded-full" />
        <span className="skeleton h-3 w-16 rounded" />
      </div>
      <div className="skeleton mt-3 h-4 w-4/5 rounded" />
      <div className="skeleton mt-2 h-4 w-3/5 rounded" />
      <div className="mt-3 flex gap-2">
        <span className="skeleton h-3 w-14 rounded" />
        <span className="skeleton h-3 w-14 rounded" />
      </div>
    </div>
  );
}
