import { apiGet, apiPatch } from "./client";
import type { McpClientConfig, McpToolSurface, UpdateMcpToolsRequest } from "./types";

/** Fetch the configurable MCP tool surface for a repo. */
export async function getMcpToolSurface(repoId?: string): Promise<McpToolSurface> {
  return apiGet<McpToolSurface>(
    "/api/mcp/tools",
    repoId ? { repo_id: repoId } : undefined,
  );
}

/** Persist a new `mcp.tools` override for a repo and return the updated surface. */
export async function updateMcpTools(
  body: UpdateMcpToolsRequest,
): Promise<McpToolSurface> {
  return apiPatch<McpToolSurface>("/api/mcp/tools", body);
}

/** One agent's MCP config snippet for a repo; without `target`, the first agent. */
export async function getMcpClientConfig(repoId: string, target?: string): Promise<McpClientConfig> {
  return apiGet<McpClientConfig>("/api/mcp/client-config", {
    repo_id: repoId,
    ...(target ? { target } : {}),
  });
}
