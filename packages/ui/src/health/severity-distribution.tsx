import { SEVERITY_BAR, SEVERITY_LABEL, type Severity } from "./tokens";

export interface SeverityBreakdown {
  critical: number;
  high: number;
  medium: number;
  low: number;
}

export interface SeverityDistributionProps {
  breakdown: SeverityBreakdown;
  /** When true, also renders the per-severity counts below the bar. */
  showCounts?: boolean;
  height?: "sm" | "md";
}

const ORDER: Severity[] = ["critical", "high", "medium", "low"];

export function SeverityDistribution({
  breakdown,
  showCounts = true,
  height = "sm",
}: SeverityDistributionProps) {
  const total =
    breakdown.critical + breakdown.high + breakdown.medium + breakdown.low;
  if (total === 0) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">No findings.</p>
    );
  }
  const h = height === "sm" ? "h-1.5" : "h-2";
  return (
    <div className="space-y-1.5">
      <div
        className={`flex w-full ${h} overflow-hidden rounded-full bg-[var(--color-bg-muted)]`}
        title={ORDER.map((s) => `${SEVERITY_LABEL[s]} ${breakdown[s]}`).join(" · ")}
      >
        {ORDER.map((s) => {
          const pct = (breakdown[s] / total) * 100;
          if (pct === 0) return null;
          return (
            <div
              key={s}
              className={SEVERITY_BAR[s]}
              style={{ width: `${pct}%` }}
              aria-label={`${SEVERITY_LABEL[s]} ${breakdown[s]}`}
            />
          );
        })}
      </div>
      {showCounts && (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-[var(--color-text-tertiary)]">
          {ORDER.map((s) => (
            <span key={s} className="inline-flex items-center gap-1 tabular-nums">
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${SEVERITY_BAR[s]}`} />
              {breakdown[s]} {SEVERITY_LABEL[s].toLowerCase()}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
