/**
 * Focus (L2): one service in the middle, what calls it on the left, what it
 * depends on on the right, sorted by link count so each column reads as a
 * list. A service on both sides sits in both columns, marked `bothWays`.
 * Two hops add callers of callers far left and dependencies of dependencies
 * far right. Placement is a fixed column grid, no ELK. Pure, no React.
 */

import type { C4LayoutPosition } from "../../c4/layout/elk-c4-layout";
import { addLink, bandId, compareIds, emptySummary } from "./bands";
import { allocateSlots } from "./draw-budget";
import { edgeFamilyOf, type EdgeFamily } from "./edge-families";
import type { DrawBudget, FocusColumnId, LevelsEdge, LevelsGraph, LinkSummary } from "./level-types";

export interface FocusEntry {
  serviceId: string;
  /** Links between this service and its neighbour column (the focus, for one hop). */
  summary: LinkSummary;
  bothWays: boolean;
}

export interface FocusColumn {
  id: FocusColumnId;
  /** Drawn entries, strongest first. */
  entries: FocusEntry[];
  /** Service ids the draw budget folded into the column's "+N more" node. */
  folded: string[];
}

/** A connector between two drawn ids, every link of the pair in one. */
export interface FocusConnector {
  id: string;
  source: string;
  target: string;
  summary: LinkSummary;
}

export interface FocusLayout {
  focus: string;
  hops: 1 | 2;
  /** Left to right, without the focus: callers2?, callers, dependencies, dependencies2?. */
  columns: FocusColumn[];
  connectors: FocusConnector[];
}

/** Drawn id of a service card in a focus column; the focus card keeps the service id. */
export function focusNodeId(column: FocusColumnId, serviceId: string): string {
  return `${column}:${serviceId}`;
}

export function focusMoreId(column: FocusColumnId): string {
  return `more:${column}`;
}

type Neighbours = Map<string, LinkSummary>;

function collect(into: Neighbours, key: string, edge: LevelsEdge): void {
  let s = into.get(key);
  if (!s) into.set(key, (s = emptySummary()));
  addLink(s, edge);
}

function ranked(neighbours: Neighbours, other: Neighbours): FocusEntry[] {
  return [...neighbours]
    .map(([serviceId, summary]) => ({ serviceId, summary, bothWays: other.has(serviceId) }))
    .sort((x, y) => y.summary.links - x.summary.links || compareIds(x.serviceId, y.serviceId));
}

type PlannedColumn = { id: FocusColumnId; entries: FocusEntry[] };

/** Neighbours of `focus` one way (callers or dependencies), from the edges given. */
function ring(edges: readonly LevelsEdge[], into: (e: LevelsEdge) => string | null): Neighbours {
  const out: Neighbours = new Map();
  for (const e of edges) {
    const key = into(e);
    if (key !== null) collect(out, key, e);
  }
  return out;
}

/** The columns before the budget: one hop each side, plus the outer rings for two hops. */
function planColumns(edges: readonly LevelsEdge[], focus: string, hops: 1 | 2): PlannedColumn[] {
  const callers = ring(edges, (e) => (e.target === focus ? e.source : null));
  const deps = ring(edges, (e) => (e.source === focus ? e.target : null));
  const plan: PlannedColumn[] = [
    { id: "callers", entries: ranked(callers, deps) },
    { id: "dependencies", entries: ranked(deps, callers) },
  ];
  if (hops === 1) return plan;
  const ring1 = new Set([focus, ...callers.keys(), ...deps.keys()]);
  const callers2 = ring(edges, (e) => (callers.has(e.target) && !ring1.has(e.source) ? e.source : null));
  const deps2 = ring(edges, (e) => (deps.has(e.source) && !ring1.has(e.target) ? e.target : null));
  return [
    { id: "callers2", entries: ranked(callers2, deps2) },
    ...plan,
    { id: "dependencies2", entries: ranked(deps2, callers2) },
  ];
}

/** Keep what the node budget allows per column (the focus card takes one slot); fold the rest. */
function foldColumns(plan: readonly PlannedColumn[], budget: DrawBudget): FocusColumn[] {
  const kept = allocateSlots(
    plan.map((c) => ({ size: c.entries.length })),
    budget.nodes - 1,
  );
  return plan.map((c, i) => ({
    id: c.id,
    entries: c.entries.slice(0, kept[i]),
    folded: c.entries.slice(kept[i]).map((e) => e.serviceId),
  }));
}

