/** Canonical wire contract for structured refactoring recommendations. */

import type { ActionCommand } from "./actions.js";
import type { FixExclusion } from "./fix-first.js";
import type { QueueCounts } from "./queue.js";

export type RefactoringType =
  | "extract_class"
  | "extract_helper"
  | "extract_method"
  | "move_method"
  | "break_cycle"
  | "split_file"
  | "performance_fix";

export type EffortBucket = "S" | "M" | "L" | "XL";
export type Confidence = "low" | "medium" | "high";
export type ValidationBasis = "measured" | "inferred" | "mixed" | "unknown";
export type ValidationVia = "coverage" | "call-graph" | "import-graph" | "name-match" | "mixed";

export interface RecommendationValidationTarget {
  file_path: string;
  basis: ValidationBasis;
  via: ValidationVia | null;
  total: number;
  tests: string[];
  truncated: boolean;
}

export interface RecommendationValidation {
  basis: ValidationBasis;
  via: ValidationVia | null;
  total: number;
  tests: string[];
  truncated: boolean;
  affected_files: string[];
  affected_symbols: string[];
  commands: string[];
  targets: RecommendationValidationTarget[];
  /** Why each shown test is listed where it is ("calls walk_file", "covers lines
   *  94-208"), keyed by test id. Absent on a payload from an older server. */
  reasons?: Record<string, string>;
  /** Set when no test reaches the change: the step to take before the edit.
   *  Absent on a payload from an older server. */
  prerequisite?: string | null;
}

/** How a step's tests were found: coverage runs its lines, the graph reaches
 *  it, or nothing does. */
export type StepVerifyCoverage = "measured" | "inferred" | "none";

/** How to check one step of a plan, on plan detail only, and only on a step
 *  whose checks differ from the plan's. A step without one is checked by the
 *  plan-level validation (always so for a single-step plan). */
export interface StepVerify {
  commands: string[];
  tests: string[];
  coverage: StepVerifyCoverage;
}

/** What another layer says to check before applying a plan: a decision that
 *  governs its file, consumers of what it moves, dead code it touches, or
 *  recent commits on its lines. */
export type PlanRiskKind = "decision" | "public_api" | "active_edit" | "dead_code";

/** One sentence to read before applying the plan. `ref` is the decision id or
 *  the newest commit sha, when the risk has one. */
export interface PlanRisk {
  kind: PlanRiskKind;
  text: string;
  ref: string | null;
}

/** A file that changed together with the plan's file, and how many commits
 *  they shared in the history the index walked. On plan detail, in
 *  `blast_radius.co_change_partners`. */
export interface PlanCoChangePartner {
  file_path: string;
  commits: number;
}

/** An Extract Method helper parameter's role: ``inout`` when the helper also
 *  returns the value's new state. Mirrors ``refactoring.render.PARAM_MODES``. */
export type ExtractParamMode = "in" | "inout";

/** One helper parameter or output, with its declared type (null: unknown). */
export interface ExtractSlot {
  name: string;
  type: string | null;
  mode?: ExtractParamMode;
}

/** The statement that replaces an Extract Method span. Spec text for whoever
 *  applies the plan; Repowise never applies it. */
export interface ExtractCallSite {
  replace_span: { start: number; end: number };
  new_text: string;
}

export interface RefactoringPlan {
  id: string;
  refactoring_type: RefactoringType | string;
  file_path: string;
  target_symbol: string;
  line_start: number | null;
  line_end: number | null;
  plan: Record<string, unknown>;
  evidence: Record<string, unknown>;
  impact_delta: number;
  effort_bucket: EffortBucket | string;
  blast_radius: Record<string, unknown>;
  confidence: Confidence | string;
  source_biomarker: string;
  /** Compatibility priority. Older servers already provide this field. */
  rank_score: number;
  /** Optional so a newer client can read a payload from a server that predates them. */
  benefit?: number;
  leverage?: number;
  cost?: number;
  risk?: number;
  dependents?: number;
  file_nloc?: number;
  file_weighted_deficit?: number;
  validation?: RecommendationValidation;
  /** Plan detail only: ids of the accepted decisions governing the file.
   *  Absent when the plan was never checked (not yet re-indexed). */
  governed_by?: string[];
  /** Plan detail only: what to check before applying it. Absent when the
   *  plan was never checked; empty when nothing was found. */
  risks?: PlanRisk[];
  /** Plan detail with `include=recipe` only: the plan as an agent applies it. */
  recipe?: RefactoringRecipe;
  /** Plan detail only, on a plan the index resolved as no longer detected. */
  payoff?: RefactoringPlanPayoff;
}

