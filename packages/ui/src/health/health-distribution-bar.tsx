import type { HealthBand, HealthDistribution } from "@repowise-dev/types/health";

/** Worst-first, matching how the surface lists files. */
const ORDER: HealthBand[] = ["alert", "warning", "healthy"];

const BAND_LABEL: Record<HealthBand, string> = {
  healthy: "Healthy",
  warning: "Warning",
  alert: "Alert",
};

/* Literal class strings so Tailwind's static scanner keeps them. */
const BAND_BAR: Record<HealthBand, string> = {
  healthy: "bg-[var(--color-success)]",
  warning: "bg-[var(--color-caution)]",
  alert: "bg-[var(--color-error)]",
};

const BAND_DOT: Record<HealthBand, string> = BAND_BAR;

export interface HealthDistributionBarProps {
  distribution: HealthDistribution;
  /** When true (default), render the per-band legend under the bar. */
  showCounts?: boolean;
  height?: "sm" | "md";
}

/**
 * NLOC-weighted distribution of files across the 3 defect-backed bands
 * (Alert / Warning / Healthy). Mirrors the `SeverityDistribution` idiom so the
 * health surface reads consistently. Widths are by code volume (NLOC), not file
 * count, so a single large unhealthy file isn't hidden behind many tiny ones.
 */
export function HealthDistributionBar({
  distribution,
  showCounts = true,
  height = "sm",
}: HealthDistributionBarProps) {
  const total = distribution.total_nloc;
  if (!distribution.total_files || total === 0) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">No files analyzed.</p>
    );
  }
  const h = height === "sm" ? "h-1.5" : "h-2";
  return (
    <div className="space-y-1.5">
      <div
        className={`flex w-full ${h} overflow-hidden rounded-full bg-[var(--color-bg-muted)]`}
        title={ORDER.map(
          (b) => `${BAND_LABEL[b]} ${distribution.bands[b].pct}%`,
        ).join(" · ")}
      >
        {ORDER.map((b) => {
          const pct = distribution.bands[b].pct;
          if (pct === 0) return null;
          return (
            <div
              key={b}
              className={BAND_BAR[b]}
              style={{ width: `${pct}%` }}
              aria-label={`${BAND_LABEL[b]} ${pct}%`}
            />
          );
        })}
      </div>
      {showCounts && (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-[var(--color-text-tertiary)]">
          {ORDER.map((b) => (
            <span key={b} className="inline-flex items-center gap-1 tabular-nums">
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${BAND_DOT[b]}`} />
              {distribution.bands[b].pct}% {BAND_LABEL[b].toLowerCase()}
              <span className="text-[var(--color-text-tertiary)]/70">
                ({distribution.bands[b].files})
              </span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
