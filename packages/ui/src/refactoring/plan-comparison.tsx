"use client";

import { ArrowDown, ArrowRight } from "lucide-react";
import { PlanBefore } from "./plan-before";
import { PlanDetail } from "./plan-detail";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

export interface PlanComparisonProps {
  plan: RefactoringPlan;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
}

/**
 * The modal centerpiece: the problem today on the left, the proposed result on
 * the right, an arrow between. The "after" reuses the per-type plan visual; the
 * "before" is the synthesized problem picture. Stacks on narrow viewports.
 */
export function PlanComparison({ plan, fileHref }: PlanComparisonProps) {
  // Before and after are a sequence, and the layout already shows it: two
  // columns with an arrow between them, in reading order. Painting the first
  // red and the second in the type's hue spent two colours restating that, and
  // red in particular is reserved for a health band. Both halves are labelled
  // instead.
  const accent = "var(--color-accent-fill)";
  return (
    <div className="relative grid gap-3 md:grid-cols-2">
      {/* before */}
      <div className="rounded-2xl border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/40 p-4">
        <div className="mb-3">
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            Today
          </span>
        </div>
        <PlanBefore plan={plan} />
      </div>

      {/* desktop arrow — centered on the seam between the two columns */}
      <div
        className="pointer-events-none absolute left-1/2 top-1/2 z-10 hidden -translate-x-1/2 -translate-y-1/2 md:block"
        aria-hidden
      >
        <span
          className="flex h-9 w-9 items-center justify-center rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shadow-sm"
          style={{ color: accent }}
        >
          <ArrowRight className="h-4 w-4" />
        </span>
      </div>

      {/* mobile arrow — between the stacked cards */}
      <div className="flex justify-center md:hidden" aria-hidden>
        <span
          className="flex h-7 w-7 items-center justify-center rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shadow-sm"
          style={{ color: accent }}
        >
          <ArrowDown className="h-4 w-4" />
        </span>
      </div>

      {/* after */}
      <div className="rounded-2xl border border-[var(--color-border-default)] p-4">
        <div className="mb-3">
          <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-secondary)]">
            After
          </span>
        </div>
        <PlanDetail plan={plan} fileHref={fileHref} hideIntro />
      </div>
    </div>
  );
}
