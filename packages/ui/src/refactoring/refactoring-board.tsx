"use client";

import { useCallback, useEffect, useState } from "react";
import { Wrench } from "lucide-react";
import { RefactoringPlanCard } from "./refactoring-plan-card";
import { RefactoringQuadrant } from "./refactoring-quadrant";
import { RefactoringInspector } from "./refactoring-inspector";
import { typeAccent, typeMeta, TYPE_ORDER } from "./meta";
import type { RefactoringPlan, RefactoringSummary } from "./types";

const PAGE_SIZE = 60;

export interface RefactoringBoardProps {
  plans: RefactoringPlan[];
  summary?: RefactoringSummary;
  /** Open the AI-prompt modal for a plan (host owns the modal + flavor). */
  onAiPrompt?: ((plan: RefactoringPlan) => void) | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
  /** Show the priority×effort quadrant centerpiece (default true). */
  showQuadrant?: boolean;
  emptyTitle?: string;
  emptyHint?: string;
}

/**
 * The Refactoring surface. The priority×effort quadrant is the full-width
 * centerpiece; a calm grid of compact plan cards sits below it. Clicking the
 * quadrant or a card opens the visual inspector for that one plan. No table.
 */
export function RefactoringBoard({
  plans,
  summary,
  onAiPrompt,
  fileHref,
  showQuadrant = true,
  emptyTitle = "No refactoring plans",
  emptyHint = "Nothing crosses the precision bar for this repo yet. Plans appear here when a class is worth splitting, a clone worth extracting, a method worth moving, or a cycle worth cutting.",
}: RefactoringBoardProps) {
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [selected, setSelected] = useState<RefactoringPlan | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  // Render the top-ranked cards first and grow on demand — a repo can surface
  // hundreds of plans and mounting them all at once would jank the grid.
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);

  // Reset the window whenever the (already rank-ordered) plan set changes,
  // e.g. on a type-filter switch, so the top is always shown first.
  useEffect(() => {
    setVisibleCount(PAGE_SIZE);
  }, [plans]);

  const openPlan = useCallback((plan: RefactoringPlan) => {
    setSelected(plan);
    setInspectorOpen(true);
  }, []);

  const pickFromQuadrant = useCallback(
    (plan: RefactoringPlan) => {
      setHighlighted(plan.id);
      openPlan(plan);
      window.setTimeout(() => setHighlighted((cur) => (cur === plan.id ? null : cur)), 2200);
    },
    [openPlan],
  );

  if (plans.length === 0) {
    return (
      <div className="mx-auto flex max-w-md flex-col items-center justify-center rounded-2xl border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-8 py-16 text-center">
        <span className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--color-bg-elevated)] text-[var(--color-text-tertiary)]">
          <Wrench className="h-6 w-6" />
        </span>
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{emptyTitle}</h3>
        <p className="mt-1.5 text-sm leading-relaxed text-[var(--color-text-tertiary)]">{emptyHint}</p>
      </div>
    );
  }

  const counts = new Map((summary?.by_type ?? []).map((c) => [c.type, c.count]));

  return (
    <div className="space-y-8">
      {showQuadrant ? (
        <section className="space-y-3">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-[var(--color-text-primary)]">
                Priority × effort
              </h2>
              <p className="mt-0.5 max-w-2xl text-sm text-[var(--color-text-secondary)]">
                Ranked by leverage — how depended-upon the file is, how much rides along, and the
                health recovered. The top-left is where to start.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {TYPE_ORDER.filter((t) => (counts.get(t) ?? 0) > 0).map((t) => {
                const meta = typeMeta(t);
                const { Icon } = meta;
                return (
                  <span
                    key={t}
                    className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)]"
                  >
                    <Icon className="h-3.5 w-3.5" style={{ color: typeAccent(t) }} />
                    {meta.label}
                    <span className="tabular-nums text-[var(--color-text-tertiary)]">{counts.get(t)}</span>
                  </span>
                );
              })}
            </div>
          </div>
          <RefactoringQuadrant plans={plans} onSelect={pickFromQuadrant} height={400} />
        </section>
      ) : null}

      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
          {plans.length} plan{plans.length === 1 ? "" : "s"}
        </h3>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {plans.slice(0, visibleCount).map((plan) => (
            <RefactoringPlanCard
              key={plan.id}
              plan={plan}
              onOpen={openPlan}
              onAiPrompt={onAiPrompt}
              highlighted={highlighted === plan.id}
            />
          ))}
        </div>
        {plans.length > visibleCount ? (
          <div className="flex justify-center pt-1">
            <button
              type="button"
              onClick={() => setVisibleCount((n) => n + PAGE_SIZE)}
              className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-4 py-2 text-sm font-medium text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-border-strong)] hover:text-[var(--color-text-primary)]"
            >
              Show {Math.min(PAGE_SIZE, plans.length - visibleCount)} more
              <span className="text-[var(--color-text-tertiary)]">
                · {plans.length - visibleCount} left
              </span>
            </button>
          </div>
        ) : null}
      </section>

      <RefactoringInspector
        plan={selected}
        open={inspectorOpen}
        onOpenChange={setInspectorOpen}
        onAiPrompt={onAiPrompt}
        fileHref={fileHref}
      />
    </div>
  );
}
