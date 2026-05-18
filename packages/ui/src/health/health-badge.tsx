export interface HealthBadgeProps {
  score: number | null | undefined;
  size?: "xs" | "sm";
}

function scoreColor(score: number): string {
  if (score < 4) return "bg-red-500/15 text-red-500";
  if (score < 6) return "bg-amber-500/15 text-amber-500";
  if (score < 8) return "bg-yellow-500/15 text-yellow-500";
  return "bg-emerald-500/15 text-emerald-500";
}

/** Compact health-score pill, designed to inline next to a file path
 * on Hotspot / Ownership / Graph rows without changing those shared
 * components' shapes. Renders nothing when the score is missing. */
export function HealthBadge({ score, size = "xs" }: HealthBadgeProps) {
  if (score == null) return null;
  const cls = scoreColor(score);
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
