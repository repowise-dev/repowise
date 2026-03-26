/**
 * TypeScript types mirroring the backend Pydantic schemas.
 * Source of truth: packages/server/src/repowise/server/schemas.py
 */

// ---------------------------------------------------------------------------
// Repository
// ---------------------------------------------------------------------------

export interface RepoCreate {
  name: string;
  local_path: string;
  url?: string;
  default_branch?: string;
  settings?: Record<string, unknown>;
}

export interface RepoUpdate {
  name?: string;
  url?: string;
  default_branch?: string;
  settings?: {
    exclude_patterns?: string[];
    [key: string]: unknown;
  };
}

export interface RepoResponse {
  id: string;
  name: string;
  url: string;
  local_path: string;
  default_branch: string;
  head_commit: string | null;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Pages
// ---------------------------------------------------------------------------

export interface PageResponse {
  id: string;
  repository_id: string;
  page_type: string;
  title: string;
  content: string;
  target_path: string;
  source_hash: string;
  model_name: string;
  provider_name: string;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  generation_level: number;
  version: number;
  confidence: number;
  freshness_status: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface PageVersionResponse {
  id: string;
  page_id: string;
  version: number;
  page_type: string;
  title: string;
  content: string;
  source_hash: string;
  model_name: string;
  provider_name: string;
  input_tokens: number;
  output_tokens: number;
  confidence: number;
  archived_at: string;
}

export interface PageListResponse {
  pages: PageResponse[];
  total: number;
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export interface JobResponse {
  id: string;
  repository_id: string;
  status: "pending" | "running" | "completed" | "failed" | "paused";
  provider_name: string;
  model_name: string;
  total_pages: number;
  completed_pages: number;
  failed_pages: number;
  current_level: number;
  error_message: string | null;
  config: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobProgressEvent {
  event: "progress" | "done" | "error";
  job_id: string;
  completed_pages: number;
  total_pages: number;
  current_page?: string;
  current_level?: number;
  tokens_input?: number;
  tokens_output?: number;
  estimated_cost?: number;
  error?: string;
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

export interface SearchRequest {
  query: string;
  search_type?: "semantic" | "fulltext";
  limit?: number;
}

export interface SearchResultResponse {
  page_id: string;
  title: string;
  page_type: string;
  target_path: string;
  score: number;
  snippet: string;
  search_type: string;
}

// ---------------------------------------------------------------------------
// Symbols
// ---------------------------------------------------------------------------

export interface SymbolResponse {
  id: string;
  repository_id: string;
  file_path: string;
  symbol_id: string;
  name: string;
  qualified_name: string;
  kind: string;
  signature: string;
  start_line: number;
  end_line: number;
  docstring: string | null;
  visibility: string;
  is_async: boolean;
  complexity_estimate: number;
  language: string;
  parent_name: string | null;
}

// ---------------------------------------------------------------------------
// Graph
// ---------------------------------------------------------------------------

export interface GraphNodeResponse {
  node_id: string;
  node_type: string;
  language: string;
  symbol_count: number;
  pagerank: number;
  betweenness: number;
  community_id: number;
  is_test: boolean;
  is_entry_point: boolean;
}

export interface GraphEdgeResponse {
  source: string;
  target: string;
  imported_names: string[];
}

export interface GraphExportResponse {
  nodes: GraphNodeResponse[];
  links: GraphEdgeResponse[];
}

export interface GraphPathResponse {
  path: Array<{ node: string; relationship: string; edge_type: string }>;
  distance: number;
  explanation: string;
}

export interface ModuleNodeResponse {
  module_id: string;
  file_count: number;
  symbol_count: number;
  avg_pagerank: number;
  doc_coverage_pct: number;
}

export interface ModuleEdgeResponse {
  source: string;
  target: string;
  edge_count: number;
}

export interface ModuleGraphResponse {
  nodes: ModuleNodeResponse[];
  edges: ModuleEdgeResponse[];
}

export interface EgoGraphResponse {
  nodes: GraphNodeResponse[];
  links: GraphEdgeResponse[];
  center_node_id: string;
  center_git_meta: GitMetadataResponse | null;
  inbound_count: number;
  outbound_count: number;
}

export interface NodeSearchResult {
  node_id: string;
  language: string;
  symbol_count: number;
}

export interface DeadCodeGraphNodeResponse {
  node_id: string;
  node_type: string;
  language: string;
  symbol_count: number;
  pagerank: number;
  betweenness: number;
  community_id: number;
  is_test: boolean;
  is_entry_point: boolean;
  confidence_group: string;
}

export interface DeadCodeGraphResponse {
  nodes: DeadCodeGraphNodeResponse[];
  links: GraphEdgeResponse[];
}

export interface HotFilesNodeResponse {
  node_id: string;
  node_type: string;
  language: string;
  symbol_count: number;
  pagerank: number;
  betweenness: number;
  community_id: number;
  is_test: boolean;
  is_entry_point: boolean;
  commit_count: number;
}

export interface HotFilesGraphResponse {
  nodes: HotFilesNodeResponse[];
  links: GraphEdgeResponse[];
}

export interface RepoStatsResponse {
  file_count: number;
  symbol_count: number;
  entry_point_count: number;
  doc_coverage_pct: number;
  freshness_score: number;
  dead_export_count: number;
}

// ---------------------------------------------------------------------------
// Git Intelligence
// ---------------------------------------------------------------------------

export interface GitMetadataResponse {
  file_path: string;
  commit_count_total: number;
  commit_count_90d: number;
  commit_count_30d: number;
  first_commit_at: string | null;
  last_commit_at: string | null;
  primary_owner_name: string | null;
  primary_owner_email: string | null;
  primary_owner_commit_pct: number | null;
  top_authors: Array<{ name: string; email: string; commit_count: number; pct: number }>;
  significant_commits: Array<{ sha: string; date: string; message: string; author: string }>;
  co_change_partners: Array<{ file_path: string; co_change_count: number }>;
  is_hotspot: boolean;
  is_stable: boolean;
  churn_percentile: number;
  age_days: number;
}

export interface HotspotResponse {
  file_path: string;
  commit_count_90d: number;
  churn_percentile: number;
  primary_owner: string | null;
  is_hotspot: boolean;
}

export interface OwnershipEntry {
  module_path: string;
  primary_owner: string | null;
  owner_pct: number | null;
  file_count: number;
  is_silo: boolean;
}

export interface GitSummaryResponse {
  total_files: number;
  hotspot_count: number;
  stable_count: number;
  average_churn_percentile: number;
  top_owners: Array<{ name: string; email: string; file_count: number; pct: number }>;
}

// ---------------------------------------------------------------------------
// Dead Code
// ---------------------------------------------------------------------------

export interface DeadCodeFindingResponse {
  id: string;
  kind: string;
  file_path: string;
  symbol_name: string | null;
  symbol_kind: string | null;
  confidence: number;
  reason: string;
  lines: number;
  safe_to_delete: boolean;
  primary_owner: string | null;
  status: string;
  note: string | null;
}

export interface DeadCodePatchRequest {
  status: string;
  note?: string;
}

export interface DeadCodeSummaryResponse {
  total_findings: number;
  confidence_summary: Record<string, number>;
  deletable_lines: number;
  total_lines: number;
  by_kind: Record<string, number>;
}

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
  created_at: string;
  updated_at: string;
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
  status: string;
  superseded_by?: string;
}

export interface DecisionHealthResponse {
  summary: {
    active: number;
    proposed: number;
    deprecated: number;
    superseded: number;
    stale: number;
  };
  stale_decisions: DecisionRecordResponse[];
  proposed_awaiting_review: DecisionRecordResponse[];
  ungoverned_hotspots: string[];
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export interface HealthResponse {
  status: string;
  db: string;
  version: string;
}

// ---------------------------------------------------------------------------
// Webhooks
// ---------------------------------------------------------------------------

export interface WebhookResponse {
  event_id: string;
  status: string;
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

export interface ConversationResponse {
  id: string;
  repository_id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ChatMessageResponse {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: {
    text?: string;
    tool_calls?: Array<{
      id: string;
      name: string;
      arguments?: Record<string, unknown>;
      result?: Record<string, unknown>;
    }>;
  };
  created_at: string;
}

export type ChatSSEEvent =
  | { type: "text_delta"; text: string }
  | {
      type: "tool_start";
      tool_id: string;
      tool_name: string;
      input: Record<string, unknown>;
    }
  | {
      type: "tool_result";
      tool_id: string;
      tool_name: string;
      summary: string;
      artifact: { type: string; data: Record<string, unknown> };
    }
  | { type: "done"; conversation_id: string; message_id: string }
  | { type: "error"; message: string };

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

export interface ProviderInfo {
  id: string;
  name: string;
  models: string[];
  default_model: string;
  configured: boolean;
}

export interface ProvidersResponse {
  active: {
    provider: string | null;
    model: string | null;
  };
  providers: ProviderInfo[];
}

// ---------------------------------------------------------------------------
// API error
// ---------------------------------------------------------------------------

export interface ApiError {
  detail: string;
  status: number;
}
