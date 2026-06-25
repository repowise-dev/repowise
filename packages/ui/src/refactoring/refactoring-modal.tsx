"use client";

import { Layers, Sparkles, TrendingUp, Wand2 } from "lucide-react";
import { Dialog, DialogContent, DialogTitle } from "../ui/dialog";
import { PlanComparison } from "./plan-comparison";
import { GenerateCodePanel } from "./generate-code-panel";
import { CONFIDENCE_LABEL, EFFORT_LABEL, typeAccent, typeMeta } from "./meta";
import {
  blastCount,
  blastFiles,
  evidenceRows,
  planWins,
  type Confidence,
  type EffortBucket,
  type GeneratedCode,
  type RefactoringPlan,
} from "./types";

export interface RefactoringModalProps {
  plan: RefactoringPlan | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAiPrompt?: ((plan: RefactoringPlan) => void) | undefined;
  /** Opt-in LLM code generation. When provided, a "Generate code" section
   *  renders a diff for the plan; omit it (e.g. on hosted / when disabled) to
   *  hide the action entirely. Host owns the API call. */
  onGenerateCode?: ((plan: RefactoringPlan) => Promise<GeneratedCode>) | undefined;
  /** Link to the repo settings (provider/model). Renders a quiet link beside
   *  the Generate code action when set. */
  settingsHref?: string | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
}

function fileParts(path: string): { name: string; dir: string } {
  const parts = path.split("/");
  const name = parts[parts.length - 1] ?? path;
  const dir = parts.slice(0, -1).join("/");
  return { name, dir };
}

/**
 * The plan inspector as a large, centered modal. Leads with the file, frames
 * the health it recovers as the win, and shows the refactoring as a visual
 * before→after. Mounted only for the selected plan, so the heavy visual cost
 * is paid once, on demand.
 */
export function RefactoringModal({
  plan,
  open,
  onOpenChange,
  onAiPrompt,
  onGenerateCode,
  settingsHref,
  fileHref,
}: RefactoringModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] w-[calc(100%-1.5rem)] max-w-4xl gap-0 overflow-y-auto p-0">
        {plan ? (
          <ModalBody
            plan={plan}
            onAiPrompt={onAiPrompt}
            onGenerateCode={onGenerateCode}
            settingsHref={settingsHref}
            fileHref={fileHref}
          />
        ) : (
          <DialogTitle className="sr-only">Refactoring plan</DialogTitle>
        )}
      </DialogContent>
    </Dialog>
  );
}

