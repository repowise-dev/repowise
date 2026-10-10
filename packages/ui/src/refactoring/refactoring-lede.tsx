/**
 * The Refactoring page's opening read: the figure, two lines, and the chips
 * that filter the list to the subsets the lines name.
 *
 * Every figure in the prose comes off the repository rollup the board already
 * loads. The chip counts come off the page's facets instead, because a chip
 * filters the list and must count what the click will show: the facets follow
 * the status and scope the list is in, and the rollup does not.
 */

import type { ReactNode } from "react";

import { PageLede } from "../shared/page-lede";
import { FilterChip } from "../health/code-health-controls";
import { formatNumber } from "../lib/format";
import { STRUCTURAL_TYPES } from "./types";
import type { RefactoringOpportunityRollup } from "@repowise-dev/types/refactoring";

export interface RefactoringLedeProps {
  /** The repository rollup. Absent or unavailable and the lede does not render. */
  summary?: RefactoringOpportunityRollup | null | undefined;
  /** The page's facet counts. Absent and the chips do not render. */
  facets?: Record<string, Record<string, number>> | null | undefined;
  /** Whether the list is narrowed to small effort, which the Quick wins chip toggles. */
  quickWinsActive?: boolean;
  onQuickWins?: (() => void) | undefined;
  /** Jump to the structural set. */
  onStructural?: (() => void) | undefined;
  /** Rendered under the prose. */
  action?: ReactNode;
}

function plural(n: number, one: string, many: string): string {
  return `${formatNumber(n)} ${n === 1 ? one : many}`;
}

export function RefactoringLede({
  summary,
  facets,
  quickWinsActive = false,
  onQuickWins,
  onStructural,
  action,
}: RefactoringLedeProps) {
  // No analysis is a different state from no work, and the board says which.
  if (!summary || summary.status !== "available") return null;

  const total = summary.opportunities_total;
  const files = summary.files_total;
  const steps = summary.steps_total;
  const mechanical = summary.mechanical_steps_total;
  const judgment = summary.judgment_steps_total;

  const byLead = summary.by_lead_type ?? {};
  const structural = (STRUCTURAL_TYPES as readonly string[]).reduce(
    (n, type) => n + (byLead[type] ?? 0),
    0,
  );
  const local = Math.max(0, total - structural);

  const quickWins = facets?.effort?.S ?? 0;
  const structuralShown = (STRUCTURAL_TYPES as readonly string[]).reduce(
    (n, type) => n + (facets?.lead_type?.[type] ?? 0),
    0,
  );
  const showQuickWins = onQuickWins && (quickWins > 0 || quickWinsActive);
  const showStructural = onStructural && structuralShown > 0;

  return (
    <PageLede
      label="Open opportunities"
      value={formatNumber(total)}
      unit={files ? `one per file, across ${plural(files, "file", "files")}` : undefined}
      action={action}
    >
      <p>
        <span className="font-medium text-[var(--color-text-primary)]">
          {structural > 0
            ? `${plural(structural, "changes", "change")} a file's shape; ${plural(local, "is", "are")} local.`
            : "All of it is local."}
        </span>{" "}
        Local work lifts a slice of a long function or tidies a repeated block, worth doing while
        you are in the file.
      </p>
      {steps > 0 ? (
        <p>
          <span className="font-medium text-[var(--color-text-primary)]">
            {formatNumber(mechanical)} of {plural(steps, "step is", "steps are")} mechanical,
          </span>{" "}
          ready to hand to an agent as written; the other {formatNumber(judgment)} want a
          person&apos;s judgment first.
        </p>
      ) : null}
      {showQuickWins || showStructural ? (
        <div className="flex flex-wrap gap-2 pt-1" role="group" aria-label="Filter the list">
          {showQuickWins ? (
            <FilterChip active={quickWinsActive} onClick={onQuickWins}>
              Quick wins{" "}
              <span className="font-mono tabular-nums">{formatNumber(quickWins)}</span>
            </FilterChip>
          ) : null}
          {showStructural ? (
            <FilterChip active={false} onClick={onStructural}>
              Structural{" "}
              <span className="font-mono tabular-nums">{formatNumber(structuralShown)}</span>
            </FilterChip>
          ) : null}
        </div>
      ) : null}
    </PageLede>
  );
}
