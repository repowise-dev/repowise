// ---------------------------------------------------------------------------
// MCP tool surface
// ---------------------------------------------------------------------------

export interface McpToolInfo {
  name: string;
  description: string;
  default: boolean;
  requires_workspace: boolean;
  enabled: boolean;
  tier: string;
  default_single_repo: boolean;
  default_workspace: boolean;
  eligible: boolean;
  eligible_single_repo: boolean;
  eligible_workspace: boolean;
  recipes: Array<{ name: string; call: string; requires: string[] }>;
  artifact_type: string;
  presentation: string;
  safety: "read_only" | "generative" | "mutating" | string;
  evidence_basis: "measured" | "inferred" | "unknown";
}

export interface McpToolSurface {
  repo_id: string | null;
  is_workspace: boolean;
  override: string[] | string | null;
  tools: McpToolInfo[];
}

export interface UpdateMcpToolsRequest {
  repo_id: string;
  tools: string[] | string | null;
}
