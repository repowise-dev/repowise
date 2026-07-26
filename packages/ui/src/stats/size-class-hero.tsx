import * as React from "react";
import {
  Sprout,
  Home,
  Building,
  Building2,
  Landmark,
  Globe2,
} from "lucide-react";
import type { StatsScale } from "@repowise-dev/types/stats";

const SIZE_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  Seedling: Sprout,
  Hamlet: Home,
  Village: Home,
  Town: Building,
  City: Building2,
  Metropolis: Landmark,
  Megalopolis: Globe2,
};

interface SizeClassHeroProps {
  scale: StatsScale;
  /** Repo name, rendered as the eyebrow above the size-class label. */
  repoName?: string;
}

/**
 * The Stats page's opening statement: a warm-washed banner naming the
 * codebase's "size class" (derived from NLOC).
 *
 * Identity only. The headline figures used to sit here as four bordered tiles,
 * which put a boxed number immediately above the hairline ribbon carrying the
 * same four numbers — the ribbon owns them now, and the hero says the one thing
 * a count can't: what kind of place this is.
 */
export function SizeClassHero({ scale, repoName }: SizeClassHeroProps) {
  const Icon = SIZE_ICON[scale.size_class.name] ?? Building2;

  return (
    <div
      className="relative overflow-hidden rounded-2xl border border-[var(--color-border-default)] p-6 sm:p-8"
      style={{ background: "var(--gradient-warm-wash, var(--color-bg-surface))" }}
    >
      <div className="min-w-0">
        {repoName && (
          <p className="text-xs font-medium uppercase tracking-[0.2em] text-[var(--color-text-tertiary)]">
            {repoName}
          </p>
        )}
        <div className="mt-2 flex items-center gap-3">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-[var(--color-accent-muted,var(--color-bg-elevated))] text-[var(--color-accent-primary)]">
            <Icon className="h-6 w-6" />
          </span>
          <div className="min-w-0">
            <p className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
              This codebase is a
            </p>
            <h2 className="text-2xl font-bold leading-tight text-[var(--color-text-primary)] sm:text-3xl">
              {scale.size_class.name}
            </h2>
          </div>
        </div>
        <p className="mt-3 max-w-md text-sm text-[var(--color-text-secondary)]">
          {scale.size_class.blurb}
        </p>
      </div>
    </div>
  );
}
