import { bandForScore } from "@repowise-dev/types";
import { HEALTH_BAND_ORDER, type HealthBand } from "@repowise-dev/types/health";
import { healthBandSoftBadgeClass } from "./tokens";

export interface HealthBadgeProps {
  score: number | null | undefined;
  /** Explicit band from the API; when omitted, or when a server predating the
   * five bands sends one this build does not know, it is derived from `score`
   * via the shared `bandForScore` mirror (no hardcoded cutoffs). */
  band?: HealthBand;
  size?: "xs" | "sm";
}

/** Compact health-score pill, designed to inline next to a file path
 * on Hotspot / Ownership / Graph rows without changing those shared
 * components' shapes. Renders nothing when the score is missing. */
export function HealthBadge({ score, band, size = "xs" }: HealthBadgeProps) {
  if (score == null) return null;
  const cls = healthBandSoftBadgeClass(
    band && HEALTH_BAND_ORDER.includes(band) ? band : bandForScore(score),
  );
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
