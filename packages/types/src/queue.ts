/**
 * The one count vocabulary every code-health queue reports
 * (`analysis/health/queue/counts.py` in core), for findings, performance
 * causes, refactoring plans and Fix first items alike.
 */

/**
 * Why a unit is out of a default queue (`queue.eligibility.Reason`), plus the
 * stored-judgement reasons (`queue.counts.STORED_REASONS`): `not_judged` (stored
 * before units were judged), `covered_by_plan`, `not_file_lead`, `not_in_queue`.
 */
export type QueueReason =
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
  | "kind_unaudited"
  | "unmeasured_cost"
  | "cold_role"
  | "background_unproven"
  | "not_judged"
  | "covered_by_plan"
  | "not_file_lead"
  | "not_in_queue";

/** One noun per unit: findings (observations), causes (performance), plans (refactoring), items (Fix first). */
export type QueueUnit = "findings" | "causes" | "plans" | "items";

/**
 * `inventory` every open unit; `in_scope` those whose code is in scope (not
 * test, tooling, vendored, generated or docs); `eligible` those a default
 * queue holds; `due` eligible units in tier now or next; `shown` what the
 * response carries; `excluded` the non-zero reasons, so the levels add up.
 */
export interface QueueCounts {
  inventory: number;
  in_scope: number;
  eligible: number;
  due: number;
  shown: number;
  excluded: Partial<Record<QueueReason, number>>;
}

/** Every unit's counts, by noun (`queue_counts` on the health overview and `repowise health --format json`). */
export type QueueCountsByUnit = Record<QueueUnit, QueueCounts>;
