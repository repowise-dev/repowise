import { apiGet, apiPatch } from "./client";
import type { McpClientConfig, McpToolSurface, UpdateMcpToolsRequest } from "./types";

/**
 * Fetch the configurable MCP tool surface for a repo. `clients` (agent ids, as
 * Setup lists them) adds each agent's resolved tools under `clients`.
 */
export async function getMcpToolSurface(
  repoId?: string,
  clients?: readonly string[],
): Promise<McpToolSurface> {
  const params: Record<string, string> = {};
  if (repoId) params.repo_id = repoId;
  if (clients?.length) params.clients = clients.join(",");
  return apiGet<McpToolSurface>(
    "/api/mcp/tools",
    Object.keys(params).length ? params : undefined,
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
