import { apiGet, apiPatch } from "./client";
import type { McpToolSurface, UpdateMcpToolsRequest } from "./types";

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
