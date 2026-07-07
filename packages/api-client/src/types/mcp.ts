// ---------------------------------------------------------------------------
// MCP tool surface
// ---------------------------------------------------------------------------

export interface McpToolInfo {
  name: string;
  description: string;
  default: boolean;
  requires_workspace: boolean;
  enabled: boolean;
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
