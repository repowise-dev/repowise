/**
 * The change review of the last turn: whether it edited files, and what
 * `get_change_risk` said about the working tree afterwards. Pure.
 */

import type { RiskReportArtifactData } from "@repowise-dev/types";

/** The `get_change_risk` result, as the shared chat types describe it. */
export type ChangeRisk = RiskReportArtifactData;

export type ReviewOutcome =
  | { phase: "none" }
  | { phase: "reviewing" }
  | { phase: "done"; risk: ChangeRisk }
  | { phase: "failed"; reason: "error" | "timeout"; message: string };

export interface ReviewState {
  /** Claude (or a subagent) edited a file since the main turn started. */
  edited: boolean;
  outcome: ReviewOutcome;
}

export type ReviewAction =
  | { type: "turnStarted" }
  | { type: "fileEdited" }
  | { type: "reviewStarted" }
  | { type: "reviewed"; risk: ChangeRisk }
  | { type: "reviewFailed"; reason: "error" | "timeout"; message: string };

export const initialReview: ReviewState = { edited: false, outcome: { phase: "none" } };

export function reduceReview(state: ReviewState, action: ReviewAction): ReviewState {
  switch (action.type) {
    case "turnStarted":
      // A new turn retires the last review and drops one still in flight.
      return state.edited || state.outcome.phase !== "none" ? initialReview : state;
    case "fileEdited":
      return state.edited ? state : { ...state, edited: true };
    case "reviewStarted":
      return { ...state, outcome: { phase: "reviewing" } };
    case "reviewed":
      return { ...state, outcome: { phase: "done", risk: action.risk } };
    case "reviewFailed":
      return { ...state, outcome: { phase: "failed", reason: action.reason, message: action.message } };
  }
}

/** The tools that write a file. */
const EDIT_TOOLS = new Set(["Edit", "MultiEdit", "Write", "NotebookEdit"]);

/**
 * A tool call wrote a file: an edit tool, answered without a deny or an error.
 * Lens's own calls (`toolu_plugin_`) never count.
 */
export function isFileEdit(e: { tool?: unknown; tool_use_id?: unknown }, result: unknown): boolean {
  if (typeof e.tool !== "string" || !EDIT_TOOLS.has(e.tool)) return false;
  if (typeof e.tool_use_id === "string" && e.tool_use_id.startsWith("toolu_plugin_")) return false;
  if (typeof result !== "object" || result === null) return false;
  const r = result as { deny?: unknown; isError?: unknown };
  return r.deny === undefined && r.isError !== true;
}

/** The tests the review names, with how it knows them; null when it names none. */
export function testsToRun(
  risk: ChangeRisk,
): { tests: string[]; total: number; truncated: boolean; files: boolean; measured: boolean } | null {
  const block = risk.impacted_tests;
  const tests = block?.tests_to_run ?? [];
  if (block === undefined || tests.length === 0) return null;
  return {
    tests,
    total: block.total ?? tests.length,
    truncated: block.truncated === true,
    files: block.tests_to_run_kind === "test_file",
    measured: block.basis === "measured",
  };
}
