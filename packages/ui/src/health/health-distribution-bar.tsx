import {
  HEALTH_BAND_LABEL,
  HEALTH_BAND_ORDER,
  type HealthBand,
  type HealthDistribution,
} from "@repowise-dev/types/health";

import { HEALTH_BAND_BAR } from "./tokens";

export interface HealthDistributionBarProps {
  distribution: HealthDistribution;
  /** When true (default), render the per-band legend under the bar. */
  showCounts?: boolean;
  height?: "sm" | "md";
}

/**
 * NLOC-weighted distribution of files across the five health bands. Widths are
 * by code volume (NLOC), not file count, so a single large at-risk file isn't
 * hidden behind many tiny ones.
 */
export function HealthDistributionBar({
  distribution,
  showCounts = true,
  height = "sm",
}: HealthDistributionBarProps) {
  // A server that predates the five bands still serves the three-band shape,
  // and the extension and the web app upgrade independently of it. A missing
  // band reads as absent, not as a crash.
  const share = (b: HealthBand) => distribution.bands[b]?.pct ?? 0;
  const files = (b: HealthBand) => distribution.bands[b]?.files ?? 0;
  const total = distribution.total_nloc;
  // A server predating the five bands serves the three-band shape with correct
  // totals, which would otherwise render as "we analysed 128 files and none of
  // them is in any band". No band accounted for is an unknown shape, not zero.
  const accounted = HEALTH_BAND_ORDER.some((b) => share(b) > 0);
  if (!distribution.total_files || total === 0 || !accounted) {
    return (
      <p className="text-xs text-[var(--color-text-tertiary)]">No files analyzed.</p>
    );
  }
  const h = height === "sm" ? "h-1.5" : "h-2";
  return (
    <div className="space-y-1.5">
      <div
        className={`flex w-full ${h} overflow-hidden rounded-full bg-[var(--color-bg-inset)]`}
        title={HEALTH_BAND_ORDER.map(
          (b) => `${HEALTH_BAND_LABEL[b]} ${share(b)}%`,
        ).join(" · ")}
      >
        {HEALTH_BAND_ORDER.map((b) => {
          const pct = share(b);
          if (pct === 0) return null;
          return (
            <div
              key={b}
              className={HEALTH_BAND_BAR[b]}
              style={{ width: `${pct}%` }}
              aria-label={`${HEALTH_BAND_LABEL[b]} ${pct}%`}
            />
          );
        })}
      </div>
      {showCounts && (
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-[var(--color-text-tertiary)]">
          {HEALTH_BAND_ORDER.map((b) => (
            <span key={b} className="inline-flex items-center gap-1 tabular-nums">
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${HEALTH_BAND_BAR[b]}`} />
              {share(b)}% {HEALTH_BAND_LABEL[b].toLowerCase()}
              <span className="text-[var(--color-text-tertiary)]/70">
                ({files(b)})
              </span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
