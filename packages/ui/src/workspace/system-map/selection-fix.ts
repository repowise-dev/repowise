/**
 * A selection names a service, a system edge, a group or a band; the canvas
 * draws cards and connectors. This maps the one onto the other, and when a
 * selection from the URL or a list below the map is hidden at the current
 * level, names the smallest view change that draws it: open the group (or
 * tray) holding a service, or show the family an edge belongs to. It never
 * changes the level (focus, between) for a selection. Pure, no React.
 */

import type { SystemEdge } from "@repowise-dev/types";
import { edgeFamilyOf } from "./edge-families";
import type { DrawnNode } from "./level-types";
import type { LevelView } from "./levels";
import type { SystemMapSelection } from "./types";
import { TRAY_OPEN_TOKEN, openContainer, showFamily, type MapViewState } from "./view-state";

export interface ResolvedSelection {
  /** The drawn node or connector id to mark selected; null when nothing drawn stands for it. */
  drawnId: string | null;
  drawnKind: "node" | "edge" | null;
  /** A view that would draw the selection, or null when it is drawn or cannot be. */
  fix: MapViewState | null;
}

const NONE: ResolvedSelection = { drawnId: null, drawnKind: null, fix: null };

/** The closed container a service is folded into: a group id, the tray, or nothing. */
function containerOf(nodes: readonly DrawnNode[], serviceId: string): string | null {
  for (const n of nodes) {
    if (n.type === "group" && !n.open && n.serviceIds.includes(serviceId)) return n.groupId;
    if (n.type === "tray" && !n.open && n.serviceIds.includes(serviceId)) return TRAY_OPEN_TOKEN;
  }
  return null;
}

/** The card drawn for a service: the focus card first, then any column copy. */
function serviceCard(nodes: readonly DrawnNode[], serviceId: string): string | null {
  let first: string | null = null;
  for (const n of nodes) {
    if (n.type !== "service" || n.serviceId !== serviceId) continue;
    if (n.column === "focus" || n.column === undefined) return n.id;
    first ??= n.id;
  }
  return first;
}

function atEstate(view: LevelView): boolean {
  return view.level === "estate" || view.level === "group";
}

function resolveService(id: string, view: LevelView, state: MapViewState): ResolvedSelection {
  const card = serviceCard(view.nodes, id);
  if (card) return { drawnId: card, drawnKind: "node", fix: null };
  const holder = atEstate(view) ? containerOf(view.nodes, id) : null;
  return { ...NONE, fix: holder ? openContainer(state, holder) : null };
}

function resolveGroup(id: string, view: LevelView): ResolvedSelection {
  const group = view.nodes.find((n) => n.type === "group" && n.groupId === id);
  return group ? { drawnId: group.id, drawnKind: "node", fix: null } : NONE;
}

function resolveBand(id: string, view: LevelView): ResolvedSelection {
  return view.edges.some((e) => e.id === id) ? { drawnId: id, drawnKind: "edge", fix: null } : NONE;
}

/** Open what folds an edge's two ends, so it gets its own line. */
function openEnds(edge: SystemEdge, view: LevelView, state: MapViewState): MapViewState | null {
  let fix: MapViewState | null = null;
  for (const end of [edge.source, edge.target]) {
    const holder = containerOf(view.nodes, end);
    if (holder) fix = openContainer(fix ?? state, holder);
  }
  return fix;
}

function resolveEdge(
  id: string,
  view: LevelView,
  edgesById: ReadonlyMap<string, SystemEdge>,
  state: MapViewState,
): ResolvedSelection {
  const edge = edgesById.get(id);
  if (!edge) return NONE;
  const drawn = view.edges.find((e) => e.type === "edge" && e.edgeId === edge.id);
  if (drawn) return { drawnId: drawn.id, drawnKind: "edge", fix: null };
  const family = edgeFamilyOf(edge);
  if (!view.families.has(family)) return { ...NONE, fix: showFamily(state, family) };
  return { ...NONE, fix: atEstate(view) ? openEnds(edge, view, state) : null };
}

export function resolveSelection(
  selection: SystemMapSelection,
  view: LevelView,
  edgesById: ReadonlyMap<string, SystemEdge>,
  state: MapViewState,
): ResolvedSelection {
  switch (selection?.type) {
    case "node":
      return resolveService(selection.id, view, state);
    case "group":
      return resolveGroup(selection.id, view);
    case "band":
      return resolveBand(selection.id, view);
    case "edge":
      return resolveEdge(selection.id, view, edgesById, state);
    default:
      return NONE;
  }
}
