"use client";

import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

import {
  extractMethodPlan,
  extractMethodStages,
  extractOrchestrator,
  formatSpans,
  stagedLineCount,
} from "./types";

/**
 * "Today" for an Extract Method plan: the function, its range and the blocks
 * that move. The drawer's "after" hides its intro, so without this the half
 * stayed empty.
 */
export function ExtractMethodBefore({ plan }: { plan: RefactoringPlan }) {
  const stages = extractMethodStages(plan);
  const em = extractMethodPlan(plan);
  const spans = stages.length >= 2 ? stages.map((s) => s.span) : em.span ? [em.span] : [];
  const orchestrator = extractOrchestrator(plan);
  const lines = stagedLineCount(plan);
  const name = plan.target_symbol || (plan.file_path.split("/").pop() ?? plan.file_path);
  return (
    <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-3.5">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1">
        <code className="min-w-0 break-all text-xs font-semibold text-[var(--color-text-primary)]">
          {name}
        </code>
        {plan.line_start && plan.line_end ? (
          <span className="shrink-0 font-mono text-2xs tabular-nums text-[var(--color-text-tertiary)]">
            lines {plan.line_start}–{plan.line_end}
            {orchestrator ? ` · CCN ${orchestrator.before}` : ""}
          </span>
        ) : null}
      </div>
      {spans.length > 0 ? (
        <p className="text-2xs leading-relaxed text-[var(--color-text-tertiary)]">
          {spans.length === 1
            ? "One block inside it does a separable job: "
            : `${spans.length} blocks inside it each do a separable job${
                lines ? `, ${lines} lines in all` : ""
              }: `}
          <span className="font-mono tabular-nums text-[var(--color-text-secondary)]">
            {formatSpans(spans)}
          </span>
          .
        </p>
      ) : null}
    </div>
  );
}
