/**
 * Canonical decision-record types.
 *
 * Canonical source: engine `DecisionRecordResponse`. Some downstream backends
 * emit a leaner `DecisionEntry` shape that omits `repository_id` and types
 * `status`/`source` as bare `string` instead of literal unions — consumer
 * adapters fill defaults before passing data to components.
 */

export type DecisionStatus =
  | "proposed"
  | "active"
  | "deprecated"
  | "superseded";

export type DecisionSource =
  | "git_archaeology"
  | "inline_marker"
  | "readme_mining"
  | "cli";

export interface DecisionRecord {
  id: string;
  repository_id: string;
  title: string;
  status: DecisionStatus;
  context: string;
  decision: string;
  rationale: string;
  alternatives: string[];
  consequences: string[];
  affected_files: string[];
  affected_modules: string[];
  tags: string[];
  source: DecisionSource;
  evidence_commits: string[];
  evidence_file: string | null;
  evidence_line: number | null;
  confidence: number;
  staleness_score: number;
  superseded_by: string | null;
  last_code_change: string | null;
  /** Trust tier of the decision's primary supporting evidence. Optional for back-compat. */
  verification?: DecisionVerification;
  created_at: string;
  updated_at: string;
}

export interface DecisionCreateInput {
  title: string;
  context?: string;
  decision?: string;
  rationale?: string;
  alternatives?: string[];
  consequences?: string[];
  affected_files?: string[];
  affected_modules?: string[];
  tags?: string[];
}

/**
 * PATCH body for /api/repos/{id}/decisions/{decision_id}. All fields are
 * optional — clients can update just the status, just the linkage, or both.
 */
export interface DecisionStatusUpdate {
  status?: DecisionStatus;
  superseded_by?: string;
  affected_modules?: string[];
  affected_files?: string[];
}

export interface DecisionHealth {
  summary: {
    active: number;
    proposed: number;
    deprecated: number;
    superseded: number;
    stale: number;
  };
  stale_decisions: DecisionRecord[];
  proposed_awaiting_review: DecisionRecord[];
  ungoverned_hotspots: string[];
}

// ---------------------------------------------------------------------------
// Phase 4C: evidence / lineage / decision-graph
// ---------------------------------------------------------------------------

/**
 * Trust level of a decision's supporting evidence. `exact` = the source quote
 * was found verbatim in the cited file/commit; `fuzzy` = a near-match;
 * `unverified` = the quote could not be located (LLM-derived, treat with care).
 */
export type DecisionVerification = "exact" | "fuzzy" | "unverified";

/** One supporting evidence row for a decision. */
export interface DecisionEvidence {
  id: string;
  source: string;
  source_rank: number;
  evidence_file: string | null;
  evidence_line: number | null;
  evidence_commit: string | null;
  source_quote: string;
  confidence: number;
  verification: DecisionVerification;
  created_at: string;
}

/**
 * One hop in a decision's supersession/evolution chain. The chain is ordered
 * root -> current; `relation` describes how the NEWER decision related to this
 * one ("supersedes" | "refines" | null for the terminal/current entry).
 */
export interface DecisionLineageEntry {
  id: string;
  title: string;
  status: DecisionStatus;
  source: string;
  relation: string | null;
}

export interface DecisionGraphNode {
  id: string;
  title: string;
  status: DecisionStatus;
  source: string;
  confidence: number;
  staleness_score: number;
  verification: DecisionVerification;
}

export type DecisionEdgeKind =
  | "supersedes"
  | "refines"
  | "relates_to"
  | "conflicts_with";

export interface DecisionGraphEdge {
  src: string;
  dst: string;
  kind: DecisionEdgeKind;
  confidence: number;
  evidence: string;
}

export interface DecisionCodeEdge {
  decision_id: string;
  node_id: string;
  link_type: "file" | "module";
}

export interface DecisionGraph {
  nodes: DecisionGraphNode[];
  decision_edges: DecisionGraphEdge[];
  code_edges: DecisionCodeEdge[];
}
