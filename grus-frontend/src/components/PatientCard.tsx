import Link from "next/link";
import { ArrowUpRight, Clock3, User } from "lucide-react";
import { PatientCard as PatientCardType } from "@/lib/types";
import { riskLevelColor, formatHours } from "@/lib/format";

export function PatientCard({ patient }: { patient: PatientCardType }) {
  const c = riskLevelColor(patient.risk_level);
  const sid = patient.short_id || String(patient.hadm_id).slice(-4);
  return (
    <Link
      href={`/patients/${patient.hadm_id}?subject=${patient.subject_id}&headline=${encodeURIComponent(
        patient.headline || ""
      )}`}
      className="group flex flex-col card card-hover p-5"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div
            className="flex h-12 w-12 shrink-0 flex-col items-center justify-center rounded-2xl bg-brand-ink text-white"
            title="Say this number to Alexa"
          >
            <span className="text-[8px] font-semibold uppercase tracking-wider text-white/60">ID</span>
            <span className="text-[15px] font-extrabold leading-none">{sid}</span>
          </div>
          <div className="min-w-0">
            <p className="flex items-center gap-1 whitespace-nowrap text-[13px] font-semibold text-ink-900">
              <User className="h-3.5 w-3.5 text-ink-400" />
              {patient.age ?? "—"}
              {patient.gender ?? ""}
              {patient.cohort && <span className="truncate font-normal capitalize text-ink-400">· {patient.cohort}</span>}
            </p>
            <p className="text-[11px] text-ink-400">Admission {patient.hadm_id}</p>
          </div>
        </div>
        <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider ${c.bg} ${c.text}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${c.dot}`} />
          {patient.risk_level}
        </span>
      </div>

      <p className="mt-4 text-[15px] font-semibold leading-snug text-ink-900 line-clamp-2">
        {patient.headline || "No headline yet"}
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {patient.alert_count > 0 && (
          <span className="rounded-full bg-sev-criticalBg px-2.5 py-1 text-[11px] font-semibold text-sev-critical">
            {patient.alert_count} alert{patient.alert_count === 1 ? "" : "s"}
          </span>
        )}
        {patient.unknown_count > 0 && (
          <span className="rounded-full bg-sev-unknownBg px-2.5 py-1 text-[11px] font-semibold text-sev-unknown">
            {patient.unknown_count} unknown{patient.unknown_count === 1 ? "" : "s"}
          </span>
        )}
        {patient.prior_admissions > 0 && (
          <span className="rounded-full bg-leaf-soft px-2.5 py-1 text-[11px] font-semibold text-brand-deep">
            {patient.prior_admissions} prior visit{patient.prior_admissions === 1 ? "" : "s"}
          </span>
        )}
      </div>

      <div className="mt-auto flex items-center justify-between border-t border-ink-900/[0.06] pt-3.5" style={{ marginTop: "1rem" }}>
        <span className="flex items-center gap-1.5 text-xs text-ink-500">
          <Clock3 className="h-3.5 w-3.5" />
          {patient.hours_since_arrival != null ? `${formatHours(patient.hours_since_arrival)}` : "Arrival time unknown"}
        </span>
        <span className="flex h-8 w-8 items-center justify-center rounded-full border border-ink-900/10 text-ink-700 transition group-hover:border-brand-ink group-hover:bg-brand-ink group-hover:text-white">
          <ArrowUpRight className="h-4 w-4" />
        </span>
      </div>
    </Link>
  );
}

export function PatientCardSkeleton() {
  return (
    <div className="card p-5">
      <div className="flex items-center gap-3">
        <span className="skeleton h-12 w-12 rounded-2xl" />
        <div className="space-y-1.5">
          <span className="skeleton block h-3 w-24 rounded" />
          <span className="skeleton block h-3 w-16 rounded" />
        </div>
      </div>
      <div className="skeleton mt-4 h-4 w-4/5 rounded" />
      <div className="skeleton mt-2 h-4 w-3/5 rounded" />
      <div className="mt-4 flex gap-2">
        <span className="skeleton h-5 w-14 rounded-full" />
        <span className="skeleton h-5 w-14 rounded-full" />
      </div>
    </div>
  );
}
