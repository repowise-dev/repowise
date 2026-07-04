// ---------------------------------------------------------------------------
// Repository
// ---------------------------------------------------------------------------

export interface RepoCreate {
  name: string;
  local_path: string;
  url?: string;
  default_branch?: string;
  settings?: Record<string, unknown>;
  /** Enqueue the first full index immediately (server default: true).
   * Pass false to register metadata only, e.g. to run a preflight check
   * before committing to generation spend. */
  index?: boolean;
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
  // Workspace metadata (populated in workspace mode). Unindexed workspace
  // repos appear as synthetic rows with `id="ws:<alias>"`. `workspace_status`
  // is also set to "needs_index" for plain registered-but-never-indexed
  // repos in any mode (no repo-local store yet).
  workspace_alias?: string | null;
  workspace_status?: "indexed" | "needs_index" | "missing_dir" | null;
  is_primary?: boolean | null;
  docs_enabled?: boolean | null;
  docs_skip_reason?: string | null;
  /** Set on POST /api/repos responses when the create enqueued the first
   * index; attach to /api/jobs/{id}/stream with it. */
  initial_job_id?: string | null;
}

// ---------------------------------------------------------------------------
// Index preflight
// ---------------------------------------------------------------------------

export interface PreflightEstimate {
  total_pages: number;
  estimated_cost_usd: number;
  cost_low_usd: number | null;
  cost_high_usd: number | null;
  estimated_input_tokens: number;
  estimated_output_tokens: number;
  is_calibrated: boolean;
  coverage_pct: number;
}

export interface PreflightResponse {
  provider: {
    ok: boolean;
    name: string | null;
    model: string | null;
    error: string | null;
  };
  file_count: number;
  /** Null when no provider is configured (index-only runs still work). */
  estimate: PreflightEstimate | null;
}