/** Where a service is drawn in a column: its card, the column's "+N more", or null when not in it. */
function drawnIn(columns: readonly FocusColumn[]): (column: FocusColumnId, id: string) => string | null {
  const at = new Map<FocusColumnId, Map<string, string>>();
  for (const c of columns) {
    const m = new Map<string, string>();
    for (const e of c.entries) m.set(e.serviceId, focusNodeId(c.id, e.serviceId));
    for (const id of c.folded) m.set(id, focusMoreId(c.id));
    at.set(c.id, m);
  }
  return (column, id) => at.get(column)?.get(id) ?? null;
}

/** One connector per pair of drawn ids between neighbouring columns, sorted by id. */
function connectColumns(
  edges: readonly LevelsEdge[],
  focus: string,
  hops: 1 | 2,
  drawn: (column: FocusColumnId, id: string) => string | null,
): FocusConnector[] {
  const connectors = new Map<string, FocusConnector>();
  const connect = (source: string | null, target: string | null, e: LevelsEdge) => {
    if (source === null || target === null) return;
    const id = bandId(source, target);
    let c = connectors.get(id);
    if (!c) connectors.set(id, (c = { id, source, target, summary: emptySummary() }));
    addLink(c.summary, e);
  };
  // Each pair of neighbouring columns, as (source column, target column); null is the focus card.
  const pairs: [FocusColumnId | null, FocusColumnId | null][] = [
    ["callers", null],
    [null, "dependencies"],
  ];
  if (hops === 2) pairs.push(["callers2", "callers"], ["dependencies", "dependencies2"]);
  const at = (column: FocusColumnId | null, id: string) =>
    column === null ? (id === focus ? focus : null) : drawn(column, id);
  for (const e of edges) {
    for (const [from, to] of pairs) connect(at(from, e.source), at(to, e.target), e);
  }
  return [...connectors.values()].sort((x, y) => compareIds(x.id, y.id));
}

/**
 * Columns around `focus` in the families shown, folded to the draw budget.
 * Null when `focus` is not a service in the graph.
 */
export function focusColumns(
  graph: LevelsGraph,
  focus: string,
  options: { families: ReadonlySet<EdgeFamily>; hops?: 1 | 2 | undefined; budget: DrawBudget },
): FocusLayout | null {
  if (!graph.nodes.some((n) => n.id === focus)) return null;
  const hops = options.hops ?? 1;
  const edges = graph.edges.filter((e) => e.source !== e.target && options.families.has(edgeFamilyOf(e)));
  const columns = foldColumns(planColumns(edges, focus, hops), options.budget);
  return { focus, hops, columns, connectors: connectColumns(edges, focus, hops, drawnIn(columns)) };
}

export interface FocusPlacement {
  /** Card footprint, the same for every column. */
  size: { width: number; height: number };
  /** Horizontal gap between columns and vertical gap between cards. */
  gap?: { x: number; y: number };
}

/**
 * Top-left positions for every drawn id: columns left to right, each centred
 * on the focus card's row. Deterministic: same layout in, same positions out.
 */
export function placeFocusColumns(layout: FocusLayout, placement: FocusPlacement): Map<string, C4LayoutPosition> {
  const { width, height } = placement.size;
  const gap = placement.gap ?? { x: 96, y: 16 };
  const left = layout.columns.filter((c) => c.id === "callers2" || c.id === "callers");
  const right = layout.columns.filter((c) => c.id === "dependencies" || c.id === "dependencies2");
  const stacks: string[][] = [
    ...left.map((c) => columnIds(c)),
    [layout.focus],
    ...right.map((c) => columnIds(c)),
  ];
  const positions = new Map<string, C4LayoutPosition>();
  stacks.forEach((ids, col) => {
    const top = -((ids.length * height + (ids.length - 1) * gap.y) / 2);
    ids.forEach((id, row) => {
      positions.set(id, { x: col * (width + gap.x), y: top + row * (height + gap.y), width, height });
    });
  });
  return positions;
}

function columnIds(column: FocusColumn): string[] {
  const ids = column.entries.map((e) => focusNodeId(column.id, e.serviceId));
  if (column.folded.length > 0) ids.push(focusMoreId(column.id));
  return ids;
}
