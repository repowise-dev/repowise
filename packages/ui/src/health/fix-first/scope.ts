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
  generated: "generated or vendored",
  expected: "expected repetition",
  unknown: "where the code's context is unknown",
  no_strategy: "with no fix strategy",
  no_plan: "with no safe fix",
  below_min_worth: "below the worth floor",
  history_only: "history only",
};

const EXCLUSION_ORDER: FixExclusion[] = [
  "test",
  "tooling",
  "generated",
  "expected",
  "unknown",
  "no_strategy",
  "no_plan",
  "below_min_worth",
  "history_only",
];

/**
 * The header line, exact about scope: how many items are shown out of how
 * many are eligible, and what each rule left out. A zero count is omitted
 * because it excluded nothing.
 */
export function fixFirstScopeSentence(queue: FixFirstQueue<unknown>): string {
  const { shown, eligible, excluded } = queue.totals;
  const head = `${formatNumber(shown)} of ${formatNumber(eligible)} eligible item${
    eligible === 1 ? "" : "s"
  }.`;
  const parts = EXCLUSION_ORDER.filter((key) => (excluded[key] ?? 0) > 0).map(
    (key) => `${formatNumber(excluded[key])} ${EXCLUSION_LABEL[key]}`,
  );
  return parts.length ? `${head} Excluded: ${parts.join(", ")}.` : head;
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
