import {
  HEALTH_BAND_LABEL,
  HEALTH_BAND_ORDER,
  type HealthBand,
  type HealthDistribution,
} from "@repowise-dev/types/health";

import { ProportionBar } from "../shared/proportion-bar";
import { HEALTH_BAND_SEGMENT } from "./tokens";

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
  return (
    <ProportionBar
      label="Code volume by health band"
      size={height}
      sort={false}
      legend={showCounts}
      segments={HEALTH_BAND_ORDER.map((b) => ({
        key: b,
        label: HEALTH_BAND_LABEL[b],
        value: share(b),
        detail: `${share(b)}% · ${files(b).toLocaleString()} ${files(b) === 1 ? "file" : "files"}`,
        color: HEALTH_BAND_SEGMENT[b],
      }))}
    />
  );
}
