/**
 * Words for the Fix-first queue. Everything here reads fields core already
 * decided; nothing ranks, groups or counts. If a sentence needs a rule, the
 * rule belongs in `repowise.core.analysis.health.fix_first`.
 */

import type {
  FixExclusion,
  FixFirstQueue,
  FixItem,
  FixTier,
} from "@repowise-dev/types/fix-first";

import { formatNumber } from "../../lib/format";
import { WORK_UNIT_LABEL } from "../labels";

export const TIER_LABEL: Record<FixTier, string> = {
  now: "Now",
  next: "Next",
  later: "Later",
};

/** Core's own reason for the tier, from `why_ranked`. */
export function tierReason(item: Pick<FixItem, "why_ranked">): string | null {
  return item.why_ranked.find((f) => f.factor === "tier")?.value ?? null;
}

/**
 * Each exclusion as it reads after its count, in the order the sentence lists
 * them. Every key of `FixExclusion` is here, so a new rule fails type-check
 * until it has words.
 */
export const EXCLUSION_LABEL: Record<FixExclusion, string> = {
  test: "in tests",
  tooling: "tooling",
  generated: "generated",
  vendored: "vendored",
  docs_example: "docs and examples",
  expected: "expected repetition",
  unknown: "where the code's context is unknown",
  no_strategy: "with no fix strategy",
  no_plan: "with no safe fix",
  below_min_worth: "below the worth floor",
  history_only: "history only",
  deprecated: "deprecated",
  gated_off: "switched off by a constant flag",
  cold_path: "run once per deploy, boot or incident",
  unreachable: "in dead code (delete it)",
  inherent_dispatch: "one long dispatch on a value",
  small_function: "small functions",
  no_concrete_step: "with no concrete first edit",
  low_value_kind: "of a kind rarely worth doing",
  kind_unaudited: "of a kind not yet audited for this list",
};

const EXCLUSION_ORDER: FixExclusion[] = [
  "test",
  "tooling",
  "generated",
  "vendored",
  "docs_example",
  "expected",
  "unknown",
  "no_strategy",
  "no_plan",
  "below_min_worth",
  "history_only",
  "deprecated",
  "gated_off",
  "cold_path",
  "unreachable",
  "inherent_dispatch",
  "small_function",
  "no_concrete_step",
  "low_value_kind",
  "kind_unaudited",
];

/**
 * The header line, exact about scope: how many items are shown out of how
 * many are eligible, and how many functions a constant flag keeps dormant.
 * What each rule left out is listed apart, by {@link exclusionEntries}.
 */
export function fixFirstScopeSentence(queue: FixFirstQueue<unknown>): string {
  const { shown, eligible, dormant } = queue.totals;
  const head = `${formatNumber(shown)} of ${formatNumber(eligible)} eligible item${
    eligible === 1 ? "" : "s"
  }.`;
  return dormant
    ? `${head} Dormant: ${formatNumber(dormant)} function${
        dormant === 1 ? "" : "s"
      } behind a disabled flag.`
    : head;
}

export interface ExclusionEntry {
  reason: FixExclusion;
  count: number;
  /** `612 in tests`. */
  text: string;
}

/** Each nonzero exclusion count with its words, in the sentence order. A zero
 *  is omitted because it excluded nothing. */
export function exclusionEntries(
  counts: Partial<Record<FixExclusion, number>>,
): ExclusionEntry[] {
  return EXCLUSION_ORDER.filter((key) => (counts[key] ?? 0) > 0).map((key) => ({
    reason: key,
    count: counts[key] ?? 0,
    text: `${formatNumber(counts[key] ?? 0)} ${EXCLUSION_LABEL[key]}`,
  }));
}

/**
 * `3 in tests, 12 below the worth floor`: {@link exclusionEntries} as one
 * phrase. Empty when nothing was excluded.
 */
export function exclusionPhrase(counts: Partial<Record<FixExclusion, number>>): string {
  return exclusionEntries(counts)
    .map((entry) => entry.text)
    .join(", ");
}

/** `path:60`, or the path alone when core stored no line. */
export function fixLocation(item: Pick<FixItem, "target">): string {
  const { file_path, line_start } = item.target;
  return line_start ? `${file_path}:${line_start}` : file_path;
}

/** What "Open plan" opens, named for the unit it lands on; null for a finding. */
export function fixPlanLabel(item: Pick<FixItem, "kind">): string | null {
  if (item.kind === "refactor") return `Open the ${WORK_UNIT_LABEL.refactor}`;
  if (item.kind === "perf_fix") return `Open the ${WORK_UNIT_LABEL.perf_fix}`;
  return null;
}
