import { cn } from "../lib/cn";
import type { RiskDriver } from "@repowise-dev/types/git";

export interface RiskDriverBreakdownProps {
  drivers: RiskDriver[];
  className?: string;
}

/**
 * Per-feature change-risk attribution. The model is a linear logistic, so each
 * driver's signed contribution is exact: positive (red, right) pushed risk up,
 * negative (green, left) pulled it down. Bars are scaled to the strongest
 * contribution in the set. NaN-valued (unknown) features are skipped.
 */
export function RiskDriverBreakdown({ drivers, className }: RiskDriverBreakdownProps) {
  const shown = drivers.filter((d) => d.value !== null);
  if (shown.length === 0) return null;

  const maxAbs = Math.max(...shown.map((d) => Math.abs(d.contribution)), 1e-6);

  return (
    <div className={cn("space-y-1.5", className)}>
      {shown.map((d) => {
        const pct = (Math.abs(d.contribution) / maxAbs) * 50; // half-width per side
        const raises = d.contribution >= 0;
        return (
          <div key={d.feature} className="flex items-center gap-2 text-xs">
            <span
              className="w-28 shrink-0 truncate text-[var(--color-text-secondary)]"
              title={d.label}
            >
              {d.label}
            </span>
            {/* Diverging bar centered on a zero baseline. */}
            <div className="relative h-3 flex-1 rounded bg-[var(--color-bg-elevated)]">
              <div className="absolute inset-y-0 left-1/2 w-px bg-[var(--color-border-default)]" />
              <div
                className={cn(
                  "absolute inset-y-0 rounded",
                  raises ? "bg-[var(--color-error)]/60" : "bg-[var(--color-success)]/60",
                )}
                style={
                  raises
                    ? { left: "50%", width: `${pct}%` }
                    : { right: "50%", width: `${pct}%` }
                }
              />
            </div>
            <span
              className={cn(
                "w-12 shrink-0 text-right tabular-nums",
                raises ? "text-[var(--color-error)]" : "text-[var(--color-success)]",
              )}
            >
              {raises ? "+" : ""}
              {d.contribution.toFixed(2)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
