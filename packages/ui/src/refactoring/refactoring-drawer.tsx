"use client";

/**
 * The plan inspector, as a right-hand drawer.
 *
 * It was a centred `max-w-4xl` dialog with six sections, a generated diff and a
 * sticky footer — a reading surface wearing a modal. A drawer keeps the list on
 * screen behind it, and lets the host put the open plan in the URL so a plan
 * can be linked rather than only navigated to.
 *
 * The before-and-after itself is unchanged: `PlanComparison`, the evidence row
 * and the blast list are the strongest work on this surface and the rework does
 * not touch what they show. What changed is the frame. The header used to paint
 * a gradient wash, an icon tile and the "After" panel's border from a per-type
 * accent, which meant six hues separating categories where two types are 96% of
 * the data — and Extract Helper's green was the same green as the health badge
 * next to it. The type is a word now. Effort, confidence, blast radius and
 * recovered health were three chip shapes across two sections; they are one
 * line of prose under the title.
 */

import type { ReactNode } from "react";
import { Layers, Sparkles, TrendingUp, Wand2 } from "lucide-react";

import { Sheet, SheetContent, SheetTitle } from "../ui/sheet";
import { Skeleton, SkeletonRegion } from "../ui/skeleton";
import { formatNumber } from "../lib/format";
import { PlanComparison } from "./plan-comparison";
import { GenerateCodePanel } from "./generate-code-panel";
import { PriorityExplanation } from "./priority-explanation";
import { ValidationSummary } from "./validation-summary";
import { CONFIDENCE_LABEL, EFFORT_LABEL, typeMeta } from "./meta";
import { blastCount, blastFiles, evidenceRows, planWins } from "./types";
import type {
  Confidence,
  EffortBucket,
  GeneratedCode,
  RefactoringPlan,
} from "@repowise-dev/types/refactoring";

export interface RefactoringDrawerProps {
  plan: RefactoringPlan | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  loading?: boolean | undefined;
  error?: string | undefined;
  /** Optional causal context supplied by another shared product surface. */
  contextSlot?: ReactNode;
  onAiPrompt?: ((plan: RefactoringPlan) => void) | undefined;
  /** Opt-in LLM code generation. Omit to hide the section entirely. */
  onGenerateCode?: ((plan: RefactoringPlan) => Promise<GeneratedCode>) | undefined;
  settingsHref?: string | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
}

export function RefactoringDrawer({
  plan,
  open,
  onOpenChange,
  loading = false,
  error,
  contextSlot,
  onAiPrompt,
  onGenerateCode,
  settingsHref,
  fileHref,
}: RefactoringDrawerProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        closeLabel="Close plan"
        // Wider than the nav sheet this primitive was written for: the
        // before-and-after is two columns at rest, and the reading floor for the
        // code block is what sets the minimum.
        className="w-full max-w-[680px] sm:w-[92vw]"
      >
        {plan ? (
          <DrawerBody
            plan={plan}
            onAiPrompt={onAiPrompt}
            onGenerateCode={onGenerateCode}
            settingsHref={settingsHref}
            fileHref={fileHref}
            contextSlot={contextSlot}
          />
        ) : loading ? (
          <>
            <SheetTitle className="border-b border-[var(--color-border-default)] px-5 py-4 pr-12 text-[15px]">
              Loading structured plan
            </SheetTitle>
            <SkeletonRegion className="space-y-4 px-5 py-5" label="Loading structured plan">
              <Skeleton className="h-16 rounded-none" />
              <Skeleton className="h-40 rounded-none" />
            </SkeletonRegion>
          </>
        ) : error ? (
          <>
            <SheetTitle className="border-b border-[var(--color-border-default)] px-5 py-4 pr-12 text-[15px]">
              Structured plan unavailable
            </SheetTitle>
            <p className="px-5 py-5 text-sm text-[var(--color-text-secondary)]">{error}</p>
          </>
        ) : (
          <SheetTitle className="sr-only">Structured plan</SheetTitle>
        )}
      </SheetContent>
    </Sheet>
  );
}

