/**
 * The System Map levels model: view state in, drawn graph out.
 *
 *  - Estate (L0) and group open (L1): `estate-view.ts`.
 *  - Focus (L2): callers, the service, dependencies (`focus-columns.ts`).
 *  - Between (L3): two services, the services on the paths joining them, and
 *    every drawn link among them (`between.ts`).
 *
 * Precedence is between, then focus, then the estate; an id that names no
 * service falls through to the next level. Every level is held to the draw
 * budget, and what does not fit is counted in the truncation report.
 * Pure, no React, deterministic.
 */

import { compareIds, connectEdges } from "./bands";
import { servicesBetween, type BetweenResult } from "./between";
import { DRAW_BUDGET, capEdges, truncationReport } from "./draw-budget";
import {
  DEFAULT_EDGE_FAMILIES,
  edgeFamilyOf,
  familyCounts,
  type EdgeFamily,
  type FamilyCount,
} from "./edge-families";
import { buildEstateView } from "./estate-view";
import { focusColumns, focusMoreId, focusNodeId, type FocusColumn, type FocusLayout } from "./focus-columns";
import type {
  DrawBudget,
  DrawnEdge,
  DrawnNode,
  LevelViewState,
  LevelsEdge,
  LevelsGraph,
  MapLevel,
  TruncationReport,
} from "./level-types";

export interface LevelView {
  level: MapLevel;
  /** True when the estate is drawn as groups. */
  grouped: boolean;
  nodes: DrawnNode[];
  edges: DrawnEdge[];
  /** Families drawn. */
  families: ReadonlySet<EdgeFamily>;
  /** Edges and links per family present in the whole graph, for the chips and the hidden-family caption. */
  familyCounts: Partial<Record<EdgeFamily, FamilyCount>>;
  truncation: TruncationReport | null;
  /** Focus level only. */
  focus: FocusLayout | null;
  /** Between level only. */
  between: BetweenResult | null;
}

/**
 * One node per id, edges only between known nodes, both in id order, so a
 * payload with duplicates or dangling edges draws no phantom card and the
 * view is the same whatever order the payload arrived in. Of two records
 * sharing an id, the one whose serialization sorts first wins.
 */
export function normalizeGraph(graph: LevelsGraph): LevelsGraph {
  const cached = normalized.get(graph);
  if (cached) return cached;
  const nodes = dedupeById(graph.nodes);
  const known = new Set(nodes.map((n) => n.id));
  const edges = dedupeById(graph.edges.filter((e) => known.has(e.source) && known.has(e.target)));
  const out = { ...graph, nodes, edges };
  normalized.set(graph, out);
  normalized.set(out, out);
  return out;
}

/** One normalized graph per payload object, so a filter toggle or a focus change never re-sorts the payload. */
const normalized = new WeakMap<LevelsGraph, LevelsGraph>();

/**
 * One record per id, in id order. A duplicate id keeps the copy that
 * serializes first, whatever its position; only colliding records are
 * serialized, so a clean payload costs one pass and a sort.
 */
function dedupeById<T extends { id: string }>(records: readonly T[]): T[] {
  const kept = new Map<string, T>();
  for (const record of records) {
    const prior = kept.get(record.id);
    if (!prior || stableKey(record) < stableKey(prior)) kept.set(record.id, record);
  }
  return [...kept.values()].sort((x, y) => compareIds(x.id, y.id));
}

/** JSON with keys in sorted order, so two equal records always serialize alike. */
function stableKey(record: object): string {
  return JSON.stringify(record, Object.keys(record).sort());
}

/** What every level reads: the normalized graph, the families shown, and the edges they keep. */
interface LevelInput {
  graph: LevelsGraph;
  families: ReadonlySet<EdgeFamily>;
  visible: LevelsEdge[];
  edgesById: Map<string, LevelsEdge>;
  budget: DrawBudget;
}

type LevelResult = Pick<LevelView, "level" | "grouped" | "nodes" | "edges" | "truncation" | "focus" | "between">;