/** What happened to a resolved plan. Mirrors ``refactoring.payoff.PAYOFF_OUTCOMES``. */
export type RefactoringPayoffOutcome =
  | "applied"
  | "file_deleted"
  | "superseded"
  | "target_changed"
  | "unknown";

/** A function's stored size measures; an absent one was not measured. */
export interface PlanMeasures {
  ccn?: number;
  nloc?: number;
  params?: number;
}

export interface RefactoringPlanPayoff {
  outcome: RefactoringPayoffOutcome;
  resolved_commit?: string;
  resolved_at?: string;
  before?: PlanMeasures;
  after?: PlanMeasures;
  /** Measured drop (`ccn_removed`, `nloc_removed`), beside the plan's predicted `evidence`. */
  realised?: Record<string, number>;
  /** The function that appeared beside the target, sized like the plan's slice. */
  new_symbol?: string;
}

/** What one recipe step does to the code. Mirrors ``refactoring.recipe.RECIPE_ACTIONS``. */
export type RecipeAction =
  | "extract"
  | "add_helper"
  | "replace_with_call"
  | "extract_class"
  | "move"
  | "cut_import"
  | "keep"
  | "reexport"
  | "edit";

/** What must hold before the edit: tests that pass, a characterization test to
 *  add, a plan risk, risks never checked (`unchecked`), or a plan type not yet
 *  audited (`kind_unaudited`). Mirrors ``refactoring.recipe.PRECONDITION_KINDS``. */
export type RecipePreconditionKind =
  | "tests"
  | "characterization"
  | "decision"
  | "public_api"
  | "active_edit"
  | "dead_code"
  | "unchecked"
  | "kind_unaudited";

export interface RecipeStep {
  n: number;
  action: RecipeAction;
  file: string | null;
  span: { start: number; end: number } | null;
  /** The instruction, in one or two sentences. */
  text: string;
  /** Extract Method: the helper to write and its copyable header. */
  new_symbol?: {
    name: string;
    kind: "method" | "function" | null;
    async: boolean;
    params: string[];
    returns: string[];
    signature_text: string | null;
    notes: string[];
  };
  call_site?: ExtractCallSite;
  symbols?: string[];
  to_file?: string | null;
  /** Extract Helper: the directory the shared helper belongs in. */
  directory?: string | null;
  applicability?: "mechanical" | "judgment";
  /** This step's own checks, where they differ from the plan's. */
  verify?: { commands: string[]; tests: string[]; coverage: string };
}

/**
 * One plan as a spec an agent applies (Repowise never applies it): what must
 * hold before, the edits in order, what must hold after, and what not to do.
 * Built by ``refactoring.recipe.build_recipe``.
 */
export interface RefactoringRecipe {
  id: string | null;
  kind: RefactoringType | string;
  /** The steps are one way to do it, for a person to judge (Break Cycle). */
  advisory: boolean;
  summary: string;
  target: { file: string; symbol: string | null; span: { start: number; end: number } | null };
  preconditions: Array<{ kind: RecipePreconditionKind; text: string; ref?: string }>;
  steps: RecipeStep[];
  postconditions: Array<
    | {
        kind: "verify";
        text: string;
        tests?: string[];
        tests_total?: number;
        commands?: string[];
        basis?: string | null;
        reasons?: Record<string, string>;
      }
    | { kind: "metric"; text: string }
  >;
  /** `constraint` reads after "Do not"; `reason` says why, when it is not obvious. */
  does_not: Array<{ constraint: string; reason: string | null }>;
}

export interface RefactoringTypeCount {
  type: string;
  count: number;
}

