/**
 * Lens's own read-only calls to this plugin's repowise MCP server. They go
 * through Claude Code's permission rules like any MCP call, and the engine
 * runs one only when it STARTS while one of Lens's hooks is live, so a caller
 * starts the call inside its hook (awaited or `void`), never from a timer or
 * a late promise.
 */

import type { Host } from "../host";
import type { McpToolResult } from "../mod-api";
import type { FileContext } from "../model/session";
import { withTimeout } from "./transport";

/** Only the tools a shipped Lens feature calls. All read-only. */
export const LENS_TOOLS = ["get_context", "get_change_risk", "get_why", "get_answer"] as const;
export type LensTool = (typeof LENS_TOOLS)[number];

/** Both spellings of this plugin's server seen at runtime; Lens calls no other. */
const PLUGIN_SERVER_FORMS: readonly string[] = ["plugin:repowise:repowise", "plugin_repowise_repowise"];

const DEFAULT_TIMEOUT_MS = 10_000;

let resolvedServer: string | null = null;
let resolving: Promise<void> | null = null;

/** Forget the resolved server; a new session resolves again. */
export function resetMcp(): void {
  resolvedServer = null;
  resolving = null;
}

/**
 * Asks the engine, once, which name this plugin's server runs under, and
 * keeps it only when it is one of this plugin's own forms. Never awaited by a
 * caller: connecting can take seconds, and an MCP call started after its hook
 * returned is refused (REST through `$.http` from a late continuation works;
 * a late MCP call does not).
 */
export function warmMcp(host: Host): void {
  if (resolvedServer !== null || resolving !== null) return;
  const asked = host.mcp
    .server()
    .then((name) => {
      if (name !== null && PLUGIN_SERVER_FORMS.includes(name)) resolvedServer = name;
    })
    .catch(() => undefined)
    .finally(() => {
      // Unresolved: the next hook asks again.
      if (resolving === asked) resolving = null;
    });
  resolving = asked;
}

/**
 * Waits for the server name, for a hook that stays live while it waits (a
 * command): the call it then starts is still started inside that hook.
 * True once resolved; false when the server did not connect in time.
 */
export async function mcpReady(host: Host, timeoutMs = DEFAULT_TIMEOUT_MS): Promise<boolean> {
  warmMcp(host);
  const pending = resolving;
  if (resolvedServer === null && pending !== null) await withTimeout(pending, timeoutMs, "MCP connect").catch(() => undefined);
  return resolvedServer !== null;
}

function parse<T>(result: McpToolResult, tool: string): T {
  const text = result.content[0]?.text;
  if (result.isError) throw new Error(`${tool} failed: ${String(text).slice(0, 200)}`);
  if (typeof text !== "string") throw new Error(`${tool} returned no text`);
  const parsed = JSON.parse(text) as { result?: T };
  // Claude Code hands the tool's structured result over as `{"result": ...}`;
  // a plain MCP client sees the bare object. Both are the same server's answer.
  return (parsed !== null && typeof parsed === "object" && "result" in parsed ? parsed.result : parsed) as T;
}

/**
 * Calls one allowlisted tool and returns the parsed `result`. The call starts
 * synchronously, so it starts while the calling hook is live (the engine
 * runs Lens's call only then); with no server name resolved yet it does
 * not start at all and rejects, and the caller tries again from a later hook.
 * Rejects on timeout or a tool error; nothing retries after the hook.
 */
export function callTool<T>(
  host: Host,
  tool: LensTool,
  args: Record<string, unknown>,
  opts: { timeoutMs?: number } = {},
): Promise<T> {
  if (resolvedServer === null) {
    warmMcp(host);
    return Promise.reject(new Error("repowise MCP server name not resolved yet"));
  }
  const call = host.mcp.call(resolvedServer, tool, args).then((r) => parse<T>(r, tool));
  return withTimeout(call, opts.timeoutMs ?? DEFAULT_TIMEOUT_MS, tool);
}

// Typed here, not shared: the MCP server has no generated TypeScript types,
// and Lens reads a few fields of a larger card.
interface ContextTarget {
  error?: string;
  callers?: unknown[];
  callers_total?: number;
  hotspot?: boolean;
  ownership?: { contributor_count?: number | null; recent_owner?: string | null; recent_owner_pct?: number | null };
}

function recentOwner(o: ContextTarget["ownership"]): FileContext["recentOwner"] {
  const name = o?.recent_owner;
  const share = o?.recent_owner_pct;
  return typeof name === "string" && name !== "" && typeof share === "number" ? { name, share } : null;
}

/** The file card's caller and contributor counts, hotspot mark and recent owner; null when the index does not know the file. */
export async function fetchFileContext(host: Host, path: string): Promise<FileContext | null> {
  const result = await callTool<{ targets?: Record<string, ContextTarget> }>(host, "get_context", {
    targets: [path],
    include: ["callers", "ownership"],
  });
  const target = result.targets?.[path];
  if (target === undefined || target.error !== undefined) return null;
  // The server stamps `callers_total` on every reduction of the list (its
  // construction cap and its response budget alike), so without it the list is whole.
  const callers = target.callers_total ?? target.callers?.length;
  const contributors = target.ownership?.contributor_count;
  return {
    callerFiles: typeof callers === "number" ? callers : null,
    contributors: typeof contributors === "number" ? contributors : null,
    hotspot: typeof target.hotspot === "boolean" ? target.hotspot : null,
    recentOwner: recentOwner(target.ownership),
  };
}
