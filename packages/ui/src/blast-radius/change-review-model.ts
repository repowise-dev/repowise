/**
 * How a change's `review` block (the score and tests `repowise risk` and
 * `get_change_risk` give) reads on the Review page. Pure, so the screen and
 * the review prompt say the same thing.
 *
 * The one rule here: a lane that was not measured is said as not measured,
 * never left out, so a failed read never passes for a clean one.
 */

import type { ChangeReview, ChangeRiskBlock, ChangeTestsBlock } from "@repowise-dev/types/blast-radius";
import type { ReviewPriority } from "@repowise-dev/types/git";
import { describeDriver } from "../commits/risk-driver-breakdown";
import { formatNumber } from "../lib/format";
import { count } from "./review-status";

export interface RiskLede {
  /** Repo-relative priority; null when there was no baseline to rank against. */
  priority: ReviewPriority | null;
  /** What the score means, in one sentence. */
  sentence: string;
  /** The strongest drivers that raised it, or null. */
  drivers: string | null;
  /** The scale, for a hover. */
  detail: string;
}

const PRIORITIES = new Set<string>(["low", "moderate", "high"]);

/** The score demoted and led by what it means: a rank against this repo's own commits. */
export function riskLede(risk: ChangeRiskBlock): RiskLede {
  const priority = PRIORITIES.has(risk.review_priority ?? "") ? (risk.review_priority as ReviewPriority) : null;
  const score = risk.score.toFixed(1);
  const sentence =
    risk.risk_percentile != null
      ? `Riskier than ${Math.round(risk.risk_percentile)}% of this repo's recent commits, by size and spread.`
      : `Change risk ${score} of 10, by size and spread.`;
  const raising = risk.drivers.filter((d) => d.value !== null && d.contribution > 0).slice(0, 2);
  return {
    priority,
    sentence,
    drivers: raising.length > 0 ? `Mostly ${raising.map(describeDriver).join(" and ")}.` : null,
    detail: `Change risk ${score} of 10 on a fixed scale calibrated across other repositories. It measures the diff's size and spread, not bugs.`,
  };
}

/** How the tests step reads: run a list, run everything, or nothing needs to run. */
export type TestsPlan =
  | {
      kind: "list";
      tests: string[];
      /** Every test to run, the ones counted apart included. */
      total: number;
      commands: string[];
      measured: boolean;
      /** Tests that still run but are counted apart (kept by a conftest, or reaching the change only through imports). */
      apart: number;
      summary: string;
    }
  | { kind: "run_all"; reasons: string[]; startWith: string[] }
  | { kind: "none"; summary: string };

/** Statuses where a selection was made; any other status is a selection that did not run. */
const SELECTED = new Set(["selected", "degraded", "run_all"]);

export function testsPlan(review: ChangeReview): TestsPlan {
  const t = review.tests;
  if (t.run_all || !SELECTED.has(t.status))
    return { kind: "run_all", reasons: t.reasons.filter(Boolean), startWith: t.tests_to_run };
  // Counted apart from `total` by the selection, but they still run.
  const apart = (t.broadcast_total ?? 0) + (t.import_closure_total ?? 0);
  if (t.tests_to_run.length === 0 && apart === 0) return { kind: "none", summary: t.summary };
  return {
    kind: "list",
    tests: t.tests_to_run,
    total: t.total + apart,
    commands: review.test_commands ?? [],
    measured: t.basis === "measured",
    apart,
    summary: t.summary,
  };
}

/** One area the review could not check fully, and why. */
export interface Lane {
  key: string;
  name: string;
  /** "not measured", "partly measured", "not checked yet"... always words, never only a colour. */
  state: string;
  reason: string | null;
}

/** Where the health check stands for the change on screen. */
export type HealthLane = "unsupported" | "not_run" | "running" | "done" | "error" | "not_a_change";

export interface LaneFacts {
  /** The `review` block; undefined for a hand-picked file set, null when the server could not build one. */
  review: ChangeReview | null | undefined;
  reviewUnavailable?: string | null | undefined;
  health: HealthLane;
  /** Changed files new since the last index run. */
  notIndexed: number;
}

function testsLane(t: ChangeTestsBlock): Lane | null {
  if (t.status === "selected") return null;
  if (t.status === "degraded") return { key: "tests", name: "Test selection", state: "partly measured", reason: t.summary };
  if (t.status === "run_all")
    return { key: "tests", name: "Test selection", state: "could not narrow it", reason: t.reasons[0] ?? t.summary };
  return { key: "tests", name: "Test selection", state: "not measured", reason: t.summary };
}

function riskLanes(risk: ChangeRiskBlock): Lane[] {
  const out: Lane[] = [];
  if (risk.risk_percentile == null)
    out.push({
      key: "rank",
      name: "Change risk",
      state: "not ranked",
      reason: `${count(risk.baseline_sample_size, "scored commit")} in this repo, too few to rank against`,
    });
  if (!risk.fix_history.available)
    out.push({ key: "fixes", name: "Earlier bug fixes in these files", state: "not measured", reason: "the index holds no fix history" });
  return out;
}

const HEALTH_LANE: Partial<Record<HealthLane, Lane>> = {
  unsupported: {
    key: "health",
    name: "Code health",
    state: "not checked",
    reason: "the health check reads both sides of the change from a checkout, and this server has none",
  },
  not_run: { key: "health", name: "Code health", state: "not checked yet", reason: null },
  error: { key: "health", name: "Code health", state: "not measured", reason: "the health check failed; its error is under Health check" },
};

/**
 * The areas that were not measured, or only in part, in a fixed order. A
 * lane that measured cleanly is not listed: that is not news, and a lane that
 * did not measure is the one thing a reader cannot infer from the rest.
 */
export function notMeasured(f: LaneFacts): Lane[] {
  const out: Lane[] = [];
  if (f.review === undefined)
    out.push({
      key: "review",
      name: "Change risk and test selection",
      state: "not measured",
      reason: "a hand-picked file set has no diff to score; pick a change for them",
    });
  else if (f.review === null)
    out.push({
      key: "review",
      name: "Change risk and test selection",
      state: "not measured",
      reason: f.reviewUnavailable?.replace(/\.$/, "") || "the server did not score this change",
    });
  else {
    out.push(...riskLanes(f.review.risk));
    const tests = testsLane(f.review.tests);
    if (tests) out.push(tests);
  }
  if (f.notIndexed > 0)
    out.push({
      key: "index",
      name: "Reach, health and owners",
      state: "partly measured",
      reason: `${count(f.notIndexed, "changed file")} ${f.notIndexed === 1 ? "is" : "are"} new since the last index run`,
    });
  const health = HEALTH_LANE[f.health];
  if (health) out.push(health);
  return out;
}

/** The tests figure above the fold, from the review when there is one. */
export function testsFigure(review: ChangeReview | null | undefined, blastTotal: number): { figure: string; runAll: boolean; total: number } {
  if (!review) return { figure: formatNumber(blastTotal), runAll: false, total: blastTotal };
  const plan = testsPlan(review);
  if (plan.kind === "run_all") return { figure: "All", runAll: true, total: 0 };
  const total = plan.kind === "list" ? plan.total : 0;
  return { figure: formatNumber(total), runAll: false, total };
}
