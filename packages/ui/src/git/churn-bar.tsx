import { cn } from "../lib/cn";

interface ChurnBarProps {
  percentile: number;
  className?: string;
  /**
   * `"scale"` grades the value red/amber/green. `"neutral"` draws the same
   * length in one quiet colour, for a table that is already sorted by this
   * number: every row of a hotspot list is high-churn by definition, so
   * grading them paints the whole column red and reads as an alarm about
   * something that is merely the subject of the table.
   */
  tone?: "scale" | "neutral";
}

export function ChurnBar({ percentile, className, tone = "scale" }: ChurnBarProps) {
  const color =
    tone === "neutral"
      ? "bg-[var(--color-text-tertiary)]"
      : percentile >= 75
        ? "bg-[var(--color-error)]"
        : percentile >= 50
          ? "bg-[var(--color-warning)]"
          : "bg-[var(--color-success)]";

  return (
    <div className={cn("h-1.5 w-full rounded-full bg-[var(--color-bg-elevated)]", className)}>
      <div
        className={cn("h-1.5 rounded-full transition-all", color)}
        style={{ width: `${Math.min(100, Math.max(0, percentile))}%` }}
      />
    </div>
  );
}
