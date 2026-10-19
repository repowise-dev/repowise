"use client";

import type { ReactNode } from "react";
import type { PerformanceScope } from "@repowise-dev/types/health";

import { PageLede } from "../../shared/page-lede";
import { areaLabel } from "./area-groups";
import type { PerformanceAreaGroup } from "./grouping";

/**
 * The lede: the count in the selected scope, named for that scope, then
 * where the causes sit. The accounting sentence beneath it names the rest of
 * the repository; the method notes live behind the label's info tip.
 */

export interface Verdict {
  value: number;
  unit: string;
  /** Where the loaded causes sit, or null when there are none to place. */
  areas: string | null;
}

const plural = (n: number, one: string, many: string) => (n === 1 ? one : many);

const UNIT: Record<PerformanceScope, [string, string]> = {
  now: ["change worth making now", "changes worth making now"],
  production: ["production cause", "production causes"],
  all: ["cause across all code", "causes across all code"],
};

/**
 * The figure is the scope's total under its filters, the same number the
 * actionability bar sums to; the area sentence describes the loaded page.
 */
export function verdictOf(
  scope: PerformanceScope,
  total: number,
  groups: readonly PerformanceAreaGroup[],
): Verdict {
  const [one, many] = UNIT[scope];
  const lead = groups[0];
  return {
    value: total,
    unit: plural(total, one, many),
    areas: !lead
      ? null
      : groups.length === 1
        ? `All in ${areaLabel(lead.area)}.`
        : `Across ${groups.length} areas, led by ${areaLabel(lead.area)}.`,
  };
}

export function PerformanceVerdict({
  verdict,
  analyzedCommit,
  children,
}: {
  verdict: Verdict;
  analyzedCommit: string | null | undefined;
  /** The accounting sentence for the scope. */
  children: ReactNode;
}) {
  return (
    <PageLede
      label="Performance"
      labelHint="Static, high-precision signals read from the code's shape, not measured latency. Repeated call paths fold into one cause, one place to edit, so a row answers what to change once and every observation stays readable as evidence. Evidence confidence, actionability and fix safety are separate judgements."
      value={verdict.value.toLocaleString()}
      unit={verdict.unit}
    >
      {children}
      {verdict.areas || analyzedCommit ? (
        <p className="text-caption text-[var(--color-text-tertiary)]">
          {verdict.areas}
          {analyzedCommit ? (
            <>
              {verdict.areas ? " " : ""}Analysed at{" "}
              <span className="font-mono">{analyzedCommit.slice(0, 7)}</span>.
            </>
          ) : null}
        </p>
      ) : null}
    </PageLede>
  );
}
