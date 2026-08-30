"use client";

/**
 * The opportunity inspector, as a right-hand drawer.
 *
 * The single-plan drawer next door still exists and is still the right surface
 * for one step, which is what a deep link from the performance queue and the
 * VS Code webview both carry. This one is the composed unit: a file's whole
 * refactoring, its steps in dependency-safe order, and the evidence they rest
 * on.
 *
 * Three things it says that a list of plans could not:
 *
 * - **Order matters, and one kind of step invalidates the next.** A step
 *   carrying `relocated_by` names an earlier step that moves its symbol to
 *   another file, so its own file and span describe where the symbol *was*.
 *   That is stated once at the top of the list and again on each affected step,
 *   because a reader who scrolls straight to step four would otherwise follow a
 *   line number into the wrong file.
 * - **Mechanical is not a synonym for safe-to-automate-later.** Each step says
 *   which it is and why, and names what the layer could not establish rather
 *   than leaving an unknown looking like a cleared check.
 * - **Evidence is not instruction.** Demoted clone groups and triggering
 *   findings sit under their own heading, worded as observations.
 */

import * as React from "react";
import { Layers, Sparkles, Terminal } from "lucide-react";

import { Sheet, SheetContent, SheetTitle } from "../ui/sheet";
import { Skeleton, SkeletonRegion } from "../ui/skeleton";
import { formatNumber } from "../lib/format";
import { ValidationSummary } from "./validation-summary";
import { CONFIDENCE_LABEL, EFFORT_LABEL, typeMeta } from "./meta";
import {
  ORDERING_NOTE,
  STATUS_LABEL,
  TRIAGE_STATUSES,
  addressesPrimaryLabel,
  humanizeBiomarker,
  isRelocated,
} from "./opportunity";
import type {
  OpportunityStatus,
  OpportunityStep,
  RefactoringOpportunityDetail,
  RefactoringOpportunityDetailResolved,
} from "@repowise-dev/types/refactoring";

/** The drill-down an agent should call. Same shape the performance drawer uses,
 *  because it is the same agent surface. */
export function opportunityHandoffCall(opportunityId: string): string {
  return `get_health(opportunity_id="${opportunityId}")`;
}

export interface OpportunityDrawerProps {
  detail: RefactoringOpportunityDetail | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  loading?: boolean | undefined;
  error?: string | undefined;
  onAiPrompt?: ((detail: RefactoringOpportunityDetailResolved) => void) | undefined;
  onStatusChange?:
    | ((
        detail: RefactoringOpportunityDetailResolved,
        status: OpportunityStatus,
      ) => Promise<void> | void)
    | undefined;
  /** Open one step as a single plan, in the plan inspector. */
  onOpenStep?: ((planId: string) => void) | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
}

export function OpportunityDrawer({
  detail,
  open,
  onOpenChange,
  loading = false,
  error,
  onAiPrompt,
  onStatusChange,
  onOpenStep,
  fileHref,
}: OpportunityDrawerProps) {
  const resolved = detail?.resolved ? detail : null;
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        closeLabel="Close opportunity"
        className="w-full max-w-[680px] sm:w-[92vw]"
      >
        {resolved ? (
          <DrawerBody
            detail={resolved}
            onAiPrompt={onAiPrompt}
            onStatusChange={onStatusChange}
            onOpenStep={onOpenStep}
            fileHref={fileHref}
          />
        ) : loading ? (
          <>
            <SheetTitle className="border-b border-[var(--color-border-default)] px-5 py-4 pr-12 text-[15px]">
              Loading opportunity
            </SheetTitle>
            <SkeletonRegion className="space-y-4 px-5 py-5" label="Loading opportunity">
              <Skeleton className="h-16 rounded-none" />
              <Skeleton className="h-40 rounded-none" />
            </SkeletonRegion>
          </>
        ) : detail && !detail.resolved ? (
          <>
            <SheetTitle className="border-b border-[var(--color-border-default)] px-5 py-4 pr-12 text-[15px]">
              Opportunity unavailable
            </SheetTitle>
            <div className="space-y-2 px-5 py-5 text-sm text-[var(--color-text-secondary)]">
              <p>
                Nothing resolves for{" "}
                <span className="break-all font-mono text-xs">{detail.opportunity_id}</span>.
              </p>
              {detail.model_state?.state === "stale_model" ? (
                <p>
                  It was written by an older analysis model. Re-index the repository and open it
                  again from the list.
                </p>
              ) : (
                <p>It may have been resolved by a later analysis, or it was never minted here.</p>
              )}
            </div>
          </>
        ) : error ? (
          <>
            <SheetTitle className="border-b border-[var(--color-border-default)] px-5 py-4 pr-12 text-[15px]">
              Opportunity unavailable
            </SheetTitle>
            <p className="px-5 py-5 text-sm text-[var(--color-text-secondary)]">{error}</p>
          </>
        ) : (
          <SheetTitle className="sr-only">Refactoring opportunity</SheetTitle>
        )}
      </SheetContent>
    </Sheet>
  );
}

