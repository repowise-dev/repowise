import {
  PERF_BOUNDARY_LABEL,
  type PerformanceActionabilityState,
  type PerformanceCostProof,
  type PerformanceExecutionContext,
  type PerformanceFacetKey,
  type PerformanceOpportunity,
  type PerformanceOpportunityConfidence,
  type PerformanceOpportunitySibling,
  type PerformanceWhyRanked,
} from "@repowise-dev/types/health";
import type { C4IoKind } from "@repowise-dev/types/external-systems";

import { biomarkerLabel } from "../biomarker-glossary";
import { CONFIDENCE_LABEL } from "../labels";

/**
 * Display derivations for the performance queue. Everything here reads fields
 * the server already decided; nothing re-groups, re-ranks, re-scores, or
 * re-links a plan. If a value needs a rule, the rule belongs on the server.
 */

const CONTEXT_LABEL: Record<PerformanceExecutionContext, string> = {
  production: "Production",
  tooling: "Tooling",
  test: "Test suite",
  unknown: "Unclassified",
};

/** The four canonical contexts, in the order the tab presents them. */
export const CONTEXT_ORDER: PerformanceExecutionContext[] = [
  "production",
  "tooling",
  "test",
  "unknown",
];

export const CONTEXT_HINT: Record<PerformanceExecutionContext, string> = {
  production: "Runtime paths",
  tooling: "Build and developer paths",
  test: "Test execution cost",
  unknown: "No context could be classified",
};

export const ACTIONABILITY_LABEL: Record<PerformanceActionabilityState, string> = {
  plan_ready: "Plan ready",
  advisory: "Advisory",
  investigate: "Needs investigation",
  expected: "Expected",
};

export const ACTIONABILITY_HINT: Record<PerformanceActionabilityState, string> = {
  plan_ready: "A named intervention the analysis considers safe to apply.",
  advisory: "A coherent intervention, but the analysis cannot prove it is safe.",
  investigate: "Evidence worth reading before any change is proposed.",
  expected: "The repetition is real and there is no change to make.",
};

export { CONFIDENCE_LABEL };

export const FACET_LABEL: Record<PerformanceFacetKey, string> = {
  context: "Context",
  boundary: "Boundary",
  confidence: "Evidence confidence",
  actionability: "Actionability",
  plan_state: "Plan",
  proof: "Cost",
  role: "Runs in",
};

const PROOF_LABEL: Record<PerformanceCostProof, string> = {
  proven: "Measured",
  unproven: "Unproven: loop size unknown",
  background_unproven: "Unproven: scheduled job, growth not shown",
};

// "Stored plan", not "Plan ready": that name belongs to the actionability
// state, which the Plan ready tile counts. A stored plan is counted by the
// "With a stored plan" tile, so the row mark and its tile share one predicate.
const PLAN_STATE_LABEL: Record<string, string> = {
  available: "Stored plan",
  no_safe_plan: "No safe plan",
  not_persisted: "Needs an index refresh",
};

/**
 * Where the edit goes. The wire type gains `intervention_kind` with the
 * one-opportunity-per-intervention model (PR 2817); until that lands it is read
 * as optional here, and a row without it simply shows no kind.
 */
export type InterventionKind = "function" | "shared_helper" | "module";

export const INTERVENTION_KIND_LABEL: Record<InterventionKind, string> = {
  function: "The loop's own function",
  shared_helper: "A helper every caller shares",
  module: "Top-level code",
};

export function interventionKind(opportunity: PerformanceOpportunity): InterventionKind | null {
  const kind = (opportunity as { intervention_kind?: string | null }).intervention_kind;
  return kind && kind in INTERVENTION_KIND_LABEL ? (kind as InterventionKind) : null;
}

/** How far above a call the excerpt looks for the loop that repeats it. */
export const LOOP_SEARCH_LINES = 15;

const LOOP_HEADER = /^\s*(?:async\s+for|for|while)\b/;

/**
 * The loop header above a call, for the excerpt. The evidence stores the
 * call's line, not the loop's, so this scans upward for the nearest
 * `for` / `while` / `async for` line and says so when there is none in range.
 */
export function loopHeaderAbove(
  fileLines: string[],
  callLine: number,
  maxUp = LOOP_SEARCH_LINES,
): { start: number; note: string | null } {
  for (let line = callLine; line >= Math.max(1, callLine - maxUp); line -= 1) {
    if (LOOP_HEADER.test(fileLines[line - 1] ?? "")) {
      return { start: line, note: `The loop starts at line ${line}.` };
    }
  }
  return {
    start: callLine,
    note: `No for or while line within ${maxUp} lines above the call; the loop is further up or in a caller.`,
  };
}

const MODULE_SCOPE = "::__module__";

/**
 * `path::__module__` names top-level code, which has no function to call it
 * by. It reads as "module scope of path"; `short` keeps only the file name,
 * for a title.
 */
export function moduleScopeLabel(symbol: string, short = false): string | null {
  if (!symbol.endsWith(MODULE_SCOPE)) return null;
  const file = symbol.slice(0, -MODULE_SCOPE.length);
  return `module scope of ${short ? (file.split("/").pop() ?? file) : file}`;
}

