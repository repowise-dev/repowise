/**
 * Engine events to session actions. Pure.
 */

import type { SessionAction } from "./session";

/** A finished turn counts only for the main loop; a subagent's carries `agentId`. */
export function fromTurnComplete(e: { agentId?: string | undefined }): SessionAction | null {
  return e.agentId === undefined ? { type: "turnCompleted" } : null;
}
