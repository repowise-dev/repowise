import * as React from "react";
import { cn } from "../lib/cn";

export type CalloutTone = "default" | "accent" | "success" | "warning" | "info";

const TONE_VALUE: Record<CalloutTone, string> = {
  default: "text-[var(--color-text-primary)]",
  accent: "text-[var(--color-accent-primary)]",
  success: "text-[var(--color-success)]",
  warning: "text-[var(--color-warning)]",
  info: "text-[var(--color-info)]",
};

export interface StatCalloutProps {
  /** Small uppercase eyebrow above the figure. */
  label: string;
  /** The headline figure (already formatted). */
  value: React.ReactNode;
  /** One-line context shown under the figure. */
  sub?: React.ReactNode;
  icon?: React.ReactNode;
  tone?: CalloutTone;
  className?: string;
}

/**
 * A large, single-figure callout card — the building block for the punchy
 * "headline" stats (AI authorship %, project age, defect-validation lift, …).
 * Bigger and more emphatic than `StatTile`, tuned for the showcase tabs.
 */
export function StatCallout({
  label,
  value,
  sub,
  icon,
  tone = "default",
  className,
}: StatCalloutProps) {
  return (
    <div
      className={cn(
        "rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          {label}
        </p>
        {icon && <span className="text-[var(--color-text-tertiary)]">{icon}</span>}
      </div>
      <p
        className={cn(
          "mt-1.5 text-3xl font-bold leading-none tabular-nums",
          TONE_VALUE[tone],
        )}
      >
        {value}
      </p>
      {sub && (
        <p className="mt-2 text-xs leading-snug text-[var(--color-text-secondary)]">{sub}</p>
      )}
    </div>
  );
}