/**
 * What each facet means, for the drawer's tooltips. The band edges mirror
 * `_LEVERAGE_BANDS` and `_CHANGE_RISK_BANDS` in core's `opportunity_rank.py`;
 * change both together.
 */
export const FACET_GLOSSARY = {
  amplification:
    "How the cost repeats. Per iteration: once per loop pass. Quadratic: nested loops over the same data. Per call: once each time the function runs.",
  exposure:
    "Whether an entry point reaches the loop's function in the call graph. Unknown when the graph could not say either way.",
  leverage:
    "How many call sites one fix settles. Isolated is 1, local up to 3, shared up to 9, broad 10 or more.",
  change_risk:
    "How many files holding evidence the edit reaches. Contained is 1, moderate up to 4, wide 5 or more.",
  loop_magnitude:
    "Whether the loop's trip count grows with the data. A bounded loop runs a fixed number of times.",
} as const;

/** The facet value used when an opportunity crosses no I/O boundary. */
const NO_BOUNDARY = "none";

/** Turn a machine token into readable words without inventing a vocabulary. */
export function humanizeToken(token: string): string {
  const words = token.replaceAll("_", " ").replaceAll("-", " ").trim();
  if (!words) return "Unknown";
  return words[0]!.toUpperCase() + words.slice(1);
}

export function contextLabel(context: PerformanceExecutionContext): string {
  return CONTEXT_LABEL[context] ?? CONTEXT_LABEL.unknown;
}

/** The boundary as a label; `none` is a real answer, not a missing one. */
export function boundaryLabel(boundary: C4IoKind | string | null | undefined): string {
  if (!boundary || boundary === NO_BOUNDARY) return "In-process";
  const known: Partial<Record<string, string>> = PERF_BOUNDARY_LABEL;
  return known[boundary] ?? humanizeToken(boundary);
}

/** The boundary as a noun that reads inside a sentence. */
function boundaryNoun(boundary: C4IoKind | null | undefined): string {
  if (!boundary) return "Repeated";
  const known: Partial<Record<string, string>> = PERF_BOUNDARY_LABEL;
  return known[boundary]?.toLowerCase() ?? "repeated";
}

export function facetValueLabel(facet: PerformanceFacetKey, value: string): string {
  if (facet === "context") return contextLabel(value as PerformanceExecutionContext);
  if (facet === "boundary") return boundaryLabel(value);
  if (facet === "confidence") return CONFIDENCE_LABEL[value as PerformanceOpportunityConfidence] ?? humanizeToken(value);
  if (facet === "actionability")
    return ACTIONABILITY_LABEL[value as PerformanceActionabilityState] ?? humanizeToken(value);
  if (facet === "proof") return PROOF_LABEL[value as PerformanceCostProof] ?? humanizeToken(value);
  if (facet === "role") return humanizeToken(value);
  return PLAN_STATE_LABEL[value] ?? humanizeToken(value);
}

/**
 * Cause phrasings for the markers whose meaning changes with the boundary.
 * Every other marker uses its glossary label, so the taxonomy stays in one
 * place and only genuinely boundary-sensitive sentences are written twice.
 */
const CAUSE_BY_MARKER: Record<string, (boundary: C4IoKind | null) => string> = {
  io_in_loop: (b) => `${b ? boundaryNoun(b) : "I/O"} call inside a loop`,
  nested_loop_with_io: (b) => `${b ? boundaryNoun(b) : "I/O"} call inside a nested loop`,
  hot_path_sync_io: (b) => `Blocking ${b ? boundaryNoun(b) : "I/O"} call on a hot path`,
  serial_await_in_loop: (b) => `${b ? boundaryNoun(b) : "Awaited"} call awaited one at a time in a loop`,
  blocking_io_under_lock: (b) => `${b ? boundaryNoun(b) : "I/O"} call made while a lock is held`,
  resource_construction_in_loop: (b) =>
    `${b ? boundaryNoun(b) : "Resource"} client constructed on every iteration`,
};

/** The cause in words: "Database call inside a loop". */
export function opportunityCause(opportunity: PerformanceOpportunity): string {
  const cause = CAUSE_BY_MARKER[opportunity.biomarker_type];
  const phrase = cause
    ? cause(opportunity.boundary_kind)
    : biomarkerLabel(opportunity.biomarker_type);
  return phrase[0]!.toUpperCase() + phrase.slice(1);
}

/**
 * The short name of the place to change: the intervention symbol without its
 * path, else the first caller, else the file's name. It is what separates two
 * rows that share a cause; the full path stays in the evidence line.
 */
export function opportunitySubject(opportunity: PerformanceOpportunity): string {
  const symbol = opportunity.intervention_symbol?.trim();
  if (symbol) return moduleScopeLabel(symbol, true) ?? symbol.split("::").pop() ?? symbol;
  const first = opportunity.evidence[0]?.function_name;
  if (first) return first;
  return opportunity.file_path.split("/").pop() ?? opportunity.file_path;
}

