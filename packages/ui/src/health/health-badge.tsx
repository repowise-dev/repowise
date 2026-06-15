import { bandForScore } from "@repowise-dev/types";
import type { HealthBand } from "@repowise-dev/types/health";
import { healthBandSoftBadgeClass } from "./tokens";

export interface HealthBadgeProps {
  score: number | null | undefined;
  /** Explicit band from the API; when omitted it is derived from `score`
   * via the shared `bandForScore` mirror (no hardcoded cutoffs). */
  band?: HealthBand;
  size?: "xs" | "sm";
}

/** Compact health-score pill, designed to inline next to a file path
 * on Hotspot / Ownership / Graph rows without changing those shared
 * components' shapes. Renders nothing when the score is missing.
 * Colored by the 3 defect-backed health bands. */
export function HealthBadge({ score, band, size = "xs" }: HealthBadgeProps) {
  if (score == null) return null;
  const resolved = band ?? bandForScore(score);
  const cls = healthBandSoftBadgeClass(resolved);
  const sizing =
    size === "xs"
      ? "text-[10px] px-1.5 py-0.5"
      : "text-xs px-2 py-0.5";
  return (
    <span
      className={`inline-flex items-center rounded font-semibold tabular-nums ${cls} ${sizing}`}
      title={`Health ${score.toFixed(1)}/10`}
    >
      {score.toFixed(1)}
    </span>
  );
}
