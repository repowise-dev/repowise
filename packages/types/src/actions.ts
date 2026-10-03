/**
 * Next actions: the short list of things worth doing, built from the index.
 *
 * Mirrors `repowise.core.analysis.actions.model` and the server's
 * `schemas/actions.py`. `test_wire_vocabulary_parity.py` fails when the unions
 * below and their Python twins disagree.
 */

export type ActionRule =
  | "live_secret"
  | "fresh_regressions"
  | "fragile_file"
  | "fix_concentration"
  | "fix_first"
  | "stale_decision"
  | "knowledge_loss"
  | "broken_doc_refs"
  | "dead_code_batch"
  | "coverage_missing"
  | "coverage_stale"
  | "decisions_unreviewed";

export type ActionHorizonKey = "week" | "quarter";

/** `act_now` is wrong or got worse; `plan` is worth scheduling; `improve_signal` sharpens the other two. */
export type ActionTier = "act_now" | "plan" | "improve_signal";

/** `unknown` is printed as a fact, never read as zero. */
export type ActionFactBasis = "measured" | "inferred" | "unknown";

export type ActionSurface =
  | "file"
  | "findings"
  | "performance"
  | "security"
  | "doc_drift"
  | "dead_code"
  | "decisions"
  | "commits"
  | "coverage";

export type ActionTargetKind = "file" | "symbol" | "folder" | "document" | "decision" | "repo";

export type ActionRuleStatus = "evaluated" | "not_applicable" | "unavailable";

export type ActionStateValue = "dismissed" | "snoozed" | "done";

export type ActionSeverity = "critical" | "high" | "medium" | "low";

export interface ActionWhy {
  label: string;
  value: string;
  basis: ActionFactBasis;
}

/** One piece of evidence: a finding, a site, a reference. */
export interface ActionDetail {
  path: string;
  line: number | null;
  symbol: string | null;
  marker: string | null;
  severity: string | null;
  reason: string;
  /** The commit or stored id that produced it. */
  ref: string | null;
}

/**
 * A way to see more: the MCP call for an agent, the CLI line for a person.
 * `tool` and `arguments` are the structured call `mcp` renders, when one exists.
 */
export interface ActionCommand {
  purpose: string;
  mcp: string | null;
  cli: string | null;
  tool?: string | null;
  arguments?: Record<string, unknown> | null;
}

export interface NextAction {
  /** Stable across re-index: derived from the rule and its target. */
  id: string;
  rule: ActionRule;
  tier: ActionTier;
  horizons: ActionHorizonKey[];
  severity: ActionSeverity;
  /** Verb first. Paths and symbols are wrapped in backticks for a mono renderer. */
  title: string;
  impact: string;
  why: ActionWhy[];
  target: { kind: ActionTargetKind; path: string; symbol: string | null };
  surface: ActionSurface;
  effort: "S" | "M" | "L";
  confidence: "high" | "medium";
  done_when: string;
  command: string | null;
  /** The biomarker behind the action, for the glossary label. */
  marker: string | null;
  evidence_ids: string[];
  evidence_total: number;
  /** Files a folder-level or roll-up action speaks for. */
  includes: string[];
  /** Sent back with a dismissal; the action returns when it changes. */
  fingerprint: string;
  /** The evidence itself, capped; `details_total` is how many there were. */
  details: ActionDetail[];
  details_total: number;
  commands: ActionCommand[];
}

export interface ActionHorizon {
  actions: NextAction[];
  /** Every visible action in this horizon, not only those listed. */
  total: number;
  /** Actions the person dismissed, snoozed or marked done. */
  hidden: number;
  by_tier: Partial<Record<ActionTier, number>>;
}

export interface ActionRuleReport {
  rule: ActionRule;
  status: ActionRuleStatus;
  reason: string;
  emitted: number;
}

export interface ActionsResponse {
  status: "available";
  /** Newest indexed commit; "this week" counts back from here, not from today. */
  anchor: string | null;
  week_start: string | null;
  context: {
    production_files: number;
    active_authors_90d: number;
    fix_commits_90d: number;
    busy_threshold: number;
    coverage: "measured" | "stale" | "unknown";
  };
  horizons: Record<ActionHorizonKey, ActionHorizon>;
  rules: ActionRuleReport[];
  /** Store name to why it could not be read. */
  unavailable: Record<string, string>;
}

export interface ActionStateRequest {
  /** `null` clears the person's answer. */
  state: ActionStateValue | null;
  fingerprint?: string;
  snooze_days?: number;
}

export interface ActionStateResponse {
  action_id: string;
  state: ActionStateValue | null;
  until: string | null;
}

export interface WorkspaceRepoActions {
  alias: string;
  repo_id: string | null;
  status: "available" | "unavailable";
  reason: string;
  /** The strongest work per horizon (act now and plan tiers), with totals. */
  horizons: Partial<Record<ActionHorizonKey, ActionHorizon>>;
}

export interface WorkspaceCrossRepoAction {
  kind: "breaking_contract";
  title: string;
  impact: string;
  count: number;
  repos: string[];
}

export interface WorkspaceActionsResponse {
  repos: WorkspaceRepoActions[];
  cross_repo: WorkspaceCrossRepoAction[];
}
