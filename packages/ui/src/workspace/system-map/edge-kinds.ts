/**
 * Edge-kind registry — the single source of truth for how each
 * `SystemEdgeKind` is labelled, coloured, and iconified on the Live System Map.
 *
 * Adding a new transport (the plan's growing dimension) is a new entry here
 * plus the matching `SystemEdgeKind` union member in
 * `@repowise-dev/types/workspace` — never an edit to a rendering `if/else`.
 *
 * Two orthogonal axes drive an edge's appearance:
 *   - `kind`       → colour + glyph + structural/behavioral category (this file)
 *   - `match_type` → stroke dash pattern (exact solid, candidate dashed,
 *                    inferred dotted) — see `matchTypeDash`.
 */

import { ArrowRight, Boxes, Database, Link2, Radio, Signal, Zap, type LucideIcon } from "lucide-react";
import type { SystemEdge, SystemEdgeFamily, SystemEdgeKind, SystemEdgeMatchType } from "@repowise-dev/types";

/** Whether an edge reflects a structural contract/dependency or behavioral co-change. */
export type EdgeCategory = "structural" | "behavioral";

/**
 * The filter family an edge kind belongs to. The map offers families, not
 * kinds, as its first filter row: Runtime and Data are drawn by default, Build
 * (package) and History (co-change) are opt-in. Core sends it on each edge;
 * the registry's `family` answers only for payloads written before that.
 */
export type EdgeFamily = SystemEdgeFamily;

export interface SystemEdgeKindStyle {
  kind: SystemEdgeKind;
  /** Short human label for the legend and inspector. */
  label: string;
  /** Semantic tint (a theme CSS var) — carried by the chip glyph. */
  color: string;
  icon: LucideIcon;
  category: EdgeCategory;
  family: EdgeFamily;
}

/**
 * Canonical edge-kind styles. Co-change is the only behavioral kind; every
 * contract / dependency transport is structural. Colours reference existing
 * theme vars so the map stays consistent with the C4 / knowledge-graph views.
 */
export const SYSTEM_EDGE_KINDS: Record<SystemEdgeKind, SystemEdgeKindStyle> = {
  http: { kind: "http", label: "HTTP", color: "var(--color-accent-secondary)", icon: ArrowRight, category: "structural", family: "runtime" },
  grpc: { kind: "grpc", label: "gRPC", color: "var(--color-accent-fill)", icon: Zap, category: "structural", family: "runtime" },
  socket: { kind: "socket", label: "Socket", color: "var(--color-info)", icon: Signal, category: "structural", family: "runtime" },
  event: { kind: "event", label: "Event", color: "var(--color-warning)", icon: Radio, category: "structural", family: "runtime" },
  package: { kind: "package", label: "Package", color: "var(--color-accent-primary)", icon: Boxes, category: "structural", family: "build" },
  db: { kind: "db", label: "Database", color: "var(--color-text-tertiary)", icon: Database, category: "structural", family: "data" },
  co_change: { kind: "co_change", label: "Co-change", color: "var(--color-edge-co-change)", icon: Link2, category: "behavioral", family: "history" },
};

const FALLBACK_KIND: SystemEdgeKindStyle = {
  kind: "http",
  label: "Link",
  color: "var(--color-text-tertiary)",
  icon: ArrowRight,
  category: "structural",
  // Matches core: a kind it does not know is History, so it starts hidden.
  family: "history",
};

/** Resolve the style for an edge kind, tolerant of an unknown future kind. */
export function edgeKindStyle(kind: string): SystemEdgeKindStyle {
  return SYSTEM_EDGE_KINDS[kind as SystemEdgeKind] ?? FALLBACK_KIND;
}

/**
 * A link a gateway's route config made (`gateway_route` strategy). It keeps
 * its transport kind and its match dash: routing is how the link was found,
 * not a third line style, so it is named in words on hover and in the legend
 * instead.
 */
export function isGatewayRouted(edge: Pick<SystemEdge, "strategy">): boolean {
  return edge.strategy === "gateway_route";
}

/** The edge's hover label: its kind, plus "via gateway" when a gateway route made it. */
export function edgeLabel(edge: Pick<SystemEdge, "kind" | "strategy">): string {
  const label = edgeKindStyle(edge.kind).label;
  return isGatewayRouted(edge) ? `${label} via gateway` : label;
}

/** Every edge kind, in legend/display order (structural first, behavioral last). */
export const EDGE_KIND_ORDER: SystemEdgeKind[] = ["http", "grpc", "socket", "event", "package", "db", "co_change"];

/**
 * Stroke dash by match confidence: exact / manual links are solid (we trust
 * them), candidate links are dashed (heuristic), inferred (co-change) is dotted
 * (behavioral, never a guaranteed call). One place, so the map and the legend
 * never disagree.
 */
export function matchTypeDash(matchType: SystemEdgeMatchType): string {
  switch (matchType) {
    case "candidate":
      return "6 4";
    case "inferred":
      return "2 4";
    case "exact":
    case "manual":
    default:
      return "none";
  }
}

/** Human label for a match type (legend + inspector). */
export function matchTypeLabel(matchType: SystemEdgeMatchType): string {
  switch (matchType) {
    case "exact":
      return "Exact";
    case "candidate":
      return "Candidate";
    case "manual":
      return "Manual";
    case "inferred":
      return "Inferred";
    default:
      return matchType;
  }
}
