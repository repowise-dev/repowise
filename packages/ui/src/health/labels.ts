/**
 * Words for the vocabularies more than one Code Health surface prints.
 *
 * One copy, imported everywhere: an effort bucket that read "Medium" on one tab
 * and "Moderate" on the next would be two vocabularies for one value.
 */

import type { Confidence, EffortBucket, OpportunityStatus } from "@repowise-dev/types/refactoring";

export const EFFORT_LABEL: Record<EffortBucket, string> = {
  S: "Small",
  M: "Medium",
  L: "Large",
  XL: "Extra large",
};

export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
};

/** Triage states. Findings and refactoring plans share the vocabulary. */
export const STATUS_LABEL: Record<OpportunityStatus, string> = {
  open: "Open",
  acknowledged: "Acknowledged",
  resolved: "Resolved",
  false_positive: "False positive",
};

/**
 * The two units of work a person opens. A refactoring plan is one file's
 * ordered steps; a performance fix is one cause and the place to change it.
 */
export const WORK_UNIT_LABEL = {
  refactor: "refactoring plan",
  perf_fix: "performance fix",
} as const;
