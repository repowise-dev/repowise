/**
 * What a click, a double-click or Enter on a drawn card or connector does,
 * as data: select something, open a container, focus a service, or list
 * what a "+N more" card folded. A single click selects; a double-click or
 * Enter opens an aggregate or focuses a service (its callers and
 * dependencies). Pure, no React.
 */

import type { DrawnEdge, DrawnNode } from "./level-types";
import type { SystemMapSelection } from "./types";
import { TRAY_OPEN_TOKEN } from "./view-state";

export type ActivateTrigger = "click" | "double" | "key";

export type Activation =
  | { kind: "select"; selection: SystemMapSelection }
  | { kind: "open"; id: string }
  | { kind: "focus"; serviceId: string }
  | { kind: "folded"; serviceIds: readonly string[] }
  | null;

export function activateNode(n: DrawnNode | undefined, trigger: ActivateTrigger): Activation {
  if (!n) return null;
  const opens = trigger !== "click";
  switch (n.type) {
    case "service":
      return opens
        ? { kind: "focus", serviceId: n.serviceId }
        : { kind: "select", selection: { type: "node", id: n.serviceId } };
    case "group":
      return opens && !n.open
        ? { kind: "open", id: n.groupId }
        : { kind: "select", selection: { type: "group", id: n.groupId } };
    case "tray":
      return n.open ? null : { kind: "open", id: TRAY_OPEN_TOKEN };
    default:
      return opens ? { kind: "folded", serviceIds: n.serviceIds } : null;
  }
}

export function activateEdge(e: DrawnEdge | undefined): Activation {
  if (!e) return null;
  return { kind: "select", selection: e.type === "edge" ? { type: "edge", id: e.edgeId } : { type: "band", id: e.id } };
}
