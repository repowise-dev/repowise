"use client";

/**
 * The full plan list, as hairline rows.
 *
 * This replaces a grid of `RefactoringPlanCard`s. Each card carried a
 * type-coloured rail, a tinted type chip, a bordered metric footer and a second
 * bordered strip holding its own AI-prompt button — four borders and two click
 * targets per plan, sixty at a time. Cards also forced three truncations each
 * (filename, directory, synopsis), which is a layout decision reported to the
 * reader as missing content.
 *
 * Two marks that are now conditional rather than universal, per "mark only what
 * needs attention": confidence renders only when it is not high (medium is 66%
 * of plans and high is the rest, so a dot on every row separated nothing), and
 * recovered health renders only when there is any (1,399 of 1,819 plans recover
 * under half a point).
 */

import * as React from "react";

import { formatNumber } from "../lib/format";
import { typeMeta } from "./meta";
import { blastCount, planSynopsis } from "./types";
import type { RefactoringPlan } from "@repowise-dev/types/refactoring";

export interface PlanRowsProps {
  plans: RefactoringPlan[];
  onOpen?: ((plan: RefactoringPlan) => void) | undefined;
  /** The overflow verb: hand this plan to a coding agent. */
  onAiPrompt?: ((plan: RefactoringPlan) => void) | undefined;
  /** Link to the file, used by the overflow menu. */
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
  /** Lit from the map above, when a row is also plotted. */
  highlightedId?: string | null | undefined;
}

const EFFORT_WORD: Record<string, string> = {
  S: "Small",
  M: "Medium",
  L: "Large",
  XL: "Extra large",
};

export function PlanRows({ plans, onOpen, onAiPrompt, fileHref, highlightedId }: PlanRowsProps) {
  return (
    <div className="flex flex-col">
      {plans.map((plan) => (
        <PlanRow
          key={plan.id}
          plan={plan}
          onOpen={onOpen}
          onAiPrompt={onAiPrompt}
          fileHref={fileHref}
          lit={plan.id === highlightedId}
        />
      ))}
    </div>
  );
}

function PlanRow({
  plan,
  onOpen,
  onAiPrompt,
  fileHref,
  lit,
}: {
  plan: RefactoringPlan;
  onOpen?: ((plan: RefactoringPlan) => void) | undefined;
  onAiPrompt?: ((plan: RefactoringPlan) => void) | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
  lit: boolean;
}) {
  const meta = typeMeta(plan.refactoring_type);
  const effort = EFFORT_WORD[plan.effort_bucket || "M"] ?? plan.effort_bucket;
  const blast = blastCount(plan);
  const gain = plan.impact_delta;
  const href = fileHref?.(plan.file_path, plan.line_start);

  return (
    <div
      data-refactoring-plan={plan.id}
      className={`grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-5 gap-y-2 border-t border-[var(--color-border-default)] px-3 py-3 lg:grid-cols-[128px_minmax(0,1fr)_170px_120px_32px] lg:items-center ${
        lit ? "bg-[var(--color-accent-muted)]" : "hover:bg-[var(--color-bg-elevated)]"
      }`}
    >
      <div className="order-2 text-xs text-[var(--color-text-secondary)] lg:order-none">
        {meta.label}
      </div>

      <button
        type="button"
        onClick={onOpen ? () => onOpen(plan) : undefined}
        disabled={!onOpen}
        className="group order-1 col-span-2 min-w-0 text-left lg:order-none lg:col-span-1"
      >
        <span className="block break-words font-mono text-[13.5px] font-medium text-[var(--color-text-primary)] group-hover:text-[var(--color-accent-primary)]">
          {plan.target_symbol || planSynopsis(plan) || meta.label}
        </span>
        <span className="mt-0.5 block break-all font-mono text-[11.5px] text-[var(--color-text-tertiary)]">
          {plan.file_path}
        </span>
      </button>

      <div className="order-3 text-[12.5px] text-[var(--color-text-secondary)] lg:order-none">
        {effort}
        {blast > 0 ? `, ${formatNumber(blast)} file${blast === 1 ? "" : "s"}` : ""}
        {plan.confidence !== "high" ? (
          <span className="ml-2 inline-flex items-center gap-1.5 text-[11.5px] text-[var(--color-caution)]">
            <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />
            {plan.confidence === "low" ? "low" : "medium"} confidence
          </span>
        ) : null}
      </div>

      <div className="order-4 text-[12.5px] tabular-nums lg:order-none">
        {plan.refactoring_type === "performance_fix" && (plan.benefit ?? 0) > 0 ? (
          <span className="font-medium text-[var(--color-success)]">
            {plan.benefit?.toFixed(1)} benefit
          </span>
        ) : gain > 0 ? (
          <span className="font-medium text-[var(--color-success)]">+{gain.toFixed(1)} health</span>
        ) : (
          <span className="text-[var(--color-text-tertiary)]">no score change</span>
        )}
      </div>

      <div className="order-5 justify-self-end lg:order-none">
        <RowOverflow plan={plan} onAiPrompt={onAiPrompt} href={href} />
      </div>
    </div>
  );
}

/**
 * The row's second-order verbs.
 *
 * Opening the plan is the row itself. Everything else lives here so a 1,819-row
 * list is not 1,819 clusters of controls the reader has to parse before they can
 * read a row — and so each verb can have its real name rather than an
 * abbreviation that only expands for someone who already knows the model.
 */
function RowOverflow({
  plan,
  onAiPrompt,
  href,
}: {
  plan: RefactoringPlan;
  onAiPrompt?: ((plan: RefactoringPlan) => void) | undefined;
  href?: string | undefined;
}) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement | null>(null);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!onAiPrompt && !href) return null;

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`More actions for ${plan.file_path}`}
        onClick={() => setOpen((v) => !v)}
        className="rounded-md px-1.5 py-1 leading-none text-[var(--color-text-tertiary)] transition-colors hover:bg-[var(--color-bg-inset)] hover:text-[var(--color-text-primary)]"
      >
        <span aria-hidden>&#183;&#183;&#183;</span>
      </button>
      {open ? (
        <div
          role="menu"
          className="absolute right-0 top-full z-20 mt-1 w-56 overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] py-1 shadow-md"
        >
          {onAiPrompt ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onAiPrompt(plan);
              }}
              className="block w-full px-3 py-2 text-left text-[13px] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
            >
              Copy prompt for an agent
            </button>
          ) : null}
          {href ? (
            <a
              href={href}
              role="menuitem"
              className="block px-3 py-2 text-[13px] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
            >
              Open file
            </a>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