export interface RefactoringSummary {
  total: number;
  by_type: RefactoringTypeCount[];
  /** Additive Phase 4 aggregates; absent on older servers. */
  files_total?: number;
  structural_total?: number;
  /** Split File / Extract Class plans with an unnamed group (`needs_design`):
   *  counted in `structural_total`, never a step. */
  design_total?: number;
  performance_total?: number;
  small_effort_total?: number;
  health_recovery_total?: number;
  negligible_health_total?: number;
  best_health_gain?: number;
}

export interface RefactoringTargets {
  summary: RefactoringSummary;
  plans: RefactoringPlan[];
}

export interface GeneratedSpan {
  file: string;
  line_start: number;
  line_end: number;
}

export interface GeneratedCode {
  suggestion_id: string | null;
  refactoring_type: string;
  file_path: string;
  target_symbol: string;
  content: string;
  diff: string;
  provider: string;
  model: string;
  cached: boolean;
  input_tokens: number;
  output_tokens: number;
  validation: Record<string, unknown>;
  spans: GeneratedSpan[];
}

/* ---------------------------------------------------------------------------
 * Composed opportunities (repowise 0.47.0).
 *
 * The product unit is a file's composed refactoring: the diagnosis it leads
 * with, its steps in an order that is safe to apply, and the observations
 * behind it. Plans remain addressable and unchanged; a step names one.
 * Every field here is additive, so an older server simply omits the block.
 * ------------------------------------------------------------------------ */

/** ``mechanical`` only when the detector's proof obligations all hold. */
export type StepClassification = "mechanical" | "judgment";

export interface StepApplicability {
  classification: StepClassification;
  /** Closed vocabulary; see docs/layers/REFACTORING.md. */
  reasons: string[];
  facts: Record<string, unknown>;
  /** Facts the layer could not establish. Never silently false. */
  unknowns: string[];
}

export interface OpportunityStep {
  /** The plan this step is; resolves through the plan detail route. */
  plan_id: string;
  refactoring_type: RefactoringType;
  target_symbol: string;
  file_path: string;
  line_start: number | null;
  line_end: number | null;
  effort_bucket: EffortBucket;
  confidence: Confidence;
  impact_delta: number;
  source_biomarker: string;
  /**
   * An earlier step that moves this step's symbol to another file. When set,
   * this step's file and span say where the symbol was: locate it again before
   * applying. Any surface rendering an ordered list has to say so.
   */
  relocated_by: string | null;
  applicability: StepApplicability;
  /** Findings this step's cause produced, for a round trip to the diagnosis. */
  finding_ids?: string[];
  validation_profile_id?: string;
}

/** A supporting observation. Never an instruction to change anything. */
export interface OpportunityEvidence {
  plan_id: string;
  refactoring_type: RefactoringType;
  target_symbol: string;
  source_biomarker: string;
  summary: Record<string, unknown>;
}

/**
 * The triage vocabulary, shared with health findings so Code Health has one
 * triage system. An opportunity's state is rolled up from its member steps;
 * ``false_positive`` is reached only when every step is one, so a single wrong
 * step never dismisses the work the others still describe.
 */
export type OpportunityStatus =
  | "open"
  | "acknowledged"
  | "resolved"
  | "false_positive";

/** The state a person can ask for. Same vocabulary, named for the write side. */
export type RefactoringTriageStatus = OpportunityStatus;

/** ``PATCH .../refactoring/opportunities/{id}/status``. */
export interface RefactoringOpportunityStatusUpdate {
  opportunity_id: string;
  status: OpportunityStatus;
  /** The member plans the transition was applied to. */
  steps_updated: number;
  status_changed_at: string | null;
}

/** ``PATCH .../refactoring/{id}/status``, one step. */
export interface RefactoringPlanStatusUpdate {
  id: string;
  public_id: string | null;
  status: OpportunityStatus;
  status_reason: string | null;
  status_changed_at: string | null;
}

