/**
 * The first-look report: what Repowise found after the first index, in order.
 * Mirrors `repowise.core.report.first_look` (`FirstLookReport.to_json`, the
 * `Staleness` block and the prose writers fill); `test_wire_vocabulary_parity.py`
 * fails when the closed vocabularies or the item fields disagree.
 */

import type { FirstLookResponse } from "./generated/http.js";

/** Core's fixed section order. A plugin section uses its own key, listed after them. */
export type FirstLookSectionKey =
  | "security"
  | "performance"
  | "health"
  | "tests"
  | "dead_code"
  | "docs"
  | "decisions"
  | "hygiene"
  | "ownership";

/** How an item is known, best first. */
export type FirstLookBasis = "measured" | "inferred" | "declared";
/** The action tier vocabulary. */
export type FirstLookSeverity = "act_now" | "plan" | "improve";
export type FirstLookConfidence = "high" | "medium" | "low";
export type FirstLookEffort = "S" | "M" | "L";

/**
 * App route keys an item links to; each shell maps them to its own URLs.
 * `file` takes `path` and `line`, `health_file` takes `path`, `decision` takes `id`.
 */
export type FirstLookRouteKey =
  | "file"
  | "security"
  | "dead_code"
  | "health_file"
  | "docs_drift"
  | "decision"
  | "ownership";

export type FirstLookPromptRenderer = "action" | "fix_item" | "perf_opportunity" | "first_look_item";

export interface FirstLookLocation {
  path: string;
  line: number | null;
  symbol: string | null;
}

export interface FirstLookLink {
  route: FirstLookRouteKey;
  params: Record<string, string | number>;
}

export interface FirstLookItem {
  id: string;
  section: FirstLookSectionKey | (string & {});
  rule: string;
  /** The facts the prose is written from. Never source text. */
  title_facts: Record<string, unknown>;
  locations: FirstLookLocation[];
  /** Every location the source had; `locations` keeps the first 40. */
  locations_total: number;
  confidence: FirstLookConfidence;
  confidence_score: number | null;
  basis: FirstLookBasis;
  source_layer: string;
  origin: string | null;
  severity: FirstLookSeverity;
  effort: FirstLookEffort;
  priority: number;
  weight: number;
  link: FirstLookLink;
  prompt: { renderer: FirstLookPromptRenderer; payload_ref: string | null };
  fingerprint: string;
}

export interface FirstLookSection {
  key: FirstLookSectionKey | (string & {});
  source_layer: string;
  /** Every item, ranked. A page shows the first `FIRST_LOOK_SECTION_CAP`. */
  items: FirstLookItem[];
  facts: Record<string, unknown>;
}

/** One "Turn on more" step. */
export interface FirstLookSetupStep {
  action_id: string;
  rule: string;
  title: string;
  command: string | null;
}

export interface FirstLookStamp {
  commit: string | null;
  indexed_at: string | null;
  created_at: string;
  /** `template`, or the model id that wrote the prose. */
  generator: string;
  /** What the prose cost in USD; null for a template. */
  cost: number | null;
  schema_version: number;
}

/**
 * The words on the page, written once in core by a template or a model.
 * A renderer prints these and nothing else.
 */
export interface FirstLookProse {
  writer?: string;
  summary?: string;
  /** The measured headline (bug-fix correlation of the lowest-health files). */
  headline?: string;
  /** Section key -> heading. */
  titles?: Record<string, string>;
  /** Section key -> intro. */
  sections?: Record<string, string>;
  /** Item id -> its sentence. */
  items?: Record<string, string>;
  /** Setup step `action_id` -> its sentence. */
  setup?: Record<string, string>;
}

export interface FirstLookRepo {
  name?: string;
  branch?: string | null;
  files?: number;
  health?: { score: number; band: string; band_label: string } | null;
  headline_facts?: Record<string, unknown>[];
}

export interface FirstLookReport {
  schema_version: number;
  stamp: FirstLookStamp;
  repo: FirstLookRepo;
  /** "Do these first": at most three item ids. */
  top: string[];
  /** Non-empty sections in core's order. */
  sections: FirstLookSection[];
  setup: FirstLookSetupStep[];
  /** Section key -> why it could not be collected. */
  unavailable: Record<string, string>;
  /** Empty until a writer fills it. */
  prose: FirstLookProse;
}

/** The report against the index as it is now. */
export interface FirstLookStaleness {
  report_commit: string | null;
  current_commit: string | null;
  /** The index has moved past the report's commit. */
  behind: boolean;
  /** Item ids gone from the index since. */
  fixed: string[];
  /** Item ids whose facts moved since. */
  changed: string[];
  /** Items the report never had; listed beside it, never inside. */
  new: FirstLookItem[];
  unknown: Record<string, string>;
}

/** Items one section shows before "N more in the app" (core's `SECTION_CAP`). */
export const FIRST_LOOK_SECTION_CAP = 12;

/** `GET /api/repos/{id}/reports/first-look` with the report and staleness typed. */
export type FirstLookPage = Omit<FirstLookResponse, "report" | "staleness"> & {
  report: FirstLookReport;
  staleness: FirstLookStaleness;
};

export type { FirstLookStampResponse } from "./generated/http.js";
