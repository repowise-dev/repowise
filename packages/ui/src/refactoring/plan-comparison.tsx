"use client";

import { ArrowDown, ArrowRight, Check } from "lucide-react";
import { typeAccent } from "./meta";
import { PlanBefore } from "./plan-before";
import { PlanDetail } from "./plan-detail";
import type { RefactoringPlan } from "./types";

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
  const accent = typeAccent(plan.refactoring_type);
  return (
    <div className="relative grid gap-3 md:grid-cols-2">
      {/* before */}
      <div className="rounded-2xl border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/40 p-4">
        <div className="mb-3 flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-[var(--color-error)]" aria-hidden />
          <span className="text-xs font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
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
      <div
        className="rounded-2xl border p-4"
        style={{
          borderColor: `color-mix(in srgb, ${accent} 40%, transparent)`,
          backgroundColor: `color-mix(in srgb, ${accent} 5%, transparent)`,
        }}
      >
        <div className="mb-3 flex items-center gap-1.5">
          <Check className="h-3.5 w-3.5" style={{ color: accent }} />
          <span className="text-xs font-semibold uppercase tracking-wide" style={{ color: accent }}>
            After
          </span>
        </div>
        <PlanDetail plan={plan} fileHref={fileHref} hideIntro />
      </div>
    </div>
  );
}
