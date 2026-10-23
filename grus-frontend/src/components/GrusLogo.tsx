export function GrusLogo({ size = 58, withWordmark = true }: { size?: number; withWordmark?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src="/grus-logo.png" alt="GRUS" width={size} height={size} style={{ height: size, width: "auto" }} />
      {withWordmark && (
        <span className="text-[1.6rem] font-extrabold tracking-tight text-brand-ink">
  GRUS
</span>
      )}
    </span>
  );
}
