/**
 * Internal data shapes for the Live System Map: the per-node / per-edge data
 * React Flow carries, the optional health join, and the additive **overlay
 * API** that later phases (blast-radius ripple, breaking-change badges,
 * conformance violations) layer on top without touching this component.
 */

import type { SystemEdge, SystemNode } from "@repowise-dev/types";

/** Repo-level health, joined client-side onto service nodes by repo alias. */
export interface RepoHealth {
  /** 0-100 health score. */
  score: number;
  /** Whether the score is canonical (computed) or derived (estimated). */
  source: "canonical" | "derived";
}

/** React Flow node payload — the `SystemNode` plus its joined health, if any. */
export interface SystemMapNodeData {
  node: SystemNode;
  health: RepoHealth | null;
  /** Overlay state applied this render (Phase 3+ ripple / badges). */
  overlay: NodeOverlayState | null;
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

/** Either a node or an edge selected in the map (drives the inspector). */
export type SystemMapSelection =
  | { type: "node"; id: string }
  | { type: "edge"; id: string }
  | null;
