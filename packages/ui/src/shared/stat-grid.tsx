import * as React from "react";
import { cn } from "../lib/cn";

export interface StatGridProps {
  /** Column count at the widest breakpoint. Defaults to a responsive 4-up. */
  columns?: 2 | 3 | 4;
  className?: string;
  children?: React.ReactNode;
}

const COLUMN_CLASS: Record<NonNullable<StatGridProps["columns"]>, string> = {
  2: "grid-cols-1 sm:grid-cols-2",
  3: "grid-cols-1 sm:grid-cols-2 lg:grid-cols-3",
  4: "grid-cols-1 sm:grid-cols-2 lg:grid-cols-4",
};

/** Responsive grid wrapper for `StatTile`s. */
export function StatGrid({ columns = 4, className, children }: StatGridProps) {
  return (
    <div className={cn("grid gap-3", COLUMN_CLASS[columns], className)}>
      {children}
    </div>
  );
}

export interface StatTileProps {
  label: string;
  value: React.ReactNode;
  hint?: string;
  className?: string;
}

/** Compact label/value/hint cell — the consolidated inline-stat helper. */
export function StatTile({ label, value, hint, className }: StatTileProps) {
  return (
    <div
      className={cn(
        "rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3",
        className,
      )}
    >
      <p className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {label}
      </p>
      <p className="mt-1 text-lg font-semibold tabular-nums text-[var(--color-text-primary)]">
        {value}
      </p>
      {hint && (
        <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">{hint}</p>
      )}
    </div>
  );
}
