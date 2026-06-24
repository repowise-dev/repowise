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

function fileParts(path: string): { name: string; dir: string } {
  const parts = path.split("/");
  const name = parts[parts.length - 1] ?? path;
  const dirParts = parts.slice(0, -1);
  const dir = dirParts.length <= 3 ? dirParts.join("/") : `…/${dirParts.slice(-3).join("/")}`;
  return { name, dir };
}

/**
 * A compact, clickable plan card — file first, with a type-colored rail so the
 * grid reads at a glance. Deliberately light: the heavy visual lives in the
 * modal that opens on click, so a long grid stays performant.
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
  const { name, dir } = fileParts(plan.file_path);

  return (
    <article
      data-refactoring-plan={plan.id}
      className={`group relative flex flex-col overflow-hidden rounded-2xl border bg-[var(--color-bg-surface)] transition-all ${
        highlighted
          ? "border-[var(--color-accent-primary)] ring-1 ring-[var(--color-accent-primary)]/30"
          : "border-[var(--color-border-default)] hover:border-[var(--color-border-strong)] hover:shadow-sm"
      }`}
    >
      {/* type-colored rail — the at-a-glance differentiator */}
      <span aria-hidden className="absolute inset-y-0 left-0 w-1" style={{ backgroundColor: accent }} />

      <button
        type="button"
        onClick={onOpen ? () => onOpen(plan) : undefined}
        className="flex flex-1 flex-col gap-2.5 py-4 pl-5 pr-4 text-left"
        disabled={!onOpen}
        aria-label={`Open ${meta.label} plan for ${plan.file_path}`}
      >
        {/* file first */}
        <div className="min-w-0">
          <h3
            className="truncate text-sm font-semibold text-[var(--color-text-primary)]"
            title={plan.file_path}
          >
            {name}
          </h3>
          {dir ? (
            <p className="truncate font-mono text-[11px] text-[var(--color-text-tertiary)]">{dir}</p>
          ) : null}
        </div>

        {/* type + one-line synopsis */}
        <div className="flex items-center gap-2">
          <span
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11px] font-semibold"
            style={{ backgroundColor: `color-mix(in srgb, ${accent} 14%, transparent)`, color: accent }}
          >
            <Icon className="h-3.5 w-3.5" />
            {meta.label}
          </span>
          <span className="min-w-0 flex-1 truncate text-xs text-[var(--color-text-secondary)]">
            {planSynopsis(plan)}
          </span>
        </div>

        {/* metric footer */}
        <div className="mt-auto flex items-center gap-x-3 gap-y-1.5 border-t border-[var(--color-border-default)] pt-3 text-[11px] text-[var(--color-text-tertiary)]">
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
          <span
            className="inline-flex items-center gap-1"
            title={`Detector confidence: ${CONFIDENCE_LABEL[confidence]}`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${CONFIDENCE_DOT[confidence]}`} />
            {CONFIDENCE_LABEL[confidence]}
          </span>
          {plan.impact_delta > 0 ? (
            <span
              className="ml-auto inline-flex items-center gap-1 rounded-full bg-[var(--color-success)]/10 px-2 py-0.5 font-semibold tabular-nums text-[var(--color-success)]"
              title="Health recovered if applied"
            >
              +{plan.impact_delta.toFixed(1)}
            </span>
          ) : null}
        </div>
      </button>

      {onAiPrompt ? (
        <div className="border-t border-[var(--color-border-default)] py-2 pl-5 pr-4">
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
