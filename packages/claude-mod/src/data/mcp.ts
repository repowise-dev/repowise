/**
 * Typed calls to this plugin's repowise MCP server. Only the read-only tools
 * Lens is allowed to approve for itself are callable from here.
 */

import type { Host } from "../host";
import { isPluginCall } from "../model/events";
import { withTimeout } from "./transport";

/** The server's key in the plugin's .mcp.json. */
export const MCP_SERVER_KEY = "repowise";

export const READ_ONLY_TOOLS = ["get_context", "get_change_risk", "get_why", "get_answer", "list_repos"] as const;
export type ReadOnlyTool = (typeof READ_ONLY_TOOLS)[number];

/** The name `$.mcp.call` takes for this plugin's server. */
export function mcpServerName(pluginName: string): string {
  return `plugin:${pluginName}:${MCP_SERVER_KEY}`;
}

/** The tool name `tool.check` sees for one of this plugin's server tools. */
export function mcpToolName(pluginName: string, tool: ReadOnlyTool): string {
  return `mcp__plugin_${pluginName}_${MCP_SERVER_KEY}__${tool}`;
}

/**
 * The one approval Lens ever gives: its own read-only call to its own server.
 * All three must hold: a mod-made call id, fired by this plugin, naming one of
 * the allowlisted tools on this plugin's server. Anything else is left to the
 * engine untouched.
 */
export function isOwnReadOnlyCall(
  e: { tool: string; tool_use_id: string },
  originPlugin: string,
  pluginName: string,
): boolean {
  return (
    isPluginCall(e.tool_use_id) &&
    originPlugin === pluginName &&
    READ_ONLY_TOOLS.some((tool) => mcpToolName(pluginName, tool) === e.tool)
  );
}

/** The server connects a few seconds after the session starts; calls before then fail with this. */
const NOT_CONNECTED = "no connected MCP tool";
const CONNECT_GRACE_MS = 5_000;
const RETRY_DELAY_MS = 500;
const DEFAULT_TIMEOUT_MS = 20_000;

export class McpCallError extends Error {
  constructor(tool: string, detail: string) {
    super(`${tool}: ${detail}`);
    this.name = "McpCallError";
  }
}

export interface Clock {
  now(): number;
  sleep(ms: number): Promise<void>;
}

const realClock: Clock = {
  now: () => Date.now(),
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
};

export interface CallOptions {
  /** When the session started, for the connect grace window. */
  startedAt: number;
  timeoutMs?: number;
  clock?: Clock;
}

function parseResult(tool: string, raw: { content: { type: string; text?: string }[]; isError: boolean }): unknown {
  const text = raw.content[0]?.text;
  if (raw.isError) throw new McpCallError(tool, text ?? "the server reported an error");
  if (typeof text !== "string") throw new McpCallError(tool, "no text content");
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new McpCallError(tool, "content is not JSON");
  }
  if (typeof parsed !== "object" || parsed === null || !("result" in parsed)) {
    throw new McpCallError(tool, "content has no result");
  }
  return (parsed as { result: unknown }).result;
}

/**
 * One call, retried while the server is still connecting, with a timeout over
 * the whole attempt. Await it inside a hook: a call raised from a detached
 * promise skips this plugin's own `tool.check`, so the engine refuses it.
 */
export async function callTool(
  host: Host,
  tool: ReadOnlyTool,
  args: Record<string, unknown>,
  options: CallOptions,
): Promise<unknown> {
  const clock = options.clock ?? realClock;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const attempt = async (): Promise<unknown> => {
    for (;;) {
      try {
        return parseResult(tool, await host.mcp.call(tool, args));
      } catch (err) {
        const connecting =
          err instanceof Error &&
          err.message.includes(NOT_CONNECTED) &&
          clock.now() - options.startedAt < CONNECT_GRACE_MS;
        if (!connecting) throw err;
        await clock.sleep(RETRY_DELAY_MS);
      }
    }
  };
  return withTimeout(attempt(), timeoutMs, tool);
}

export interface McpRepo {
  alias: string;
  path: string | null;
  absolute_path: string | null;
}

export interface ListReposResult {
  workspace: boolean;
  repos: McpRepo[];
}

/** Results Lens has not modelled yet stay as plain objects. */
export type McpObject = Record<string, unknown>;

export const mcp = {
  listRepos: (host: Host, o: CallOptions) => callTool(host, "list_repos", {}, o) as Promise<ListReposResult>,
  getContext: (host: Host, args: { targets: string[]; include?: string[] }, o: CallOptions) =>
    callTool(host, "get_context", args, o) as Promise<McpObject>,
  getChangeRisk: (host: Host, args: { revspec?: string }, o: CallOptions) =>
    callTool(host, "get_change_risk", args, o) as Promise<McpObject>,
  getWhy: (host: Host, args: { query?: string; targets?: string[] }, o: CallOptions) =>
    callTool(host, "get_why", args, o) as Promise<McpObject>,
  getAnswer: (host: Host, args: { question: string }, o: CallOptions) =>
    callTool(host, "get_answer", args, o) as Promise<McpObject>,
};
