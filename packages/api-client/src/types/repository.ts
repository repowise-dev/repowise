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
  // Workspace mode (optional — only populated when the server runs in
  // workspace mode). Unindexed repos appear as synthetic rows with
  // `id="ws:<alias>"` and `workspace_status === "needs_index"`.
  workspace_alias?: string | null;
  workspace_status?: "indexed" | "needs_index" | "missing_dir" | null;
  is_primary?: boolean | null;
  docs_enabled?: boolean | null;
  docs_skip_reason?: string | null;
}
