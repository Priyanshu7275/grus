import {
  BriefSection,
  ParsedBrief,
  isCriticalUnknownsHeading,
  isRedFlagsHeading,
  isResolvedHeading,
  tokenizeCitations,
} from "@/lib/briefParser";
import { Severity } from "@/lib/types";
import { Citation } from "./SourceDrawer";
import { SeverityBadge } from "./SeverityBadge";
import { severityColor } from "@/lib/format";

function renderTokens(text: string, keyPrefix: string) {
  return tokenizeCitations(text).map((t, i) =>
    t.type === "text" ? (
      <span key={`${keyPrefix}-${i}`}>{t.value}</span>
    ) : (
      <Citation key={`${keyPrefix}-${i}`} table={t.table} id={t.id} />
    )
  );
}

export function BriefRenderer({ parsed }: { parsed: ParsedBrief }) {
  if (!parsed.sections.length) {
    return <p className="text-sm text-ink-500">Empty brief.</p>;
  }
  return (
    <div className="space-y-6">
      {parsed.sections.map((section, i) => (
        <BriefSectionView key={i} section={section} />
      ))}
    </div>
  );
}

function BriefSectionView({ section }: { section: BriefSection }) {
  const heading = section.heading;

  if (isRedFlagsHeading(heading)) {
    return (
      <section>
        <SectionHeading>{heading}</SectionHeading>
        <div className="space-y-2">
          {section.blocks.map((b, i) => (
            <AlertBlock key={i} tag={b.tag || "warning"} title={b.title} detailLines={b.detailLines} action={b.action} idx={i} />
          ))}
        </div>
      </section>
    );
  }

  if (isCriticalUnknownsHeading(heading)) {
    return (
      <section>
        <SectionHeading urgent>{heading}</SectionHeading>
        <p className="mb-2 text-xs text-ink-500">
          What GRUS looked for and could not find. Not an error state — this is the differentiator.
        </p>
        <div className="space-y-2">
          {section.blocks.map((b, i) => (
            <UnknownBlock key={i} title={b.title} detailLines={b.detailLines} action={b.action} idx={i} />
          ))}
        </div>
      </section>
    );
  }

  if (isResolvedHeading(heading)) {
    return (
      <section>
        <SectionHeading>{heading}</SectionHeading>
        <p className="mb-2 text-xs text-ink-500">Found in clinical notes, not in any structured table.</p>
        <div className="space-y-2">
          {section.blocks.map((b, i) => (
            <ResolvedBlock key={i} title={b.title} detailLines={b.detailLines} action={b.action} idx={i} />
          ))}
        </div>
      </section>
    );
  }

  // Plain prose sections: SUMMARY, CURRENT STATE, HISTORY, or an unnamed preamble.
  return (
    <section>
      {heading && <SectionHeading>{heading}</SectionHeading>}
      <div className="space-y-2 text-sm leading-relaxed text-ink-900">
        {section.blocks.map((b, i) => (
          <p key={i}>
            {renderTokens(b.title, `p-${i}`)}
            {b.detailLines.map((line, j) => (
              <span key={j} className="block text-ink-500">
                {renderTokens(line, `p-${i}-${j}`)}
              </span>
            ))}
          </p>
        ))}
      </div>
    </section>
  );
}

function SectionHeading({ children, urgent }: { children: React.ReactNode; urgent?: boolean }) {
  return (
    <h2
      className={`mb-2 font-mono text-xs font-semibold uppercase tracking-wider ${
        urgent ? "text-sev-unknown" : "text-ink-500"
      }`}
    >
      {children}
    </h2>
  );
}

function AlertBlock({
  tag,
  title,
  detailLines,
  action,
  idx,
}: {
  tag: Severity;
  title: string;
  detailLines: string[];
  action?: string;
  idx: number;
}) {
  const c = severityColor(tag);
  return (
    <div className={`rounded-lg border ${c.border} ${c.bg} px-4 py-3`}>
      <div className="flex items-start justify-between gap-3">
        <p className={`text-sm font-semibold ${c.text}`}>{renderTokens(title, `alert-t-${idx}`)}</p>
        <SeverityBadge severity={tag} small />
      </div>
      {detailLines.length > 0 && (
        <div className="mt-1.5 space-y-1 text-sm text-ink-900/90">
          {detailLines.map((line, i) => (
            <p key={i}>{renderTokens(line, `alert-d-${idx}-${i}`)}</p>
          ))}
        </div>
      )}
      {action && (
        <p className="mt-2 flex items-start gap-1.5 text-sm font-medium text-ink-900">
          <span className="text-ink-400">→</span>
          {renderTokens(action, `alert-a-${idx}`)}
        </p>
      )}
    </div>
  );
}

function UnknownBlock({
  title,
  detailLines,
  action,
  idx,
}: {
  title: string;
  detailLines: string[];
  action?: string;
  idx: number;
}) {
  return (
    <div className="rounded-lg border border-sev-unknown/50 bg-sev-unknownBg px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-semibold text-sev-unknown">
          <span className="mr-1.5">?</span>
          {renderTokens(title, `unk-t-${idx}`)}
        </p>
        <SeverityBadge severity="unknown" small />
      </div>
      {detailLines.length > 0 && (
        <div className="mt-1.5 space-y-1 text-sm text-ink-900/90">
          {detailLines.map((line, i) => (
            <p key={i}>{renderTokens(line, `unk-d-${idx}-${i}`)}</p>
          ))}
        </div>
      )}
      {action && (
        <p className="mt-2 flex items-start gap-1.5 text-sm font-medium text-ink-900">
          <span className="text-ink-400">→</span>
          {renderTokens(action, `unk-a-${idx}`)}
        </p>
      )}
    </div>
  );
}

function ResolvedBlock({
  title,
  detailLines,
  action,
  idx,
}: {
  title: string;
  detailLines: string[];
  action?: string;
  idx: number;
}) {
  return (
    <div className="rounded-lg border border-sev-info/40 bg-sev-infoBg px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-semibold text-sev-info">
          <span className="mr-1.5">✓</span>
          {renderTokens(title, `res-t-${idx}`)}
        </p>
        <span className="rounded border border-sev-info/40 bg-sev-infoBg px-1.5 py-0.5 font-mono text-[10px] text-sev-info">
          found in notes
        </span>
      </div>
      {detailLines.length > 0 && (
        <div className="mt-1.5 space-y-1 text-sm text-ink-900/90">
          {detailLines.map((line, i) => (
            <p key={i}>{renderTokens(line, `res-d-${idx}-${i}`)}</p>
          ))}
        </div>
      )}
      {action && (
        <p className="mt-2 flex items-start gap-1.5 text-sm font-medium text-ink-900">
          <span className="text-ink-400">→</span>
          {renderTokens(action, `res-a-${idx}`)}
        </p>
      )}
    </div>
  );
}