function ModalBody({
  plan,
  onAiPrompt,
  onGenerateCode,
  settingsHref,
  fileHref,
}: {
  plan: RefactoringPlan;
  onAiPrompt?: ((plan: RefactoringPlan) => void) | undefined;
  onGenerateCode?: ((plan: RefactoringPlan) => Promise<GeneratedCode>) | undefined;
  settingsHref?: string | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
}) {
  const meta = typeMeta(plan.refactoring_type);
  const accent = typeAccent(plan.refactoring_type);
  const { Icon } = meta;
  const effort = (plan.effort_bucket || "M") as EffortBucket;
  const confidence = (plan.confidence || "medium") as Confidence;
  const evidence = evidenceRows(plan);
  const wins = planWins(plan);
  const blast = blastFiles(plan).filter((f) => f !== plan.file_path);
  const blastN = blastCount(plan);
  const { name, dir } = fileParts(plan.file_path);

  return (
    <>
      {/* header — file first, type as a colored accent, win badge on the right */}
      <div
        className="border-b border-[var(--color-border-default)] px-6 py-5 pr-12"
        style={{ background: `linear-gradient(to bottom, color-mix(in srgb, ${accent} 6%, transparent), transparent)` }}
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <span
              className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
              style={{ backgroundColor: `color-mix(in srgb, ${accent} 14%, transparent)`, color: accent }}
            >
              <Icon className="h-[18px] w-[18px]" />
            </span>
            <div className="min-w-0">
              <DialogTitle className="truncate text-base font-semibold text-[var(--color-text-primary)]">
                {name}
              </DialogTitle>
              {dir ? (
                <p className="truncate font-mono text-[11px] text-[var(--color-text-tertiary)]">{dir}</p>
              ) : null}
              <div className="mt-1.5 flex flex-wrap items-center gap-2">
                <span
                  className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[11px] font-semibold"
                  style={{ backgroundColor: `color-mix(in srgb, ${accent} 14%, transparent)`, color: accent }}
                >
                  {meta.label}
                </span>
                {plan.target_symbol ? (
                  <code className="truncate text-[11px] text-[var(--color-text-secondary)]">
                    {plan.target_symbol}
                  </code>
                ) : null}
              </div>
            </div>
          </div>
          {plan.impact_delta > 0 ? (
            <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-[var(--color-success)]/10 px-3 py-1.5 text-sm font-semibold tabular-nums text-[var(--color-success)]">
              <TrendingUp className="h-4 w-4" />
              +{plan.impact_delta.toFixed(1)} health
            </span>
          ) : null}
        </div>
      </div>

      <div className="space-y-6 px-6 py-5">
        <p className="text-sm leading-relaxed text-[var(--color-text-secondary)]">{meta.blurb}</p>

        {/* the visual before → after */}
        <section>
          <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
            The change
          </h4>
          <PlanComparison plan={plan} fileHref={fileHref} />
        </section>

        {/* generate code — opt-in LLM enrichment (plan -> diff) */}
        {onGenerateCode ? (
          <section>
            <div className="mb-3 flex items-center justify-between gap-3">
              <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
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

        {/* what you gain */}
        {wins.length > 0 ? (
          <section className="rounded-2xl border border-[var(--color-success)]/25 bg-[var(--color-success)]/[0.05] p-4">
            <h4 className="mb-2.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--color-success)]">
              <TrendingUp className="h-3.5 w-3.5" />
              What you gain
            </h4>
            <ul className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2">
              {wins.map((w) => (
                <li
                  key={w.label}
                  className={`flex items-center gap-2 text-sm ${
                    w.hero
                      ? "font-semibold text-[var(--color-text-primary)]"
                      : "text-[var(--color-text-secondary)]"
                  }`}
                >
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-success)]"
                    aria-hidden
                  />
                  {w.label}
                </li>
              ))}
            </ul>
            <div className="mt-3 flex flex-wrap gap-2 border-t border-[var(--color-success)]/15 pt-3">
              <Chip label="Effort" value={EFFORT_LABEL[effort]} />
              <Chip label="Confidence" value={CONFIDENCE_LABEL[confidence]} />
              {blastN > 0 ? (
                <Chip label="Touches" value={`${blastN} file${blastN === 1 ? "" : "s"}`} />
              ) : null}
            </div>
          </section>
        ) : null}

        {/* evidence */}
        {evidence.length > 0 ? (
          <section>
            <h4 className="mb-2.5 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
              Why this was flagged
            </h4>
            <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {evidence.map((row) => (
                <div
                  key={row.label}
                  className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-2"
                >
                  <dt className="text-[10px] uppercase tracking-wide text-[var(--color-text-tertiary)]">
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

        {/* blast radius */}
        {blast.length > 0 ? (
          <section>
            <h4 className="mb-2.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
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
                        className="font-mono text-xs text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-accent-primary)] hover:underline"
                      >
                        {f}
                      </a>
                    ) : (
                      <span className="font-mono text-xs text-[var(--color-text-secondary)]">{f}</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}
      </div>

      {onAiPrompt ? (
        <div className="sticky bottom-0 mt-auto flex flex-col gap-3 border-t border-[var(--color-border-default)] bg-[var(--color-bg-overlay)] px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
          <span className="hidden text-xs text-[var(--color-text-tertiary)] sm:block">
            Hand the exact steps, blast radius, and a completion contract to your coding agent.
          </span>
          <button
            type="button"
            onClick={() => onAiPrompt(plan)}
            className="inline-flex w-full shrink-0 items-center justify-center gap-2 rounded-lg bg-[var(--color-accent-primary)] px-3.5 py-2 text-sm font-semibold text-[var(--color-bg-surface)] transition-opacity hover:opacity-90 sm:w-auto"
          >
            <Sparkles className="h-4 w-4" />
            Get AI prompt
          </button>
        </div>
      ) : null}
    </>
  );
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-3 py-1 text-xs">
      <span className="text-[var(--color-text-tertiary)]">{label}</span>
      <span className="font-medium text-[var(--color-text-primary)]">{value}</span>
    </span>
  );
}