/** Between (L3): the pair and the services on its paths, every drawn link among them. Null when a or b is unknown. */
function betweenLevel(input: LevelInput, pair: { a: string; b: string }): LevelResult | null {
  const between = servicesBetween(input.graph, pair.a, pair.b, { families: input.families });
  if (!between) return null;
  const drawn = new Set([between.a, between.b]);
  for (const p of [...between.pathsAToB, ...between.pathsBToA]) p.nodes.forEach((id) => drawn.add(id));
  const edges = connectEdges(input.visible, (id) => (drawn.has(id) ? id : null), () => true);
  const capped = capEdges(edges, input.budget.edges, input.edgesById);
  return {
    level: "between",
    grouped: false,
    focus: null,
    between,
    nodes: [...drawn].sort().map((id) => ({ type: "service", id, serviceId: id, parentId: null })),
    edges: capped.edges,
    truncation: truncationReport(input.budget, 0, 0, capped.droppedEdges, capped.droppedLinks),
  };
}

/** A focus column's cards, then its "+N more" when the budget folded some. */
function columnNodes(column: FocusColumn): DrawnNode[] {
  const nodes: DrawnNode[] = column.entries.map((e) => ({
    type: "service",
    id: focusNodeId(column.id, e.serviceId),
    serviceId: e.serviceId,
    parentId: null,
    column: column.id,
    links: e.summary,
    bothWays: e.bothWays,
  }));
  if (column.folded.length > 0) {
    nodes.push({ type: "more", id: focusMoreId(column.id), parentId: null, serviceIds: column.folded, groupIds: [] });
  }
  return nodes;
}

/** Focus (L2): left columns, the focus card, right columns. Null when the focus is unknown. */
function focusLevel(input: LevelInput, focusId: string, hops: 1 | 2 | undefined): LevelResult | null {
  const focus = focusColumns(input.graph, focusId, { families: input.families, hops, budget: input.budget });
  if (!focus) return null;
  const isLeft = (c: FocusColumn) => c.id === "callers" || c.id === "callers2";
  const focusCard: DrawnNode = { type: "service", id: focus.focus, serviceId: focus.focus, parentId: null, column: "focus" };
  const nodes = [
    ...focus.columns.filter(isLeft).flatMap(columnNodes),
    focusCard,
    ...focus.columns.filter((c) => !isLeft(c)).flatMap(columnNodes),
  ];
  const folded = new Set(focus.columns.flatMap((c) => c.folded));
  const edges: DrawnEdge[] = focus.connectors.map((c) => ({ type: "band", ...c }));
  const capped = capEdges(edges, input.budget.edges, input.edgesById);
  return {
    level: "focus",
    grouped: false,
    focus,
    between: null,
    nodes,
    edges: capped.edges,
    truncation: truncationReport(input.budget, folded.size, 0, capped.droppedEdges, capped.droppedLinks),
  };
}

/** The drawn graph for a view state. Exported helpers below it expect a normalized graph. */
export function buildLevelView(
  graph: LevelsGraph,
  state: LevelViewState = {},
  budget: DrawBudget = DRAW_BUDGET,
): LevelView {
  const normalized = normalizeGraph(graph);
  const families = state.families ?? DEFAULT_EDGE_FAMILIES;
  const input: LevelInput = {
    graph: normalized,
    families,
    visible: normalized.edges.filter((e) => families.has(edgeFamilyOf(e))),
    edgesById: new Map(normalized.edges.map((e) => [e.id, e])),
    budget,
  };
  const result =
    (state.between ? betweenLevel(input, state.between) : null) ??
    (state.focus ? focusLevel(input, state.focus, state.hops) : null) ?? {
      ...buildEstateView(normalized, state, input.visible, input.edgesById, budget),
      focus: null,
      between: null,
    };
  return { ...result, families, familyCounts: familyCounts(normalized.edges) };
}
