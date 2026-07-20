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
import { formatCompact, formatNumber, formatLOC } from "../lib/format";
import { NLOC_HINT } from "./stat-callout";

const SIZE_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  Seedling: Sprout,
  Hamlet: Home,
  Village: Home,
  Town: Building,
  City: Building2,
  Metropolis: Landmark,
  Megalopolis: Globe2,
};

interface HeroFigure {
  label: string;
  value: string;
  /** Longer clarification shown as a native hover tooltip. */
  hint?: string;
}

interface SizeClassHeroProps {
  scale: StatsScale;
  /** Repo name, rendered as the eyebrow above the size-class label. */
  repoName?: string;
}

/**
 * The Stats page centerpiece: a warm-washed banner that names the codebase's
 * "size class" (derived from NLOC) and leads with the headline figures. Reads
 * as the centerpiece through spacing, the wash, and one accent — not through
 * outsized type, which would put it out of step with every other page.
 */
export function SizeClassHero({ scale, repoName }: SizeClassHeroProps) {
  const Icon = SIZE_ICON[scale.size_class.name] ?? Building2;

  const figures: HeroFigure[] = [
    { label: "Lines of code", value: formatLOC(scale.total_nloc), hint: NLOC_HINT },
    { label: "Files", value: formatCompact(scale.file_count) },
    { label: "Symbols", value: formatCompact(scale.symbol_count) },
    { label: "Languages", value: formatNumber(scale.language_count) },
  ];

  return (
    <div
      className="relative overflow-hidden rounded-2xl border border-[var(--color-border-default)] p-6 sm:p-8"
      style={{ background: "var(--gradient-warm-wash, var(--color-bg-surface))" }}
    >
      {/* Side by side once there is room, but wrapping rather than letting the
          figures crowd the size-class name — the figure tiles refuse to shrink,
          so the row needs somewhere to break. */}
      <div className="flex flex-col gap-6 lg:flex-row lg:flex-wrap lg:items-center lg:justify-between">
        <div className="min-w-0 lg:basis-80 lg:grow">
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

        {/* Columns floor at their content width so a six-character figure
            ("485.3K") is never clipped to an ellipsis; they still share the
            leftover width equally, and the group refuses to be squeezed by the
            blurb beside it. */}
        <div className="grid grid-cols-[repeat(2,minmax(max-content,1fr))] gap-3 sm:grid-cols-[repeat(4,minmax(max-content,1fr))] lg:shrink-0">
          {figures.map((f) => (
            <div
              key={f.label}
              title={f.hint}
              className={`rounded-xl border border-[var(--color-border-subtle,var(--color-border-default))] bg-[var(--color-bg-surface)]/70 px-4 py-3 backdrop-blur-sm${f.hint ? " cursor-help" : ""}`}
            >
              <p className="whitespace-nowrap text-2xl font-bold tabular-nums text-[var(--color-text-primary)]">
                {f.value}
              </p>
              <p className="mt-0.5 text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
                {f.label}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