function DrawerBody({
  plan,
  onAiPrompt,
  onGenerateCode,
  settingsHref,
  fileHref,
  contextSlot,
}: {
  plan: RefactoringPlan;
  onAiPrompt?: ((plan: RefactoringPlan) => void) | undefined;
  onGenerateCode?: ((plan: RefactoringPlan) => Promise<GeneratedCode>) | undefined;
  settingsHref?: string | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
  contextSlot?: ReactNode;
}) {
  const meta = typeMeta(plan.refactoring_type);
  const effort = (plan.effort_bucket || "M") as EffortBucket;
  const confidence = (plan.confidence || "medium") as Confidence;
  const evidence = evidenceRows(plan);
  const wins = planWins(plan);
  const blast = blastFiles(plan).filter((f) => f !== plan.file_path);
  const blastN = blastCount(plan);
  const name = plan.file_path.split("/").pop() ?? plan.file_path;

  // One line of words, in the order a reader asks: how big, how sure, how far,
  // what it buys. Rule 4 — every figure gets a unit or a sentence.
  const facts: string[] = [
    `${EFFORT_LABEL[effort]} effort`,
    `${CONFIDENCE_LABEL[confidence]} confidence`,
  ];
  if (blastN > 0) facts.push(`${formatNumber(blastN)} file${blastN === 1 ? "" : "s"} affected`);

  return (
    <>
      <div className="border-b border-[var(--color-border-default)] px-5 py-4 pr-12">
        <div className="text-[11px] text-[var(--color-text-secondary)]">{meta.label}</div>
        <SheetTitle className="mt-0.5 break-words font-mono text-[15px] font-semibold text-[var(--color-text-primary)]">
          {plan.target_symbol || name}
        </SheetTitle>
        <p className="mt-1 break-all font-mono text-[11.5px] text-[var(--color-text-tertiary)]">
          {plan.file_path}
          {plan.line_start ? `:${plan.line_start}` : ""}
          {plan.line_start && plan.line_end ? `–${plan.line_end}` : ""}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-[var(--color-border-default)] px-5 py-2.5 text-[12.5px] text-[var(--color-text-secondary)]">
        {facts.map((f) => (
          <span key={f}>{f}</span>
        ))}
        {plan.impact_delta > 0 ? (
          <span className="font-medium tabular-nums text-[var(--color-success)]">
            +{plan.impact_delta.toFixed(1)} health
          </span>
        ) : (
          <span className="text-[var(--color-text-tertiary)]">No score change</span>
        )}
      </div>

      <div className="flex-1 space-y-6 overflow-y-auto px-5 py-5">
        {contextSlot}
        <p className="text-sm leading-relaxed text-[var(--color-text-secondary)]">{meta.blurb}</p>

        <section>
          <h4 className="mb-3 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            The change
          </h4>
          <PlanComparison plan={plan} fileHref={fileHref} />
        </section>

        <section>
          <h4 className="mb-3 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            Priority
          </h4>
          <PriorityExplanation plan={plan} />
        </section>

        <section>
          <h4 className="mb-3 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            Validation
          </h4>
          <ValidationSummary
            validation={plan.validation}
            fileHref={(path) => fileHref?.(path, null)}
          />
        </section>

        {onGenerateCode ? (
          <section>
            <div className="mb-3 flex items-center justify-between gap-3">
              <h4 className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                <Wand2 className="h-3.5 w-3.5" />
                Generate the code
              </h4>
              {settingsHref ? (
                <a
                  href={settingsHref}
                  className="text-[11px] text-[var(--color-text-tertiary)] underline-offset-2 transition-colors hover:text-[var(--color-text-secondary)] hover:underline"
                >
                  Change model
                </a>
              ) : null}
            </div>
            <GenerateCodePanel plan={plan} onGenerate={onGenerateCode} />
          </section>
        ) : null}

        {wins.length > 0 ? (
          <section>
            <h4 className="mb-2.5 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              <TrendingUp className="h-3.5 w-3.5" />
              What you gain
            </h4>
            <ul className="space-y-1">
              {wins.map((w) => (
                <li
                  key={w.label}
                  className={`flex items-start gap-2 text-sm ${
                    w.hero
                      ? "font-medium text-[var(--color-text-primary)]"
                      : "text-[var(--color-text-secondary)]"
                  }`}
                >
                  <span
                    className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-[var(--color-text-tertiary)]"
                    aria-hidden
                  />
                  {w.label}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {evidence.length > 0 ? (
          <section>
            <h4 className="mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              Why this was flagged
            </h4>
            <dl className="grid grid-cols-2 sm:grid-cols-4">
              {evidence.map((row) => (
                <div
                  key={row.label}
                  className="border-t border-[var(--color-border-default)] py-2 pr-3"
                >
                  <dt className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
                    {row.label}
                  </dt>
                  <dd className="mt-0.5 text-sm font-semibold tabular-nums text-[var(--color-text-primary)]">
                    {row.value}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ) : null}

        {blast.length > 0 ? (
          <section>
            <h4 className="mb-2 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              <Layers className="h-3.5 w-3.5" />
              Also affected
            </h4>
            <ul className="space-y-1">
              {blast.map((f) => {
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
      </div>

      {onAiPrompt ? (
        <div className="flex items-center gap-3 border-t border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-5 py-3.5">
          <button
            type="button"
            onClick={() => onAiPrompt(plan)}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-[var(--color-model)] px-3.5 py-2 text-sm font-semibold text-[var(--color-text-on-model)] transition-opacity hover:opacity-90"
          >
            <Sparkles className="h-4 w-4" />
            Copy prompt for an agent
          </button>
          <span className="hidden text-xs text-[var(--color-text-tertiary)] sm:block">
            The steps, the blast radius, and a completion contract.
          </span>
        </div>
      ) : null}
    </>
  );
}
