import * as React from "react";
import { cn } from "../lib/cn";
import { Card, CardContent } from "../ui/card";

interface StatCardProps {
  label: string;
  value: string | number;
  description?: string;
  trend?: { value: string; positive: boolean };
  icon?: React.ReactNode;
  className?: string;
  href?: string;
  /** Tighter padding for dense stat strips. */
  dense?: boolean;
}

export function StatCard({
  label,
  value,
  description,
  trend,
  icon,
  className,
  href,
  dense,
}: StatCardProps) {
  const card = (
    <Card className={cn("h-full transition-colors hover:border-[var(--color-border-hover)]", href && "cursor-pointer", className)}>
      <CardContent className={dense ? "p-3" : "p-4"}>
        <div className="flex items-start justify-between">
          <div className="space-y-1">
            <p className="text-xs font-medium text-[var(--color-text-tertiary)] uppercase tracking-wider">
              {label}
            </p>
            <div className="flex items-baseline gap-2">
              <p className="text-2xl font-bold text-[var(--color-text-primary)] tabular-nums">
                {value}
              </p>
              {trend && (
                <span
                  className={cn(
                    "text-xs font-medium tabular-nums",
                    trend.positive
                      ? "text-[var(--color-success)]"
                      : "text-[var(--color-error)]",
                  )}
                >
                  {trend.positive ? "\u2191" : "\u2193"} {trend.value}
                </span>
              )}
            </div>
            {description && (
              <p className="text-xs text-[var(--color-text-secondary)]">{description}</p>
            )}
          </div>
          {icon && (
            <div className="rounded-md bg-[var(--color-bg-elevated)] p-2 text-[var(--color-text-secondary)]">
              {icon}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );

  if (href) {
    return (
      <a href={href} className="block h-full no-underline">
        {card}
      </a>
    );
  }

  return card;
}
