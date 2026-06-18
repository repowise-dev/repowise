// ---------------------------------------------------------------------------
// Decisions
// ---------------------------------------------------------------------------

export interface DecisionRecordResponse {
  id: string;
  repository_id: string;
  title: string;
  status: "proposed" | "active" | "deprecated" | "superseded";
  context: string;
  decision: string;
  rationale: string;
  alternatives: string[];
  consequences: string[];
  affected_files: string[];
  affected_modules: string[];
  tags: string[];
  source: "git_archaeology" | "inline_marker" | "readme_mining" | "cli";
  evidence_commits: string[];
  evidence_file: string | null;
  evidence_line: number | null;
  confidence: number;
  staleness_score: number;
  superseded_by: string | null;
  last_code_change: string | null;
  /** Trust tier of the decision's primary supporting evidence. */
  verification?: DecisionVerification;
  created_at: string;
  updated_at: string;
  /** Evidence rows backing the record. Populated by the list endpoint only. */
  evidence_count?: number | null;
  /** Top-ranked evidence row, slimmed. Populated by the list endpoint only. */
  evidence_preview?: EvidencePreview | null;
}

export type DecisionVerification = "exact" | "fuzzy" | "unverified";

export interface EvidencePreview {
  source: string;
  source_quote: string;
  verification: DecisionVerification;
  evidence_file?: string | null;
  evidence_line?: number | null;
}

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

export interface DecisionEvidenceResponse {
  evidence: DecisionEvidence[];
}

export interface DecisionLineageEntry {
  id: string;
  title: string;
  status: "proposed" | "active" | "deprecated" | "superseded";
  source: string;
  relation: string | null;
}

export interface DecisionLineageResponse {
  lineage: DecisionLineageEntry[];
}

export interface DecisionGraphNode {
  id: string;
  title: string;
  status: "proposed" | "active" | "deprecated" | "superseded";
  source: string;
  confidence: number;
  staleness_score: number;
  verification: DecisionVerification;
}

export interface DecisionGraphEdge {
  src: string;
  dst: string;
  kind: "supersedes" | "refines" | "relates_to" | "conflicts_with";
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

export interface DecisionCreate {
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

export interface DecisionStatusUpdate {
  status?: string;
  superseded_by?: string;
  affected_modules?: string[];
  affected_files?: string[];
}