export interface RefactoringOpportunity {
  /** ``refop<model>_<digest>`` over the member plan ids. */
  opportunity_id: string;
  refactoring_model_version: number;
  status: OpportunityStatus;
  file_path: string;
  /** The file's dominant finding; null when none was recorded. */
  lead_biomarker: string | null;
  lead_refactoring_type: RefactoringType | "";
  /**
   * Tri-state. ``null`` means no dominant finding was available to compare
   * against, which is not the same claim as ``false``.
   */
  addresses_primary_problem: boolean | null;
  effort_bucket: EffortBucket;
  confidence: Confidence;
  step_count: number;
  mechanical_steps: number;
  judgment_steps: number;
  evidence_total: number;
  affected_files_total: number;
  recoverable_health: number;
  rank_score: number;
  rank_position: number;
  /** Position in the diversified default order. */
  queue_position: number;
  rank_factors: Record<string, number>;
  why_ranked: Array<{ factor: string; value: number }>;
  /** Lines in the lead file. Absent when the store predates the figure. */
  file_nloc?: number;
  /** Files importing the lead file. Absent when the store predates the figure. */
  dependents?: number;
  steps?: OpportunityStep[];
  steps_total?: number;
  steps_emitted?: number;
  steps_reduced_reason?: string;
  /** Present only when the queue row was asked for inline evidence. */
  evidence?: OpportunityEvidence[];
  evidence_emitted?: number;
  evidence_truncated?: boolean;
  evidence_reduced_reason?: string;
  evidence_next_cursor?: number;
}

/**
 * A detail lookup either found the id or did not; the two shapes share nothing
 * but the discriminant, so narrow on `found` before reading anything else.
 * `found` is the lookup, `status` the triage lifecycle (which can itself be
 * `resolved`). REST 404s on the not-found branch, MCP returns it verbatim.
 */
export type RefactoringOpportunityDetail =
  | RefactoringOpportunityDetailResolved
  | RefactoringOpportunityDetailUnresolved;

export interface RefactoringOpportunityDetailUnresolved {
  found: false;
  opportunity_id: string;
  reason: "unknown_opportunity_id";
  /** MCP only: tells a stale-model id apart from one never minted here. */
  model_state?: {
    state: "current" | "stale_model" | "unrecognized";
    public_id: string;
    requested_model_version: number | null;
    refactoring_model_version: number;
    refresh_required: boolean;
  };
}

export interface RefactoringOpportunityDetailResolved
  extends Omit<RefactoringOpportunity, "steps_total"> {
  found: true;
  /**
   * `unavailable` when the opportunity resolved but its steps could not be
   * read: `steps` is empty and `steps_total` is null, while the row's own
   * `step_count` still says how many there are. Absent means available.
   */
  details_status?: "available" | "unavailable";
  steps: OpportunityStep[];
  /** Null only when `details_status` is `unavailable`. */
  steps_total: number | null;
  steps_emitted: number;
  steps_reduced_reason?: string;
  steps_next_cursor?: number;
  evidence: OpportunityEvidence[];
  evidence_emitted: number;
  evidence_truncated: boolean;
  evidence_reduced_reason?: string;
  evidence_next_cursor?: number;
  affected_files: string[];
  /** Absent when no finding on this file is addressable by id. */
  lead_finding_ids?: string[];
  validation_profiles: Array<{ id: string } & RecommendationValidation>;
  /** The member plans' payloads, so the steps are executable in one call. */
  plans: RefactoringPlan[];
  next_actions: Array<{ why: string; tool: string; arguments: Record<string, unknown> }>;
  /** Present only when a step carries `relocated_by`. */
  ordering_note?: string;
}

/** Named orderings for the queue. ``diversified`` is the default. */
export type RefactoringView = "diversified" | "canonical" | "file_spread";

/**
 * Named orderings. ``queue`` is the diversified default; ``rank`` is the honest
 * tied order; ``health`` is the explicit worst-files-first view. All are indexed
 * columns, so none costs a sort.
 */
export type RefactoringOrder = "queue" | "rank" | "health" | "effort" | "file";

/**
 * Which open opportunities a queue lists. ``fix_first`` (the default for the
 * repository-wide open queue) keeps only what Fix first would take; ``all`` is
 * the full inventory, and the default for a file or a triaged status.
 */
