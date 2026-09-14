import {
  PERF_BOUNDARY_LABEL,
  type PerformanceActionabilityState,
  type PerformanceExecutionContext,
  type PerformanceFacetKey,
  type PerformanceOpportunity,
  type PerformanceOpportunityConfidence,
  type PerformanceWhyRanked,
} from "@repowise-dev/types/health";
import type { C4IoKind } from "@repowise-dev/types/external-systems";

import { biomarkerLabel } from "../biomarker-glossary";

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
};

export const ACTIONABILITY_HINT: Record<PerformanceActionabilityState, string> = {
  plan_ready: "A named intervention the analysis considers safe to apply.",
  advisory: "A coherent intervention, but the analysis cannot prove it is safe.",
  investigate: "Evidence worth reading before any change is proposed.",
};

export const CONFIDENCE_LABEL: Record<PerformanceOpportunityConfidence, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
};

export const FACET_LABEL: Record<PerformanceFacetKey, string> = {
  context: "Context",
  boundary: "Boundary",
  confidence: "Evidence confidence",
  actionability: "Actionability",
  plan_state: "Plan",
};

const PLAN_STATE_LABEL: Record<string, string> = {
  available: "Plan ready",
  no_safe_plan: "No safe plan",
  not_persisted: "Needs an index refresh",
};

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

/**
 * The cause a reader should act on, in words. The terminal sink is deliberately
 * absent: it is machine evidence and belongs in the monospace line beneath.
 */
export function opportunityTitle(opportunity: PerformanceOpportunity): string {
  const cause = CAUSE_BY_MARKER[opportunity.biomarker_type];
  const phrase = cause
    ? cause(opportunity.boundary_kind)
    : biomarkerLabel(opportunity.biomarker_type);
  return phrase[0]!.toUpperCase() + phrase.slice(1);
}

/**
 * The machine evidence under the title: the sink the paths converge on, or the
 * symbol worth editing, or the file. Whichever exists is monospace.
 */
export function opportunityEvidenceLine(opportunity: PerformanceOpportunity): string {
  const sink = opportunity.terminal_sink?.trim();
  if (sink) return sink;
  const symbol = opportunity.intervention_symbol?.trim();
  if (symbol) return symbol;
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
      label: "Structured plan ready",
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
