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