export type RefactoringScope = "fix_first" | "all";

/** What the ``fix_first`` scope leaves out of the filtered set, by reason. */
export interface RefactoringHiddenCounts {
  total: number;
  by_reason: Partial<Record<FixExclusion, number>>;
}

/** No stored analysis: the counts genuinely do not exist, so none are present. */
export interface RefactoringRollupUnavailable {
  status: "unavailable";
  reason: "no_refactoring_analysis";
  detail: string;
}

export interface RefactoringRollupAvailable {
  status: "available";
  opportunities_total: number;
  files_total: number;
  steps_total: number;
  mechanical_steps_total: number;
  judgment_steps_total: number;
  /** The plan inventory: steps + evidence + unattached. Absent on older indexes. */
  plans_total?: number;
  /** Plans carried as evidence (demoted clones, advisory cycles, unnamed groups), not steps. */
  evidence_total?: number;
  /** Plans no opportunity carries; listed only in the plan inventory. */
  unattached_plans_total?: number;
  /** Plans held out of the steps for an unnamed group (`needs_design`); part of
   *  the evidence and unattached shares. */
  design_total?: number;
  by_lead_type: Record<string, number>;
  by_effort: Record<string, number>;
  by_confidence: Record<string, number>;
  by_status: Record<string, number>;
  addresses_primary_problem: { yes: number; no: number; unknown: number };
  /** The same lead the directive reads; null when nothing is open. */
  lead: RefactoringDirectiveLead | null;
  refactoring_model_version: number;
  analyzed_commit: string | null;
  /** Present on the MCP block only. */
  facets?: Record<string, Record<string, number>>;
  next_call?: ActionCommand;
}

export type RefactoringOpportunityRollup =
  | RefactoringRollupAvailable
  | RefactoringRollupUnavailable;

export interface RefactoringDirectiveLead {
  opportunity_id: string;
  file_path: string;
  lead_biomarker: string | null;
  lead_refactoring_type: string;
  addresses_primary_problem: boolean | null;
  step_count: number;
  mechanical_steps: number;
  judgment_steps: number;
  effort_bucket: EffortBucket;
  confidence: Confidence;
  recoverable_health: number;
  status: OpportunityStatus;
}

/**
 * The Level-0 lead on a bare health call. Three shapes, one discriminant:
 * there is work, there is none, or there is no analysis to answer from.
 */
export type RefactoringDirective =
  | RefactoringDirectiveAvailable
  | {
      status: "clear";
      reason: "no_open_opportunities" | "only_test_file_opportunities";
      detail: string;
      opportunities_total: number;
    }
  | RefactoringRollupUnavailable;

export interface RefactoringDirectiveAvailable {
  status: "available";
  opportunity_id: string;
  fix_first: string;
  reason: string | null;
  lead_refactoring_type: string;
  steps: number;
  mechanical_steps: number;
  judgment_steps: number;
  effort_bucket: EffortBucket;
  confidence: Confidence;
  recovers_health_points: number;
  addresses_primary_problem: boolean | null;
  opportunities_total: number;
  next_action: { tool: string; arguments: Record<string, unknown> };
  /** Set when the steps do not address the file's dominant finding, or cannot say. */
  note?: string;
}

export interface RefactoringOpportunityPage {
  items: RefactoringOpportunity[];
  total: number;
  offset: number;
  has_more: boolean;
  next_offset: number | null;
  facets: Record<string, Record<string, number>>;
  summary: RefactoringOpportunityRollup | null;
  /** Values the server could not admit, named rather than dropped. */
  ignored_arguments?: Record<string, string>;
  /** The scope the server applied, which `total` and `facets` count. */
  scope?: RefactoringScope;
  /** Under ``fix_first`` only. */
  hidden?: RefactoringHiddenCounts;
  /** The plans' count vocabulary over the open inventory in the queried files. */
  counts?: QueueCounts;
}

/** ``GET /api/repos/{repo_id}/refactoring/summary``. */
export interface RefactoringSummaryResponse {
  summary: RefactoringOpportunityRollup;
  directive: RefactoringDirective;
}
