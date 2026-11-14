/**
 * Internal data shapes for the Live System Map: the per-node / per-edge data
 * React Flow carries, the optional health join, and the additive **overlay
 * API** that later phases (blast-radius ripple, breaking-change badges,
 * conformance violations) layer on top without touching this component.
 */

import type { SystemEdge, SystemNode } from "@repowise-dev/types";

/** Repo-level health, joined client-side onto service nodes by repo alias. */
export interface RepoHealth {
  /** 0-100 code health score. */
  score: number;
}

/** React Flow node payload — the `SystemNode` plus its joined health, if any. */
export interface SystemMapNodeData {
  node: SystemNode;
  health: RepoHealth | null;
  /** Overlay state applied this render (Phase 3+ ripple / badges). */
  overlay: NodeOverlayState | null;
  /** Architecture role label ("Core", "Shared"...), named in text on the node. */
  role: string | null;
  /** Focus level: connectors join card sides, since its columns read left to right. */
  horizontal?: boolean;
  /** Focus level: the links to the neighbour column ("8 · HTTP 2, Event 6"), in place of the contract counts. */
  focusLinks?: string | null;
  /** Between level: one of the two services compared. */
  endpoint?: boolean;
  [key: string]: unknown;
}

/** React Flow edge payload. */
export interface SystemMapEdgeData {
  edge: SystemEdge;
  overlay: EdgeOverlayState | null;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Overlay API — additive layers Phases 3-5 attach to the map. Phase 2 ships
// the contract and renders it; it passes nothing, so the map is a faithful
// render of the system graph. Later phases compute a `SystemMapOverlay` and
// hand it in; the map dims/highlights/badges accordingly. Designing this now
// is the D7 requirement (overlays must not fork the map component).
// ---------------------------------------------------------------------------

export type BadgeTone = "danger" | "warning" | "info";

export interface SystemMapBadge {
  label: string;
  tone: BadgeTone;
}

/** Per-node overlay decoration resolved for one render. */
export interface NodeOverlayState {
  /** Emphasize this node (e.g. a blast-radius target or reachable service). */
  highlighted?: boolean;
  /** Fade this node (outside the current focus set). */
  dimmed?: boolean;
  /** Optional corner badge (e.g. "3 breaking"). */
  badge?: SystemMapBadge;
}

/** Per-edge overlay decoration resolved for one render. */
export interface EdgeOverlayState {
  highlighted?: boolean;
  dimmed?: boolean;
  badge?: SystemMapBadge;
}

/**
 * An additive overlay over the whole map. Sets are keyed by node/edge id; a
 * later phase populates only what it needs. Empty/undefined means "no overlay,"
 * which is exactly the Phase 2 default.
 */
export interface SystemMapOverlay {
  highlightNodeIds?: ReadonlySet<string>;
  dimNodeIds?: ReadonlySet<string>;
  nodeBadges?: Readonly<Record<string, SystemMapBadge>>;
  highlightEdgeIds?: ReadonlySet<string>;
  dimEdgeIds?: ReadonlySet<string>;
  edgeBadges?: Readonly<Record<string, SystemMapBadge>>;
}

export function resolveNodeOverlay(
  overlay: SystemMapOverlay | undefined,
  nodeId: string,
): NodeOverlayState | null {
  if (!overlay) return null;
  const highlighted = overlay.highlightNodeIds?.has(nodeId) ?? false;
  const dimmed = overlay.dimNodeIds?.has(nodeId) ?? false;
  const badge = overlay.nodeBadges?.[nodeId];
  if (!highlighted && !dimmed && !badge) return null;
  return { highlighted, dimmed, ...(badge ? { badge } : {}) };
}

export function resolveEdgeOverlay(
  overlay: SystemMapOverlay | undefined,
  edgeId: string,
): EdgeOverlayState | null {
  if (!overlay) return null;
  const highlighted = overlay.highlightEdgeIds?.has(edgeId) ?? false;
  const dimmed = overlay.dimEdgeIds?.has(edgeId) ?? false;
  const badge = overlay.edgeBadges?.[edgeId];
  if (!highlighted && !dimmed && !badge) return null;
  return { highlighted, dimmed, ...(badge ? { badge } : {}) };
}

/**
 * What the map has selected (drives the rail). `node` is a service id and
 * `edge` a system edge id, so a host can put either in its URL; `group` is a
 * group id and `band` the id of a band drawn at the current level.
 */
export type SystemMapSelection =
  | { type: "node"; id: string }
  | { type: "edge"; id: string }
  | { type: "group"; id: string }
  | { type: "band"; id: string }
  | null;
