/**
 * The Refactoring page's opening read.
 *
 * The surface used to open on a priority-by-effort scatter with no sentence
 * anywhere above it, which made it the only ported page with no lede. The
 * figure alone is also actively misleading: "1,819 plans" reads as a backlog you
 * are failing at, when most of it is small local tidy-ups you do while already
 * in the file.
 *
 * It counted plans until the page's unit became the opportunity, and then it
 * counted nothing at all: the page stopped fetching the plan list, so every
 * figure here silently read zero while the board below it showed 581 rows. The
 * fix is not to re-fetch plans - it is to say the thing the composed unit
 * actually knows, which is a better story anyway. Every number comes off the
 * repository rollup the board already loads, so the lede and the list cannot
 * disagree.
 *
 * The fact worth leading with changed too. It is no longer "most of this is
 * small" - it is how much of the work is *mechanical*, and how much of it
 * addresses what is actually wrong with the file. Both are honest and both were
 * unsayable before composition.
 */

import type { ReactNode } from "react";

import { PageLede } from "../shared/page-lede";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
import { formatNumber } from "../lib/format";
import { typeMeta } from "./meta";
import { STRUCTURAL_TYPES } from "./types";
import type { RefactoringOpportunityRollup } from "@repowise-dev/types/refactoring";

export interface RefactoringLedeProps {
  /** The repository rollup. Absent or unavailable and the lede does not render. */
  summary?: RefactoringOpportunityRollup | null | undefined;
  /** Files in the repo, for the "581 of 4,952 indexed" denominator. Omit and
   *  the ribbon drops the comparison rather than inventing one. */
  indexedFileCount?: number | undefined;
  /** Rendered under the prose. */
  action?: ReactNode;
}

/** "46 files to split, 13 cycles to cut and 6 classes doing two jobs" - built
 *  rather than interpolated so a repo with one kind does not read "and". */
function joinPhrases(parts: string[]): string {
  if (parts.length <= 1) return parts[0] ?? "";
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** What each structural type is, in the words someone would use out loud. */
const STRUCTURAL_PHRASE: Record<string, (n: number) => string> = {
  split_file: (n) => `${n} file${n === 1 ? "" : "s"} large enough to split along a real seam`,
  break_cycle: (n) => `${n} import cycle${n === 1 ? "" : "s"} with a cuttable edge`,
  extract_class: (n) => `${n} class${n === 1 ? "" : "es"} doing more than one job`,
  move_method: (n) => `${n} method${n === 1 ? "" : "s"} living on the wrong class`,
};

export function RefactoringLede({ summary, indexedFileCount, action }: RefactoringLedeProps) {
  // No analysis is a different state from no work, and the board says which.
  // The lede has nothing to open with either way.
  if (!summary || summary.status !== "available") return null;

  const total = summary.opportunities_total;
  const files = summary.files_total;
  const steps = summary.steps_total;
  const mechanical = summary.mechanical_steps_total;
  const judgment = summary.judgment_steps_total;

  const byLead = summary.by_lead_type ?? {};
  const structuralByType = (STRUCTURAL_TYPES as readonly string[])
    .map((type) => [type, byLead[type] ?? 0] as const)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  const structuralTotal = structuralByType.reduce((n, [, count]) => n + count, 0);
  const local = Math.max(0, total - structuralTotal);

  const small = summary.by_effort?.S ?? 0;
  const smallPct = total ? Math.round((small / total) * 100) : 0;
  const mechanicalPct = steps ? Math.round((mechanical / steps) * 100) : 0;

  const primary = summary.addresses_primary_problem;
  const addressesLead = primary?.yes ?? 0;
  const addressesSide = primary?.no ?? 0;
  const addressesUnknown = primary?.unknown ?? 0;

  const structuralPhrase = joinPhrases(
    structuralByType.map(
      ([type, n]) => STRUCTURAL_PHRASE[type]?.(n) ?? `${n} ${typeMeta(type).label.toLowerCase()}`,
    ),
  );

  const stats: RibbonStat[] = [
    {
      label: "Files with work",
      value: formatNumber(files),
      sub: indexedFileCount ? `of ${formatNumber(indexedFileCount)} indexed` : "one each",
    },
    {
      label: "Steps to apply",
      value: formatNumber(steps),
      sub: `${formatNumber(mechanical)} mechanical, ${formatNumber(judgment)} judgment`,
    },
    {
      label: "Rated small effort",
      value: total ? `${smallPct}%` : "0%",
      sub: `${formatNumber(small)} opportunit${small === 1 ? "y" : "ies"}`,
    },
    {
      label: "Change a file's shape",
      value: formatNumber(structuralTotal),
      sub: structuralTotal ? "structural, and they lead the page" : "none in this repo",
    },
  ];

  return (
    <div className="space-y-6">
      <PageLede
        label="Open opportunities"
        value={formatNumber(total)}
        unit={files ? `one per file, across ${formatNumber(files)} file${files === 1 ? "" : "s"}` : undefined}
        layout="beside"
        action={action}
        figureFooter={
          steps > 0 ? (
            <p className="text-caption text-[var(--color-text-tertiary)]">
              {formatNumber(steps)} step{steps === 1 ? "" : "s"} in total, so most files carry more
              than one.
            </p>
          ) : undefined
        }
      >
        <p>
          <span className="font-medium text-[var(--color-text-primary)]">
            {mechanicalPct}% of the steps are mechanical.
          </span>{" "}
          {formatNumber(mechanical)} of {formatNumber(steps)} have every proof obligation met, so
          they are safe to hand to an agent as written. The other {formatNumber(judgment)} change
          where something lives or what it is called, and want a person&apos;s judgment first.
        </p>
        {addressesSide > 0 || addressesLead > 0 ? (
          <p>
            <span className="font-medium text-[var(--color-text-primary)]">
              {formatNumber(addressesLead)} address the file&apos;s main problem.
            </span>{" "}
            {formatNumber(addressesSide)} do not - they are real work, but not the biggest cost in
            that file
            {addressesUnknown > 0
              ? `, and for ${formatNumber(addressesUnknown)} no dominant problem was recorded to compare against`
              : ""}
            . Each row says which, so nothing here claims to fix more than it does.
          </p>
        ) : null}
        {local > 0 ? (
          <p>
            <span className="font-medium text-[var(--color-text-primary)]">
              Most of this list is local.
            </span>{" "}
            {formatNumber(local)} opportunit{local === 1 ? "y lifts" : "ies lift"} a slice of a long
            function or tidy a repeated block, and {smallPct}% are rated small effort. They are
            worth doing when you are already in the file, and they are not worth a planning meeting.
          </p>
        ) : null}
        {structuralTotal > 0 ? (
          <p>
            <span className="font-medium text-[var(--color-text-primary)]">
              {formatNumber(structuralTotal)}{" "}
              {structuralTotal === 1 ? "is structural" : "are structural"}.
            </span>{" "}
            {structuralPhrase}. These change how the codebase is shaped, so they lead the page.
          </p>
        ) : null}
      </PageLede>

      <StatRibbon stats={stats} />
    </div>
  );
}
