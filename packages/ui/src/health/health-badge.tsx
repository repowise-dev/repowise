import { scoreSoftBadgeClass } from "./tokens";

export interface HealthBadgeProps {
  score: number | null | undefined;
  size?: "xs" | "sm";
}

/** Compact health-score pill, designed to inline next to a file path
 * on Hotspot / Ownership / Graph rows without changing those shared
 * components' shapes. Renders nothing when the score is missing.
 * Colors come from the shared score-band helper in tokens.ts. */
export function HealthBadge({ score, size = "xs" }: HealthBadgeProps) {
  if (score == null) return null;
  const cls = scoreSoftBadgeClass(score);
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
