import * as React from "react";
import { cn } from "../lib/cn";

export type CalloutTone = "default" | "accent" | "success" | "warning" | "info";

// Canonical clarifications for the two most-misread stats, shared by every
// surface that shows them (stats tabs, overview teaser, size-class hero).
export const NLOC_HINT =
  "Lines of code in the repo today: non-blank, non-comment lines, excluding lockfiles, generated and vendored files. Not a historical total — deleted code doesn't count.";
export const AGENT_PCT_HINT =
  "Share of commits with a verifiable agent signature: known bot identities, agent commit footers, or Co-authored-by agent trailers. Squash merges that drop trailers and agents committing under a human git identity can't be detected, so the true share may be higher.";

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
  /** Longer clarification shown as a native hover tooltip on the card. */
  hint?: string;
  icon?: React.ReactNode;
  tone?: CalloutTone;
  /** Wraps the callout in a link to the surface that explains the figure. */
  href?: string;
  LinkComponent?: React.ElementType<{
    href: string;
    className?: string;
    children: React.ReactNode;
  }>;
  className?: string;
}

/**
 * A single-figure callout card — the building block for the punchy "headline"
 * stats (AI authorship %, project age, defect-validation lift, …). Carries a
 * longer `sub` line than `MetricCard` but shares its type scale, so the stats
 * tabs read at the same weight as every other page.
 */
export function StatCallout({
  label,
  value,
  sub,
  hint,
  icon,
  tone = "default",
  href,
  LinkComponent = "a",
  className,
}: StatCalloutProps) {
  const card = (
    <div
      title={hint}
      className={cn(
        "h-full rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4",
        hint && "cursor-help",
        href && "transition-colors hover:border-[var(--color-border-hover)]",
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
          "mt-1.5 text-2xl font-bold leading-none tabular-nums",
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

  if (!href) return card;

  const Link = LinkComponent;
  return (
    <Link href={href} className="block h-full no-underline">
      {card}
    </Link>
  );
}
