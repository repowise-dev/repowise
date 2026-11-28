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
  /** About how many tokens the schema costs a client that loads every schema up front. */
  schema_tokens?: number;
}

/** Named tool sets; unset means each client gets its own default. */
export type McpToolProfile = "core" | "lean" | "standard" | "all";

export interface McpToolSurface {
  repo_id: string | null;
  is_workspace: boolean;
  override: string[] | string | null;
  /** The profile the override names; null when each client gets its default. */
  profile?: McpToolProfile | null;
  /** Each profile's tools in this mode. */
  profiles?: Partial<Record<McpToolProfile, string[]>>;
  tools: McpToolInfo[];
  /** The `repowise config` command that sets this surface. */
  command?: string;
  /** Per agent asked for with `clients`: its profile (null for an exact list) and tools. */
  clients?: Record<string, { profile?: string | null; tools: string[] }>;
}

/** One agent's MCP config for a repo, as `repowise agents print-config` prints it. */
export interface McpClientConfig {
  repo_id: string;
  clients: Array<{ id: string; display_name: string }>;
  target: string;
  display_name: string;
  config: string;
  docs_url: string | null;
  /** The profile this agent lists; null when `mcp.tools` is an exact list. */
  profile: string | null;
  tool_count: number;
  /** `mcp.always_load` for an agent that defers schemas; null for one that loads them up front. */
  always_load: boolean | null;
}

export interface UpdateMcpToolsRequest {
  repo_id: string;
  tools: string[] | string | null;
}
