"use client";

import type { PerformanceActionabilityState } from "@repowise-dev/types/health";

import { ProportionBar } from "../../shared/proportion-bar";
import { Segmented, type SegmentOption } from "../../shared/segmented";
import type { ActionabilityCounts } from "./grouping";
import { ACTIONABILITY_HINT, ACTIONABILITY_LABEL } from "./presentation";

/**
 * The scope's split by actionability, as one bar and the one control that
 * narrows by it. The server counts it inside the scope and every other
 * filter, so the segments sum to the lede's figure and a segment's count is
 * exactly what choosing it lists.
 */

const STATES: PerformanceActionabilityState[] = ["plan_ready", "advisory", "investigate", "expected"];

/** One neutral family, darkest where the change is surest; the segment names
 *  carry the meaning, so no state borrows a health colour. */
const STATE_COLOR: Record<PerformanceActionabilityState, string> = {
  plan_ready: "var(--color-text-primary)",
  advisory: "var(--color-text-secondary)",
  investigate: "var(--color-text-tertiary)",
  expected: "var(--color-border-default)",
};

type Choice = PerformanceActionabilityState | "all";

export function ActionabilityBar({
  counts,
  value,
  onChange,
}: {
  /** Server counts inside the scope and the other filters. */
  counts: ActionabilityCounts;
  value: PerformanceActionabilityState | null;
  onChange: (state: PerformanceActionabilityState | null) => void;
}) {
  const total = STATES.reduce((sum, state) => sum + counts[state], 0);
  // Only the states this scope holds, and the chosen one, which keeps its
  // place so it can be cleared; an empty segment would name nothing to show.
  const states = STATES.filter((state) => counts[state] > 0 || value === state);
  const options: SegmentOption<Choice>[] = [
    { value: "all", label: "All", count: total.toLocaleString() },
    ...states.map((state) => ({
      value: state,
      label: ACTIONABILITY_LABEL[state],
      count: counts[state].toLocaleString(),
      hint: ACTIONABILITY_HINT[state],
    })),
  ];
  // One segment holding everything says nothing the counts below do not.
  const split = states.filter((state) => counts[state] > 0).length > 1;
  if (!split && value === null) return null;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      {split ? (
        <ProportionBar
          label="Causes by actionability"
          legend={false}
          sort={false}
          size="sm"
          segments={states.map((state) => ({
            key: state,
            label: ACTIONABILITY_LABEL[state],
            value: counts[state],
            color: STATE_COLOR[state],
          }))}
        />
      ) : null}
      <Segmented
        label="Actionability"
        value={value ?? "all"}
        options={options}
        onChange={(next) => onChange(next === "all" ? null : next)}
      />
    </div>
  );
}