/**
 * The row's title: the cause, then where. Every database loop in a repository
 * used to share one title ("Database call inside a loop" on 273 rows), so the
 * part that told rows apart was the mono line nobody reads first.
 */
export function opportunityTitle(opportunity: PerformanceOpportunity): string {
  return `${opportunityCause(opportunity)} in ${opportunitySubject(opportunity)}`;
}

/**
 * The machine evidence under the title: the sink the paths converge on, or the
 * symbol worth editing, or the file. Whichever exists is monospace.
 */
export function opportunityEvidenceLine(opportunity: PerformanceOpportunity): string {
  const sink = opportunity.terminal_sink?.trim();
  if (sink) return sink;
  const symbol = opportunity.intervention_symbol?.trim();
  if (symbol) return moduleScopeLabel(symbol) ?? symbol;
  const first = opportunity.evidence[0];
  if (first?.function_name) return `${opportunity.file_path}::${first.function_name}`;
  return opportunity.file_path;
}

/** `12 call sites across 3 files`, with the singulars right. */
export function affectedSummary(opportunity: PerformanceOpportunity): string {
  const sites = opportunity.affected_call_sites_total;
  const files = opportunity.affected_files_total;
  const sitePart = `${sites.toLocaleString()} call site${sites === 1 ? "" : "s"}`;
  const filePart = `${files.toLocaleString()} file${files === 1 ? "" : "s"}`;
  return `${sitePart} across ${filePart}`;
}

const ROLE_PHRASE: Record<string, string> = {
  request: "runs on every request",
  event_consumer: "runs on every message",
  scheduled_job: "runs on a schedule",
  unknown: "no evidence of what runs it",
};

const MULTIPLIER_PHRASE: Record<string, string> = {
  io_in_loop: "runs once per loop iteration",
  serial_await_in_loop: "awaited one at a time",
  nested_loop_with_io: "runs inside nested loops",
};

/**
 * A rank factor as a reader's reason, without the points: "8 call sites",
 * "reachable from an entry point". The points stay in `whyRankedLabel`, which
 * the drawer shows for anyone checking the arithmetic.
 */
export function whyRankedPhrase(factor: PerformanceWhyRanked): string {
  const value = factor.value;
  switch (factor.factor) {
    case "affected_call_sites":
      return typeof value === "number"
        ? `${value.toLocaleString()} call site${value === 1 ? "" : "s"}`
        : "several call sites";
    case "boundary_kind":
      return !value || value === NO_BOUNDARY
        ? "in-process work"
        : `a ${boundaryLabel(String(value)).toLowerCase()} call`;
    case "multiplier_shape":
      return MULTIPLIER_PHRASE[String(value)] ?? humanizeToken(String(value));
    case "execution_context":
      return `${contextLabel(String(value) as PerformanceExecutionContext).toLowerCase()} code`;
    case "entry_reachability":
      return "reachable from an entry point";
    case "execution_role":
      return ROLE_PHRASE[String(value)] ?? humanizeToken(String(value));
    case "loop_magnitude":
      return value === "grows_with_data" ? "grows with the data" : humanizeToken(String(value));
    case "provenance":
      return value === "call-site" ? "seen at the call site" : humanizeToken(String(value));
    default:
      return whyRankedLabel(factor);
  }
}

/** `Multiplier shape: serial await in loop (+4)`, capped by the server at three. */
export function whyRankedLabel(factor: PerformanceWhyRanked): string {
  const name = humanizeToken(factor.factor);
  const sign = factor.points >= 0 ? "+" : "";
  if (factor.value === null || factor.value === "" || typeof factor.value === "boolean") {
    return `${name} (${sign}${factor.points})`;
  }
  const value = typeof factor.value === "number" ? factor.value.toLocaleString() : humanizeToken(String(factor.value));
  return `${name}: ${value} (${sign}${factor.points})`;
}

export interface PlanPresentation {
  label: string;
  detail: string;
  /** True only for a plan the server says exists and the client verified. */
  actionable: boolean;
}

/**
 * Plan state as the server reported it. The reason is the server's; this only
 * chooses the heading it sits under.
 */
export function planPresentation(opportunity: PerformanceOpportunity): PlanPresentation {
  if (opportunity.plan_status === "available" && opportunity.plan_id) {
    return {
      label: PLAN_STATE_LABEL.available!,
      detail: opportunity.plan_reason,
      actionable: true,
    };
  }
  if (opportunity.plan_status === "not_persisted") {
    return {
      label: "Needs an index refresh",
      detail: opportunity.plan_reason,
      actionable: false,
    };
  }
  return { label: "No safe plan", detail: opportunity.plan_reason, actionable: false };
}

/**
 * The copy that tells an agent which drill-down to call. Quotes the opportunity
 * id because that is the id the agent surface resolves.
 */
export function agentHandoffCall(opportunityId: string): string {
  return `get_health(opportunity_id="${opportunityId}")`;
}

/** A sibling's fix, in words, for the drawer's "also flagged" line. */
export function siblingFixLabel(sibling: PerformanceOpportunitySibling): string {
  return humanizeToken(sibling.strategy ?? sibling.biomarker_type);
}
