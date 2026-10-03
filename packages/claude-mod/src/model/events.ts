/**
 * Engine events to session actions. Pure.
 */

import type { SessionAction } from "./session";

/** Ids of tool calls a mod made itself (`$.mcp.call`), never Claude's. */
const PLUGIN_CALL_PREFIX = "toolu_plugin_";

export function isPluginCall(toolUseId: string | undefined): boolean {
  return typeof toolUseId === "string" && toolUseId.startsWith(PLUGIN_CALL_PREFIX);
}

/** A finished turn counts only for the main loop; a subagent's carries `agentId`. */
export function fromTurnComplete(e: { agentId?: string | undefined }): SessionAction | null {
  return e.agentId === undefined ? { type: "turnCompleted" } : null;
}
