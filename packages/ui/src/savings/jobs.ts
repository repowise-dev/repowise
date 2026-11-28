/**
 * The plain jobs savings are grouped into on the page.
 *
 * An operation is whatever the recording surface named it: a distill filter,
 * a replacement hook, or an MCP tool. Those names read as identifiers, so the
 * page groups them into five jobs a developer would recognise. This map is the
 * one place that grouping lives; a host renders the jobs, it never regroups.
 */
import type { SavingsBreakdownView } from "./types";

export type SavingsJobId = "trim" | "search" | "checks" | "answers" | "rereads";

/** Ranked order when two jobs tie, and the order of empty rows. */
export const SAVINGS_JOB_IDS: readonly SavingsJobId[] = [
  "trim",
  "search",
  "checks",
  "answers",
  "rereads",
];

const JOB_BY_OPERATION: Readonly<Record<string, SavingsJobId>> = {
  // Distill filters: command output trimmed before the agent read it.
  build_output: "trim",
  file_listing: "trim",
  file_read: "trim",
  git_diff: "trim",
  git_diff_stat: "trim",
  git_log: "trim",
  git_status: "trim",
  lint_output: "trim",
  logs: "trim",
  test_output: "trim",
  install_output: "trim",
  // Transcript-mined omissions whose filter was not recorded. Every one in a
  // real ledger is on the distill surface: command output, not a question.
  unknown: "trim",
  // Searches answered in place of grep.
  search_results: "search",
  search_digest: "search",
  search_codebase: "search",
  // Risk and review checks.
  get_risk: "checks",
  get_change_risk: "checks",
  get_why: "checks",
  get_health: "checks",
  get_dead_code: "checks",
  get_security: "checks",
  get_blast_radius: "checks",
  get_conformance: "checks",
  // Code questions.
  get_answer: "answers",
  get_context: "answers",
  get_symbol: "answers",
  get_overview: "answers",
  // Files already read this session.
  read_reread: "rereads",
  read_skeleton: "rereads",
};

/**
 * The job an operation belongs to. The operation set is open (any MCP tool
 * records under its own name), so an unknown name falls into "answers": a new
 * tool is far more often a question answered from the index than a new kind
 * of command output, and the distill filters are listed above by name.
 */
export function jobForOperation(operation: string | null | undefined): SavingsJobId {
  return (operation && JOB_BY_OPERATION[operation]) || "answers";
}

export interface SavingsJobTotal {
  id: SavingsJobId;
  events: number;
  savedInputTokens: number;
  pricedInputUsd: number;
}

/** What a job row leads with, and so what it is ranked and sized by. */
export type SavingsJobMeasure = "usd" | "tokens";

/** A job's figure under one measure. */
export function jobValue(job: SavingsJobTotal, by: SavingsJobMeasure): number {
  return by === "usd" ? job.pricedInputUsd : job.savedInputTokens;
}

/** Fold the report's per-operation rows into one total per job, largest first
 *  by *by*, then by the other measure, events and a fixed job order. Every job
 *  is returned, empty ones last, so a host can say which are not set up rather
 *  than silently dropping them. */
export function groupByJob(
  rows: readonly SavingsBreakdownView[],
  by: SavingsJobMeasure = "tokens",
): SavingsJobTotal[] {
  const totals = new Map<SavingsJobId, SavingsJobTotal>(
    SAVINGS_JOB_IDS.map((id) => [id, { id, events: 0, savedInputTokens: 0, pricedInputUsd: 0 }]),
  );
  for (const row of rows) {
    const total = totals.get(jobForOperation(row.group)) as SavingsJobTotal;
    total.events += row.events ?? 0;
    total.savedInputTokens += row.saved_input_tokens ?? 0;
    total.pricedInputUsd += row.priced_input_savings_usd ?? 0;
  }
  return [...totals.values()].sort(
    (a, b) =>
      jobValue(b, by) - jobValue(a, by) ||
      jobValue(b, by === "usd" ? "tokens" : "usd") - jobValue(a, by === "usd" ? "tokens" : "usd") ||
      b.events - a.events ||
      SAVINGS_JOB_IDS.indexOf(a.id) - SAVINGS_JOB_IDS.indexOf(b.id),
  );
}
