import * as React from "react";
import { cn } from "../lib/cn";
import { Card, CardContent } from "../ui/card";

export interface MetricCardProps {
  label: string;
  value: React.ReactNode;
  /**
   * Signed change indicator. `positive` is a good-vs-bad flag (drives
   * success/error colouring), not up-vs-down. Set `neutral` to render the
   * change uncolored for metrics where growth carries no value judgement.
   */
  delta?: { value: string; positive: boolean; neutral?: boolean };
  /** Optional inline sparkline node, rendered beneath the value. */
  sparkline?: React.ReactNode;
  /** Optional distribution bar node, rendered beneath the value. */
  distBar?: React.ReactNode;
  icon?: React.ReactNode;
  /** Wraps the tile in a link. */
  href?: string;
  LinkComponent?: React.ElementType<{
    href: string;
    className?: string;
    children: React.ReactNode;
  }>;
  className?: string;
}

/**
 * The canonical stat tile: label + value with optional delta, sparkline, and
 * distribution-bar slots. Built on `Card`; the future single replacement for
 * the near-identical summary-card variants across views.
 */
export function MetricCard({
  label,
  value,
  delta,
  sparkline,
  distBar,
  icon,
  href,
  LinkComponent = "a",
  className,
}: MetricCardProps) {
  const card = (
    <Card
      className={cn(
        "h-full transition-colors hover:border-[var(--color-border-hover)]",
        href && "cursor-pointer",
        className,
      )}
    >
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 space-y-1">
            <p className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
              {label}
            </p>
            <div className="flex items-baseline gap-2">
              <div className="text-2xl font-bold tabular-nums text-[var(--color-text-primary)]">
                {value}
              </div>
              {delta && (
                <span
                  className={cn(
                    "text-xs font-medium tabular-nums",
                    delta.neutral
                      ? "text-[var(--color-text-tertiary)]"
                      : delta.positive
                        ? "text-[var(--color-success)]"
                        : "text-[var(--color-error)]",
                  )}
                >
                  {(delta.neutral ? "" : delta.positive ? "↑ " : "↓ ") + delta.value}
                </span>
              )}
            </div>
          </div>
          {icon && (
            <div className="shrink-0 rounded-md bg-[var(--color-bg-elevated)] p-2 text-[var(--color-text-secondary)]">
              {icon}
            </div>
          )}
        </div>
        {sparkline && <div className="mt-3">{sparkline}</div>}
        {distBar && <div className="mt-3">{distBar}</div>}
      </CardContent>
    </Card>
  );

  if (href) {
    const Link = LinkComponent;
    return (
      <Link href={href} className="block h-full no-underline">
        {card}
      </Link>
    );
  }

  return card;
}
