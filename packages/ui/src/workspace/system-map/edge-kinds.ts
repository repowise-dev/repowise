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

import { ArrowRight, Boxes, Database, Link2, Radio, Zap, type LucideIcon } from "lucide-react";
import type { SystemEdgeKind, SystemEdgeMatchType } from "@repowise-dev/types";

/** Whether an edge reflects a structural contract/dependency or behavioral co-change. */
export type EdgeCategory = "structural" | "behavioral";

export interface SystemEdgeKindStyle {
  kind: SystemEdgeKind;
  /** Short human label for the legend and inspector. */
  label: string;
  /** Semantic tint (a theme CSS var) — carried by the chip glyph. */
  color: string;
  icon: LucideIcon;
  category: EdgeCategory;
}

/**
 * Canonical edge-kind styles. Co-change is the only behavioral kind; every
 * contract / dependency transport is structural. Colours reference existing
 * theme vars so the map stays consistent with the C4 / knowledge-graph views.
 */
export const SYSTEM_EDGE_KINDS: Record<SystemEdgeKind, SystemEdgeKindStyle> = {
  http: { kind: "http", label: "HTTP", color: "var(--color-accent-secondary)", icon: ArrowRight, category: "structural" },
  grpc: { kind: "grpc", label: "gRPC", color: "var(--color-accent-fill)", icon: Zap, category: "structural" },
  event: { kind: "event", label: "Event", color: "var(--color-warning)", icon: Radio, category: "structural" },
  package: { kind: "package", label: "Package", color: "var(--color-accent-primary)", icon: Boxes, category: "structural" },
  db: { kind: "db", label: "Database", color: "var(--color-text-tertiary)", icon: Database, category: "structural" },
  co_change: { kind: "co_change", label: "Co-change", color: "var(--color-edge-co-change)", icon: Link2, category: "behavioral" },
};

const FALLBACK_KIND: SystemEdgeKindStyle = {
  kind: "http",
  label: "Link",
  color: "var(--color-text-tertiary)",
  icon: ArrowRight,
  category: "structural",
};

/** Resolve the style for an edge kind, tolerant of an unknown future kind. */
export function edgeKindStyle(kind: string): SystemEdgeKindStyle {
  return SYSTEM_EDGE_KINDS[kind as SystemEdgeKind] ?? FALLBACK_KIND;
}

/** Every edge kind, in legend/display order (structural first, behavioral last). */
export const EDGE_KIND_ORDER: SystemEdgeKind[] = ["http", "grpc", "event", "package", "db", "co_change"];

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
