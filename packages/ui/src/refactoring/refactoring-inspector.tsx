"use client";

import { Layers, Sparkles } from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../ui/sheet";
import { PlanDetail } from "./plan-detail";
import { CONFIDENCE_LABEL, EFFORT_LABEL, typeAccent, typeMeta } from "./meta";
import {
  blastCount,
  blastFiles,
  type Confidence,
  type EffortBucket,
  type RefactoringPlan,
} from "./types";

export interface RefactoringInspectorProps {
  plan: RefactoringPlan | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onAiPrompt?: ((plan: RefactoringPlan) => void) | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
}

const EVIDENCE_LABELS: Record<string, string> = {
  lcom4: "LCOM4",
  method_count: "Methods",
  field_count: "Fields",
  wmc: "WMC",
  occurrence_count: "Occurrences",
  duplicated_lines: "Duplicated lines",
  co_change_count: "Co-changes",
  foreign_calls: "Calls to target",
  own_calls: "Calls to own class",
  own_distance: "Distance to own",
  target_distance: "Distance to target",
  cycle_size: "Cycle size",
  edge_count: "Edges in cycle",
  cut_count: "Edges to cut",
};

function evidenceRows(plan: RefactoringPlan): { label: string; value: string }[] {
  const rows: { label: string; value: string }[] = [];
  for (const [key, label] of Object.entries(EVIDENCE_LABELS)) {
    const v = plan.evidence?.[key];
    if (typeof v === "number" && Number.isFinite(v)) {
      rows.push({ label, value: Number.isInteger(v) ? String(v) : v.toFixed(2) });
    }
  }
  return rows;
}

/**
 * The slide-over that renders one plan visually: the concrete per-type plan,
 * the evidence behind it, and the blast radius. Mounted only for the selected
 * plan, so the heavy visual cost is paid once, on demand.
 */
export function RefactoringInspector({
  plan,
  open,
  onOpenChange,
  onAiPrompt,
  fileHref,
}: RefactoringInspectorProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full gap-0 overflow-y-auto p-0 sm:max-w-xl"
      >
        {plan ? <InspectorBody plan={plan} onAiPrompt={onAiPrompt} fileHref={fileHref} /> : null}
      </SheetContent>
    </Sheet>
  );
}

function InspectorBody({
  plan,
  onAiPrompt,
  fileHref,
}: {
  plan: RefactoringPlan;
  onAiPrompt?: ((plan: RefactoringPlan) => void) | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
}) {
  const meta = typeMeta(plan.refactoring_type);
  const accent = typeAccent(plan.refactoring_type);
  const { Icon } = meta;
  const effort = (plan.effort_bucket || "M") as EffortBucket;
  const confidence = (plan.confidence || "medium") as Confidence;
  const evidence = evidenceRows(plan);
  const blast = blastFiles(plan).filter((f) => f !== plan.file_path);
  const blastN = blastCount(plan);

  return (
    <>
      <SheetHeader className="border-b border-[var(--color-border-default)] px-6 py-5 pr-12">
        <SheetTitle className="flex items-center gap-3 text-base">
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl"
            style={{ backgroundColor: `color-mix(in srgb, ${accent} 14%, transparent)`, color: accent }}
          >
            <Icon className="h-[18px] w-[18px]" />
          </span>
          <span className="min-w-0">
            <span className="block font-semibold text-[var(--color-text-primary)]">{meta.label}</span>
            <code className="block truncate text-xs font-normal text-[var(--color-text-secondary)]">
              {plan.target_symbol}
            </code>
          </span>
        </SheetTitle>
      </SheetHeader>

      <div className="space-y-6 px-6 py-5">
        <p className="text-sm leading-relaxed text-[var(--color-text-secondary)]">{meta.blurb}</p>

        {/* Signal chips */}
        <div className="flex flex-wrap gap-2">
          <Chip label="Effort" value={EFFORT_LABEL[effort]} />
          <Chip label="Confidence" value={CONFIDENCE_LABEL[confidence]} />
          {blastN > 0 ? <Chip label="Touches" value={`${blastN} file${blastN === 1 ? "" : "s"}`} /> : null}
          {plan.impact_delta > 0 ? <Chip label="Recovers" value={`+${plan.impact_delta.toFixed(2)}`} /> : null}
        </div>

        {/* The visual plan */}
        <section>
          <h4 className="mb-2.5 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
            The plan
          </h4>
          <PlanDetail plan={plan} fileHref={fileHref} />
        </section>

        {/* Evidence */}
        {evidence.length > 0 ? (
          <section>
            <h4 className="mb-2.5 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-tertiary)]">
              Why this was flagged
            </h4>
            <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
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

        {/* Blast radius */}
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
        <div className="sticky bottom-0 mt-auto border-t border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-6 py-4">
          <button
            type="button"
            onClick={() => onAiPrompt(plan)}
            className="inline-flex items-center gap-2 rounded-lg bg-[var(--color-accent-primary)] px-3.5 py-2 text-sm font-semibold text-[var(--color-bg-surface)] transition-opacity hover:opacity-90"
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
