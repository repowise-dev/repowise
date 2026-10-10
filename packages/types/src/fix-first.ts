/**
 * Fix first: one ranked list of what to fix, built once in core from stored rows.
 *
 * Mirrors `repowise.core.analysis.health.fix_first.model`.
 * `test_wire_vocabulary_parity.py` fails when the unions below and their
 * Python twins disagree.
 */

import type { ActionCommand } from "./actions.js";

export type FixTier = "now" | "next" | "later";

export type FixKind = "refactor" | "perf_fix" | "finding";

export type FixImproves = "defect" | "maintainability" | "performance";

export type FixGainKind = "health_points" | "performance";

export type FixEffort = "S" | "M" | "L" | "XL";

export type FixLevel = "high" | "medium" | "low";

/** `unknown` is printed as a fact, never read as zero. */
export type FixFactBasis = "measured" | "inferred" | "unknown";

/** Why a unit of work is not in the queue; each is counted in `totals.excluded`. */
export type FixExclusion =
  | "test"
  | "tooling"
  | "unknown"
  | "generated"
  | "expected"
  | "no_strategy"
  | "no_plan"
  | "below_min_worth"
  | "history_only"
  | "vendored"
  | "docs_example"
  | "deprecated"
  | "gated_off"
  | "cold_path"
  | "unreachable"
  | "inherent_dispatch"
  | "small_function"
  | "no_concrete_step"
  | "low_value_kind"
  | "kind_unaudited";

/** `all` keeps test files, labelled in `context`. */
export type FixScope = "production" | "all";

export interface FixTarget {
  file_path: string;
  symbol: string | null;
  line_start: number | null;
  line_end: number | null;
}

export interface FixFact {
  label: string;
  value: string;
  basis: FixFactBasis;
}

export interface FixStep {
  order: number;
  text: string;
  file_path: string;
  line: number | null;
  mechanical: boolean;
}

export interface FixItem {
  /** `fix1_<20 hex>`: stable while the item's source id is. */
  id: string;
  rank: number;
  tier: FixTier;
  kind: FixKind;
  improves: FixImproves;
  /** Imperative, at most 90 characters, no biomarker ids. */
  title: string;
  target: FixTarget;
  /** One plain sentence: the problem, and why it matters here. */
  why: string;
  facts: FixFact[];
  action: { summary: string; steps: FixStep[]; steps_total: number; mechanical: boolean };
  gain: { kind: FixGainKind; value: number | null; text: string };
  effort: { bucket: FixEffort; basis: string };
  risk: { level: FixLevel; dependents: number | null; files_touched: number; text: string };
  confidence: { level: FixLevel; reason: string };
  verify: {
    tests: { path: string; reason: string }[];
    tests_total: number;
    command: string | null;
    basis: FixFactBasis;
  };
  /** History signals: shown beside the item, never ranked on. */
  context: { label: string; value: string }[];
  source: { opportunity_id: string | null; plan_ids: string[]; finding_ids: string[] };
  next_call: ActionCommand;
  /** The value inputs and the tier reason, so the order is explainable. */
  why_ranked: { factor: string; value: string }[];
}

/** The projection a list renders; the full item is one lookup away. */
export interface FixItemCompact {
  id: string;
  tier: FixTier;
  kind: FixKind;
  title: string;
  target: FixTarget;
  why: string;
  gain: string;
  effort: FixEffort;
  confidence: FixLevel;
  next_call: ActionCommand;
}

export interface FixFirstTotals {
  candidates: number;
  eligible: number;
  shown: number;
  excluded: Record<FixExclusion, number>;
  /** Distinct functions with work kept out as `gated_off`: dormant, not gone. */
  dormant: number;
}

/**
 * The five-level count vocabulary (`FixFirstQueue.counts` in core), read on the
 * whole queue: every unit considered, those in scope (not excluded for where
 * the code lives), those that became an item, those due (tier now or next),
 * and those this response shows; `excluded` holds the non-zero exclusions.
 */
export interface FixFirstCounts {
  inventory: number;
  in_scope: number;
  eligible: number;
  due: number;
  shown: number;
  excluded: Partial<Record<FixExclusion, number>>;
}

export interface FixFirstQueue<Item = FixItem> {
  items: Item[];
  /** `items[0]`, or null when nothing is eligible. */
  lead: Item | null;
  totals: FixFirstTotals;
  /** Eligible items per pillar they improve. */
  by_improves: Record<FixImproves, number>;
  model_version: number;
  basis: { analyzed_commit: string | null; health_analyzed_at: string | null };
}
