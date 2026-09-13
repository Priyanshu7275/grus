import { TrustBlock } from "@/lib/types";
import { formatPct } from "@/lib/format";

export function TrustStrip({ trust, generationMs, cached }: { trust?: TrustBlock; generationMs?: number; cached?: boolean }) {
  if (!trust) return null;
  const sourced = trust.citations_valid ?? trust.claims_sourced;
  const invalid = trust.citations_invalid;
  const pct = trust.traceable_pct;
  const abstained = trust.claims_abstained;

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-sev-ok/30 bg-sev-okBg px-4 py-2.5 font-mono text-xs text-sev-ok">
      {pct !== undefined && <span className="font-semibold">{formatPct(pct)} traceable</span>}
      {sourced !== undefined && <span>{sourced} sourced</span>}
      {!!invalid && <span className="text-sev-critical">{invalid} unverified</span>}
      {abstained !== undefined && <span className="text-ink-500">{abstained} abstained</span>}
      {generationMs !== undefined && (
        <span className="ml-auto text-ink-500">
          generated in {(generationMs / 1000).toFixed(1)}s{cached ? " · cached" : ""}
        </span>
      )}
    </div>
  );
}