function DrawerBody({
  detail,
  onAiPrompt,
  onStatusChange,
  onOpenStep,
  fileHref,
}: {
  detail: RefactoringOpportunityDetailResolved;
  onAiPrompt?: ((detail: RefactoringOpportunityDetailResolved) => void) | undefined;
  onStatusChange?:
    | ((
        detail: RefactoringOpportunityDetailResolved,
        status: OpportunityStatus,
      ) => Promise<void> | void)
    | undefined;
  onOpenStep?: ((planId: string) => void) | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
}) {
  const meta = typeMeta(detail.lead_refactoring_type || "");
  const name = detail.file_path.split("/").pop() ?? detail.file_path;
  const others = detail.affected_files.filter((f) => f !== detail.file_path);
  const anyRelocated = detail.steps.some(isRelocated);

  const [pending, setPending] = React.useState<OpportunityStatus | null>(null);
  const [failed, setFailed] = React.useState(false);
  const status = pending ?? detail.status;

  React.useEffect(() => {
    setPending(null);
  }, [detail.status]);

  const setStatus = React.useCallback(
    async (next: OpportunityStatus) => {
      if (!onStatusChange) return;
      setPending(next);
      setFailed(false);
      try {
        await onStatusChange(detail, next);
      } catch {
        setPending(null);
        setFailed(true);
      }
    },
    [onStatusChange, detail],
  );

  const facts: string[] = [
    `${EFFORT_LABEL[detail.effort_bucket]} effort`,
    `${CONFIDENCE_LABEL[detail.confidence]} confidence`,
    `${detail.step_count} step${detail.step_count === 1 ? "" : "s"}, ${detail.mechanical_steps} mechanical`,
  ];
  if (detail.affected_files_total > 1) {
    facts.push(`${formatNumber(detail.affected_files_total)} files affected`);
  }

  return (
    <>
      <div className="border-b border-[var(--color-border-default)] px-5 py-4 pr-12">
        <div className="text-[11px] text-[var(--color-text-secondary)]">
          {meta.label}
          {detail.lead_biomarker ? ` · against ${humanizeBiomarker(detail.lead_biomarker)}` : ""}
        </div>
        <SheetTitle className="mt-0.5 break-words font-mono text-[15px] font-semibold text-[var(--color-text-primary)]">
          {name}
        </SheetTitle>
        <p className="mt-1 break-all font-mono text-[11.5px] text-[var(--color-text-tertiary)]">
          {detail.file_path}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-[var(--color-border-default)] px-5 py-2.5 text-[12.5px] text-[var(--color-text-secondary)]">
        {facts.map((f) => (
          <span key={f}>{f}</span>
        ))}
        {detail.recoverable_health > 0 ? (
          <span className="font-medium tabular-nums text-[var(--color-success)]">
            +{detail.recoverable_health.toFixed(1)} health
          </span>
        ) : (
          <span className="text-[var(--color-text-tertiary)]">No score change</span>
        )}
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto px-5 py-5">
        {/* Tri-state, stated in words. `null` is its own sentence: the layer had
            no dominant finding to compare against, which is not the same claim
            as "these steps address something else". */}
        <p className="text-sm leading-relaxed text-[var(--color-text-secondary)]">
          {addressesPrimaryLabel(detail.addresses_primary_problem)}.{" "}
          {detail.addresses_primary_problem === false
            ? "The steps below are still real work; they are just not what is costing this file the most."
            : meta.blurb}
        </p>

        {onStatusChange ? (
          <section>
            <h4 className="mb-2 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              Triage
            </h4>
            <div role="radiogroup" aria-label="Triage this opportunity" className="flex flex-wrap gap-1.5">
              {TRIAGE_STATUSES.map((option) => {
                const current = option.value === status;
                return (
                  <button
                    key={option.value}
                    type="button"
                    role="radio"
                    aria-checked={current}
                    disabled={current}
                    onClick={() => void setStatus(option.value)}
                    className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                      current
                        ? "bg-[var(--color-accent-muted)] text-[var(--color-accent-primary)]"
                        : "text-[var(--color-text-tertiary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)]"
                    }`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
            <p className="mt-1.5 text-[11.5px] text-[var(--color-text-tertiary)]">
              Applies to all {detail.step_count} step{detail.step_count === 1 ? "" : "s"}.{" "}
              {status === "false_positive"
                ? "A false positive is suppressed on every later analysis."
                : "Currently " + STATUS_LABEL[status].toLowerCase() + "."}
            </p>
            <p
              role="status"
              className={
                failed ? "mt-1 text-[11.5px] text-[var(--color-error)]" : "sr-only"
              }
            >
              {failed ? "Could not save that. The opportunity is unchanged." : ""}
            </p>
          </section>
        ) : null}

        <section>
          <h4 className="mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            The steps, in order
          </h4>
          {anyRelocated ? (
            <p className="mb-3 rounded-md border border-[var(--color-caution)]/40 bg-[var(--color-caution)]/10 px-3 py-2 text-[12.5px] text-[var(--color-text-secondary)]">
              {detail.ordering_note ?? ORDERING_NOTE}
            </p>
          ) : null}
          <ol className="space-y-3">
            {detail.steps.map((step, i) => (
              <StepCard
                key={step.plan_id}
                step={step}
                index={i}
                onOpenStep={onOpenStep}
                fileHref={fileHref}
              />
            ))}
          </ol>
          {detail.steps_emitted < detail.steps_total ? (
            <p className="mt-2 text-[11.5px] text-[var(--color-text-tertiary)]">
              Showing {detail.steps_emitted} of {detail.steps_total} steps.
            </p>
          ) : null}
        </section>

        {detail.validation_profiles.length > 0 ? (
          <section>
            <h4 className="mb-3 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              Validation
            </h4>
            <div className="space-y-4">
              {detail.validation_profiles.map((profile) => (
                <ValidationSummary
                  key={profile.id}
                  validation={profile}
                  fileHref={(path) => fileHref?.(path, null)}
                />
              ))}
            </div>
          </section>
        ) : null}

        {detail.evidence.length > 0 ? (
          <section>
            <h4 className="mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              Evidence
            </h4>
            {/* Named as observation, not instruction. These are mostly demoted
                clone groups: real duplication, not a change worth making on its
                own account. */}
            <p className="mb-2.5 text-[12.5px] text-[var(--color-text-secondary)]">
              Supporting observations on this file. They are why the diagnosis reads the way it
              does, not extra work to do.
            </p>
            <ul className="space-y-1.5">
              {detail.evidence.map((item) => (
                <li
                  key={item.plan_id}
                  className="border-t border-[var(--color-border-default)] pt-1.5 text-[12.5px] text-[var(--color-text-secondary)]"
                >
                  <span className="text-[var(--color-text-tertiary)]">
                    {typeMeta(item.refactoring_type).label}
                  </span>
                  {item.target_symbol ? (
                    <span className="ml-2 break-all font-mono text-[12px] text-[var(--color-text-primary)]">
                      {item.target_symbol}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
            {detail.evidence_truncated ? (
              <p className="mt-2 text-[11.5px] text-[var(--color-text-tertiary)]">
                Showing {detail.evidence_emitted} of {detail.evidence_total} observations.
              </p>
            ) : null}
          </section>
        ) : null}

        {others.length > 0 ? (
          <section>
            <h4 className="mb-2 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              <Layers className="h-3.5 w-3.5" />
              Also affected
            </h4>
            <ul className="space-y-1">
              {others.map((f) => {
                const href = fileHref?.(f, null);
                return (
                  <li key={f}>
                    {href ? (
                      <a
                        href={href}
                        className="break-all font-mono text-xs text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-accent-primary)] hover:underline"
                      >
                        {f}
                      </a>
                    ) : (
                      <span className="break-all font-mono text-xs text-[var(--color-text-secondary)]">
                        {f}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}

        <section>
          <h4 className="mb-2 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            <Terminal className="h-3.5 w-3.5" />
            Ask for this by id
          </h4>
          <p className="text-[12.5px] text-[var(--color-text-secondary)]">
            Copy the prompt with the Claude + MCP flavor and it carries this call, so the agent
            can pull the same record itself rather than working from the pasted copy. The other
            flavors inline the whole plan instead, because they have no tool that resolves an id.
          </p>
          <p className="mt-1.5 break-all rounded bg-[var(--color-bg-inset)] px-2 py-1.5 font-mono text-xs text-[var(--color-text-primary)]">
            {opportunityHandoffCall(detail.opportunity_id)}
          </p>
        </section>
      </div>

      {onAiPrompt ? (
        <div className="flex items-center gap-3 border-t border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-5 py-3.5">
          <button
            type="button"
            onClick={() => onAiPrompt(detail)}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-[var(--color-model)] px-3.5 py-2 text-sm font-semibold text-[var(--color-text-on-model)] transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
          >
            <Sparkles className="h-4 w-4" aria-hidden="true" />
            Copy prompt for an agent
          </button>
          <span className="hidden text-xs text-[var(--color-text-tertiary)] sm:block">
            The ordered steps, the evidence, and the id to query it back.
          </span>
        </div>
      ) : null}
    </>
  );
}

function StepCard({
  step,
  index,
  onOpenStep,
  fileHref,
}: {
  step: OpportunityStep;
  index: number;
  onOpenStep?: ((planId: string) => void) | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
}) {
  const meta = typeMeta(step.refactoring_type);
  const mechanical = step.applicability.classification === "mechanical";
  const href = fileHref?.(step.file_path, step.line_start);
  const span = step.line_start
    ? `${step.line_start}${step.line_end ? `–${step.line_end}` : ""}`
    : null;

  return (
    <li className="rounded-lg border border-[var(--color-border-default)] px-3.5 py-3">
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <span className="font-mono text-xs tabular-nums text-[var(--color-text-tertiary)]">
          {String(index + 1).padStart(2, "0")}
        </span>
        <span className="text-[12.5px] text-[var(--color-text-secondary)]">{meta.label}</span>
        {/* Not a colour-only mark: the word is the signal, and the tint only
            reinforces it. */}
        <span
          className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
            mechanical
              ? "bg-[var(--color-success)]/12 text-[var(--color-success)]"
              : "bg-[var(--color-bg-inset)] text-[var(--color-text-secondary)]"
          }`}
        >
          {mechanical ? "Mechanical" : "Judgment"}
        </span>
        {isRelocated(step) ? (
          <span className="rounded bg-[var(--color-caution)]/15 px-1.5 py-0.5 text-[11px] font-medium text-[var(--color-caution)]">
            Moved by an earlier step
          </span>
        ) : null}
      </div>

      <button
        type="button"
        onClick={onOpenStep ? () => onOpenStep(step.plan_id) : undefined}
        disabled={!onOpenStep}
        className="mt-1 block break-all text-left font-mono text-[13px] font-medium text-[var(--color-text-primary)] enabled:hover:text-[var(--color-accent-primary)]"
      >
        {step.target_symbol || meta.label}
      </button>

      <p className="mt-0.5 break-all font-mono text-[11px] text-[var(--color-text-tertiary)]">
        {href ? (
          <a href={href} className="underline-offset-2 hover:underline">
            {step.file_path}
            {span ? `:${span}` : ""}
          </a>
        ) : (
          <>
            {step.file_path}
            {span ? `:${span}` : ""}
          </>
        )}
      </p>

      {isRelocated(step) ? (
        <p className="mt-1.5 text-[12px] text-[var(--color-text-secondary)]">
          An earlier step moves this symbol out of that file, so the path and lines above say where
          it was. Find it again before applying this one.
        </p>
      ) : null}

      {step.applicability.reasons.length > 0 ? (
        <p className="mt-1.5 text-[12px] text-[var(--color-text-secondary)]">
          {step.applicability.reasons.map(humanizeBiomarker).join("; ")}.
        </p>
      ) : null}

      {step.applicability.unknowns.length > 0 ? (
        // An unknown is first-class. Leaving it out would let a check the layer
        // never ran read as a check that passed.
        <p className="mt-1 text-[12px] text-[var(--color-text-tertiary)]">
          Not established: {step.applicability.unknowns.map(humanizeBiomarker).join(", ")}.
        </p>
      ) : null}
    </li>
  );
}
