import { cn } from "../lib/cn";
import { formatHealthImpact } from "./tokens";

/**
 * A finding's deduction, or the word for not having one.
 *
 * One owner for the figure, because the alternative is every surface deciding
 * for itself what a zero means. Performance findings carry an impact of zero
 * by construction, and the defect formatter rendered that as "−0.00" in red:
 * a measured deduction that rounded away, on a marker that never scored one.
 * A marker with no deduction says so in a word, in the neutral colour, because
 * the red is the deduction's colour and there is no deduction.
 */
export function ImpactFigure({
  impact,
  tone = "deduction",
  className,
}: {
  impact: number | null | undefined;
  /**
   * How loud the figure is.
   *
   * `deduction` is the red, for a figure that is the point of its row.
   * `muted` is for a long list of them, where every row already carries a
   * severity mark and colouring the numbers too turns a breakdown into a wall
   * of red that ranks nothing.
   */
  tone?: "deduction" | "muted";
  className?: string;
}) {
  const figure = formatHealthImpact(impact);
  if (figure === null) {
    return (
      <span className={cn("shrink-0 text-[var(--color-text-tertiary)]", className)}>
        not scored
      </span>
    );
  }
  return (
    <span
      className={cn(
        "shrink-0 tabular-nums",
        tone === "muted" ? "text-[var(--color-text-tertiary)]" : "text-[var(--color-error)]",
        className,
      )}
    >
      {figure}
    </span>
  );
}
