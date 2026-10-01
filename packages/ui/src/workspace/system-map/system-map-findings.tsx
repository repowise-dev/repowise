"use client";

/**
 * "Needs attention": the findings the map's shape implies, listed whether or
 * not a lens is on. A dependency cycle is visible as two arrows on the canvas
 * but easy to miss there; here it is a named row with the action that fixes
 * it. Quiet states stay quiet: a clean check is one sentence, not a row of
 * green badges.
 */

import { useState } from "react";
import type {
  BreakingChangeReport,
  ConformanceReport,
  ExtractionDiagnostics,
  SystemGraph,
} from "@repowise-dev/types/workspace";
import { OverviewSection } from "../../overview/section";
import { AiPromptButton } from "../../health/ai-prompt-button";
import { AiPromptModal } from "../../health/ai-prompt-modal";
import { buildConformanceAiPrompt } from "../../health/ai-prompt-builder";
import { CycleRow } from "./system-map-lens-results";
import type { SystemMapPromptState } from "./system-map-drawer";
import type { SystemMapLens } from "./system-map-lens";
import { plural, unmatchedReasonList } from "./system-map-model";
import type { SystemMapSelection } from "./types";

export interface SystemMapFindingsProps {
  graph: SystemGraph;
  conformance: ConformanceReport | null;
  breaking: BreakingChangeReport | null;
  diagnostics?: ExtractionDiagnostics | null | undefined;
  /** The request for a report failed; without these a missing report reads as loading. */
  conformanceError?: boolean | undefined;
  breakingError?: boolean | undefined;
  onSelect: (selection: SystemMapSelection) => void;
  onLensChange: (lens: SystemMapLens) => void;
  /** Where unmatched consumers are reviewed (the Contracts page). */
  unmatchedHref?: string | undefined;
  conformanceHref?: string | undefined;
  LinkComponent?: React.ElementType | undefined;
}

export function SystemMapFindings({
  graph,
  conformance,
  breaking,
  diagnostics,
  conformanceError,
  breakingError,
  onSelect,
  onLensChange,
  unmatchedHref,
  conformanceHref,
  LinkComponent,
}: SystemMapFindingsProps) {
  const [prompt, setPrompt] = useState<SystemMapPromptState | null>(null);
  const A = LinkComponent ?? "a";

  const cycles = conformance?.cycles ?? [];
  const totalCycles = conformance ? (conformance.total_cycles ?? conformance.cycle_count) : 0;
  const violations = conformance?.violations ?? [];
  const breakingCount = breaking?.breaking_count ?? 0;
  const unmatched = diagnostics?.unmatched_consumers.length ?? 0;
  const reasons = unmatchedReasonList(diagnostics?.unmatched_by_reason ?? {});
  // An all-clear needs both reports loaded and actually run; a missing report
  // is not a clean one.
  const clean =
    Boolean(conformance?.generated_at && breaking?.generated_at) &&
    cycles.length === 0 &&
    violations.length === 0 &&
    breakingCount === 0;

  const status: string[] = [];
  if (conformanceError) status.push("Could not load the conformance report.");
  else if (!conformance) status.push("Loading the conformance report…");
  else if (!conformance.generated_at)
    status.push("Conformance has not been checked yet. Run a workspace update to produce a result.");
  if (breakingError) status.push("Could not load the breaking-change report.");
  else if (!breaking) status.push("Loading the breaking-change report…");
  else if (!breaking.generated_at)
    status.push("Breaking-change detection has not run yet. Run a workspace update to produce a result.");

  return (
    <OverviewSection
      title="Needs attention"
      description="Findings from the most recent workspace update. Each names the services involved; click one to open it on the map."
    >
      <ul className="m-0 flex list-none flex-col divide-y divide-[var(--color-border-default)] border-t border-[var(--color-border-default)] p-0">
        {cycles.map((c) => (
          <CycleRow key={c.edge_ids.join("|")} cycle={c} graph={graph} onSelect={onSelect} onPrompt={setPrompt} />
        ))}
        {totalCycles > cycles.length && (
          <li className="py-3 text-xs text-[var(--color-text-secondary)]">
            {`${totalCycles - cycles.length} more ${totalCycles - cycles.length === 1 ? "cycle is" : "cycles are"} not listed; the report keeps the first ${cycles.length}.`}
          </li>
        )}
        {violations.length > 0 && (
          <li className="flex flex-wrap items-center justify-between gap-3 py-3">
            <span className="flex items-center gap-2 text-[15px] text-[var(--color-text-primary)]">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--color-error)]" />
              {plural(violations.length, "dependency breaks", "dependencies break")} a declared architecture rule.
              <button
                type="button"
                onClick={() => onLensChange("conformance")}
                className="cursor-pointer text-xs font-medium text-[var(--color-accent-primary)] hover:underline"
              >
                Show on map
              </button>
            </span>
            <AiPromptButton
              label="Fix violations"
              onClick={() =>
                setPrompt({
                  title: "AI conformance fix",
                  description: "A ready-to-paste prompt that has your agent remove the dependencies your rules forbid.",
                  build: (flavor) => buildConformanceAiPrompt({ violations, flavor }),
                })
              }
            />
          </li>
        )}
        {breakingCount > 0 && breaking && (
          <li className="flex flex-wrap items-center justify-between gap-3 py-3">
            <span className="flex items-center gap-2 text-[15px] text-[var(--color-text-primary)]">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--color-error)]" />
              {plural(breakingCount, "provider change breaks", "provider changes break")} compatibility, exposing{" "}
              {plural(breaking.total_impacted_consumers, "consumer", "consumers")}.
            </span>
            <button
              type="button"
              onClick={() => onLensChange("breaking")}
              className="cursor-pointer rounded-md border border-[var(--color-border-hover)] px-2.5 py-1 text-xs font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-bg-elevated)]"
            >
              Show on map
            </button>
          </li>
        )}
        {clean && (
          <li className="py-3 text-[15px] text-[var(--color-text-secondary)]">
            No dependency cycles, rule violations or breaking changes.
          </li>
        )}
        {unmatched > 0 && (
          <li className="flex flex-wrap items-baseline justify-between gap-3 py-3">
            <span className="text-[15px] text-[var(--color-text-secondary)]">
              {plural(unmatched, "call", "calls")} to a contract match no provider in the workspace
              {reasons && `: ${reasons}`}.
            </span>
            {unmatchedHref && (
              <A href={unmatchedHref} className="whitespace-nowrap text-xs font-medium text-[var(--color-accent-primary)] hover:underline">
                Review on Contracts →
              </A>
            )}
          </li>
        )}
        {status.map((line) => (
          <li key={line} className="py-3 text-xs text-[var(--color-text-tertiary)]">
            {line}
          </li>
        ))}
        {conformanceHref && cycles.length + violations.length > 0 && (
          <li className="py-3">
            <A href={conformanceHref} className="text-xs font-medium text-[var(--color-accent-primary)] hover:underline">
              See the dependency matrix on Conformance →
            </A>
          </li>
        )}
      </ul>
      <AiPromptModal
        open={prompt !== null}
        onOpenChange={(o) => !o && setPrompt(null)}
        getPrompt={prompt?.build ?? null}
        {...(prompt ? { title: prompt.title, description: prompt.description } : {})}
      />
    </OverviewSection>
  );
}
