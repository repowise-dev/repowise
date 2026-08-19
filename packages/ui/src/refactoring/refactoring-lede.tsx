/**
 * The Refactoring page's opening read.
 *
 * The surface used to open on a priority-by-effort scatter with no sentence
 * anywhere above it, which made it the only ported page with no lede. The
 * figure alone is also actively misleading here: "1,819 plans" reads as a
 * backlog you are failing at, when 96% of it is small local tidy-ups you do
 * while already in the file.
 *
 * So the prose carries the split, and the split is what the rest of the page is
 * organised around. It is measured, not asserted — every number below is
 * counted off the plans passed in.
 */

import type { ReactNode } from "react";

import { PageLede } from "../shared/page-lede";
import { StatRibbon, type RibbonStat } from "../stats/stat-ribbon";
import { formatNumber } from "../lib/format";
import { typeMeta } from "./meta";
import { isStructural, planPoint, type RefactoringPlan } from "./types";

export interface RefactoringLedeProps {
  plans: RefactoringPlan[];
  /** Files in the repo, for the "802 of 4,952 indexed" denominator. Omit and
   *  the ribbon drops the comparison rather than inventing one. */
  indexedFileCount?: number | undefined;
  /** Rendered under the prose. */
  action?: ReactNode;
}

/** "46 files to split, 13 cycles to cut and 6 classes doing two jobs" — built
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

export function RefactoringLede({ plans, indexedFileCount, action }: RefactoringLedeProps) {
  const total = plans.length;
  const structural = plans.filter(isStructural);
  const local = total - structural.length;

  const files = new Set(plans.map((p) => p.file_path)).size;
  const small = plans.filter((p) => (p.effort_bucket || "M") === "S").length;
  const recovers = plans.filter((p) => p.impact_delta >= 0.1).length;
  const plottable = structural.filter((p) => planPoint(p) !== null).length;

  const byType = new Map<string, number>();
  for (const p of structural) byType.set(p.refactoring_type, (byType.get(p.refactoring_type) ?? 0) + 1);
  const structuralPhrase = joinPhrases(
    [...byType.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([type, n]) => STRUCTURAL_PHRASE[type]?.(n) ?? `${n} ${typeMeta(type).label.toLowerCase()}`),
  );

  // The largest single recovery on the page. Quoted rather than assumed,
  // because the ceiling is what makes "rank by health recovered" a bad idea and
  // a reader is entitled to check the claim.
  const bestGain = plans.reduce((m, p) => Math.max(m, p.impact_delta), 0);
  const negligible = plans.filter((p) => p.impact_delta < 0.5).length;

  const stats: RibbonStat[] = [
    {
      label: "Files with a plan",
      value: formatNumber(files),
      sub: indexedFileCount ? `of ${formatNumber(indexedFileCount)} indexed` : undefined,
    },
    {
      label: "Rated small effort",
      value: total ? `${Math.round((small / total) * 100)}%` : "0%",
      sub: `${formatNumber(small)} plan${small === 1 ? "" : "s"}`,
    },
    {
      label: "Recover health",
      value: formatNumber(recovers),
      sub: "+0.1 or better",
    },
    {
      label: "Change a file's shape",
      value: formatNumber(structural.length),
      sub: plottable < structural.length ? `${formatNumber(plottable)} measured` : "the rest are local",
    },
  ];

  return (
    <div className="space-y-6">
      <PageLede
        label="Open plans"
        value={formatNumber(total)}
        unit={files ? `across ${formatNumber(files)} file${files === 1 ? "" : "s"}` : undefined}
        layout="beside"
        action={action}
        figureFooter={
          structural.length > 0 ? (
            <p className="text-caption text-[var(--color-text-tertiary)]">
              {formatNumber(structural.length)} of them change a file&apos;s shape. The rest are
              local.
            </p>
          ) : undefined
        }
      >
        <p>
          <span className="font-medium text-[var(--color-text-primary)]">
            Most of this list is small.
          </span>{" "}
          {formatNumber(local)} plan{local === 1 ? "" : "s"} lift a slice of a long function or
          dedupe a repeated block, and {total ? Math.round((small / total) * 100) : 0}% are rated
          small effort. They are worth doing when you are already in the file, and they are not
          worth a planning meeting.
        </p>
        {structural.length > 0 ? (
          <p>
            <span className="font-medium text-[var(--color-text-primary)]">
              {formatNumber(structural.length)}{" "}
              {structural.length === 1 ? "is structural" : "are structural"}.
            </span>{" "}
            {structuralPhrase}. These change how the codebase is shaped, so they lead the page.
          </p>
        ) : null}
        {bestGain > 0 ? (
          <p>
            Health recovered tops out at +{bestGain.toFixed(1)} per plan and{" "}
            {formatNumber(negligible)} recover under half a point, so it ranks poorly on its own.
            The order below weighs how depended-upon the file is and how much rides along with it.
          </p>
        ) : null}
      </PageLede>

      <StatRibbon stats={stats} />
    </div>
  );
}
