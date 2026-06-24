"use client";

import { Layers, Sparkles } from "lucide-react";
import { CONFIDENCE_DOT, CONFIDENCE_LABEL, EFFORT_LABEL, typeAccent, typeMeta } from "./meta";
import {
  blastCount,
  planSynopsis,
  type Confidence,
  type EffortBucket,
  type RefactoringPlan,
} from "./types";

export interface RefactoringPlanCardProps {
  plan: RefactoringPlan;
  /** Open the visual plan inspector for this plan. */
  onOpen?: ((plan: RefactoringPlan) => void) | undefined;
  /** Open the AI-prompt modal for this plan. */
  onAiPrompt?: ((plan: RefactoringPlan) => void) | undefined;
  /** Flash-highlight (e.g. after a quadrant dot click scrolled to it). */
  highlighted?: boolean;
}

function shortFile(path: string): string {
  const parts = path.split("/");
  return parts.length <= 3 ? path : `…/${parts.slice(-3).join("/")}`;
}

/**
 * A compact, clickable plan card. Deliberately light — the heavy visual lives
 * in the inspector that opens on click — so a long grid stays performant.
 */
export function RefactoringPlanCard({
  plan,
  onOpen,
  onAiPrompt,
  highlighted = false,
}: RefactoringPlanCardProps) {
  const meta = typeMeta(plan.refactoring_type);
  const accent = typeAccent(plan.refactoring_type);
  const { Icon } = meta;
  const blast = blastCount(plan);
  const effort = (plan.effort_bucket || "M") as EffortBucket;
  const confidence = (plan.confidence || "medium") as Confidence;

  return (
    <article
      data-refactoring-plan={plan.id}
      className={`group relative flex flex-col rounded-2xl border bg-[var(--color-bg-surface)] transition-all ${
        highlighted
          ? "border-[var(--color-accent-primary)] ring-1 ring-[var(--color-accent-primary)]/30"
          : "border-[var(--color-border-default)] hover:border-[var(--color-border-strong)] hover:shadow-sm"
      }`}
    >
      <button
        type="button"
        onClick={onOpen ? () => onOpen(plan) : undefined}
        className="flex flex-1 flex-col gap-3 p-4 text-left"
        disabled={!onOpen}
        aria-label={`Open ${meta.label} plan for ${plan.target_symbol}`}
      >
        <div className="flex items-start gap-3">
          <span
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg"
            style={{ backgroundColor: `color-mix(in srgb, ${accent} 14%, transparent)`, color: accent }}
          >
            <Icon className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-semibold text-[var(--color-text-primary)]">
                {meta.label}
              </span>
              <span
                className="inline-flex items-center gap-1 text-[10px] text-[var(--color-text-tertiary)]"
                title={`Detector confidence: ${CONFIDENCE_LABEL[confidence]}`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${CONFIDENCE_DOT[confidence]}`} />
                {CONFIDENCE_LABEL[confidence]}
              </span>
            </div>
            <p className="mt-0.5 truncate text-xs text-[var(--color-text-secondary)]">
              {planSynopsis(plan)}
            </p>
          </div>
        </div>

        {plan.target_symbol ? (
          <code className="block truncate text-[11px] text-[var(--color-text-secondary)]">
            {plan.target_symbol}
          </code>
        ) : null}
        <span
          className="truncate font-mono text-[11px] text-[var(--color-text-tertiary)]"
          title={plan.file_path}
        >
          {shortFile(plan.file_path)}
        </span>

        <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1.5 pt-1 text-[11px] text-[var(--color-text-tertiary)]">
          <span title={`Effort: ${EFFORT_LABEL[effort]}`} className="inline-flex items-center gap-1">
            <span className="rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5 font-medium tabular-nums text-[var(--color-text-secondary)]">
              {effort}
            </span>
          </span>
          {blast > 0 ? (
            <span className="inline-flex items-center gap-1" title="Files this refactoring touches">
              <Layers className="h-3.5 w-3.5" />
              {blast}
            </span>
          ) : null}
          {plan.impact_delta > 0 ? (
            <span className="tabular-nums" title="Health recovered if applied">
              +{plan.impact_delta.toFixed(1)}
            </span>
          ) : null}
        </div>
      </button>

      {onAiPrompt ? (
        <div className="border-t border-[var(--color-border-default)] px-4 py-2">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onAiPrompt(plan);
            }}
            className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11px] font-semibold text-[var(--color-accent-primary)] transition-colors hover:bg-[var(--color-accent-muted)]"
            title="Open a ready-to-paste prompt for your AI coding agent"
          >
            <Sparkles className="h-3.5 w-3.5" />
            AI prompt
          </button>
        </div>
      ) : null}
    </article>
  );
}
