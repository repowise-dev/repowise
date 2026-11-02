/**
 * A drawn levels view plus its positions, as React Flow nodes and edges.
 * Joins what each card shows (service record, repo health, role, lens marks,
 * group health and source) onto the drawn ids. Cards with no position yet
 * (new to the view while its layout runs) are left out, with their children
 * and lines, so nothing flashes at the origin. Pure, no React rendering.
 */

import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { NodeArchitectureRole, SystemEdge, SystemNode } from "@repowise-dev/types";
import type { C4LayoutPosition } from "../../c4/layout/elk-c4-layout";
import { bandSentence, groupSourceLabel, healthCounts, kindMixText, type HealthCounts } from "./aggregate-facts";
import { roleStyle } from "./architecture";
import { edgeLabel } from "./edge-kinds";
import { focusColumnLabels, type ColumnLabelData } from "./focus-labels";
import type {
  DrawnBand,
  DrawnGroupNode,
  DrawnMoreNode,
  DrawnNode,
  DrawnServiceNode,
  DrawnSingleEdge,
  DrawnTrayNode,
} from "./level-types";
import type { LevelView } from "./levels";
import {
  resolveEdgeOverlay,
  resolveNodeOverlay,
  type NodeOverlayState,
  type RepoHealth,
  type SystemMapEdgeData,
  type SystemMapNodeData,
  type SystemMapOverlay,
} from "./types";
import { formatNumber } from "../../lib/format";

export interface GroupNodeData {
  group: DrawnGroupNode;
  sourceLabel: string | null;
  health: HealthCounts;
  /** Lens marks folded up from the members: highlighted when any is, dimmed when all are. */
  overlay: NodeOverlayState | null;
  [key: string]: unknown;
}

export interface TrayNodeData {
  tray: DrawnTrayNode;
  /** Names of the first services, for the closed card. */
  names: string[];
  [key: string]: unknown;
}

export interface MoreNodeData {
  more: DrawnMoreNode;
  /** In a focus column: connectors join its sides. */
  horizontal?: boolean;
  [key: string]: unknown;
}

export interface BandEdgeData {
  band: DrawnBand;
  sentence: string;
  overlay: { highlighted: boolean; dimmed: boolean } | null;
  [key: string]: unknown;
}

export interface FlowInputs {
  view: LevelView;
  positions: ReadonlyMap<string, C4LayoutPosition> | null;
  nodesById: ReadonlyMap<string, SystemNode>;
  edgesById: ReadonlyMap<string, SystemEdge>;
  healthByRepo?: ReadonlyMap<string, RepoHealth> | undefined;
  overlay?: SystemMapOverlay | undefined;
  roleByNodeId?: ReadonlyMap<string, NodeArchitectureRole> | undefined;
  /** Drawn id to mark selected. */
  selectedId?: string | null | undefined;
}

export interface FlowElements {
  nodes: Node[];
  edges: Edge[];
}

/** Names on the tray's closed card before "+N more". */
const TRAY_NAMES = 3;

const ARROW = { type: MarkerType.ArrowClosed, width: 14, height: 14, color: "var(--color-diagram-edge)" } as const;
/** Bands are wide; an arrow in user units keeps one head size whatever the band's width. */
const BAND_ARROW = { ...ARROW, width: 16, height: 16, markerUnits: "userSpaceOnUse" } as const;

/** Members' lens marks on their group card, so a lens still reads at the estate. */
function groupOverlay(overlay: SystemMapOverlay | undefined, ids: readonly string[]): NodeOverlayState | null {
  if (!overlay) return null;
  const marks = ids.map((id) => resolveNodeOverlay(overlay, id));
  const highlighted = marks.some((m) => m?.highlighted);
  const dimmed = marks.length > 0 && marks.every((m) => m?.dimmed);
  return highlighted || dimmed ? { highlighted, dimmed } : null;
}

function bandOverlay(overlay: SystemMapOverlay | undefined, edgeIds: readonly string[]): BandEdgeData["overlay"] {
  if (!overlay) return null;
  const marks = edgeIds.map((id) => resolveEdgeOverlay(overlay, id));
  const highlighted = marks.some((m) => m?.highlighted);
  const dimmed = marks.length > 0 && marks.every((m) => m?.dimmed);
  return highlighted || dimmed ? { highlighted, dimmed } : null;
}

/** A drawn id's display name: a service's name, a group's label, or the aggregate's own words. */
export function drawnLabel(n: DrawnNode | undefined, nodesById: ReadonlyMap<string, SystemNode>): string {
  if (!n) return "";
  if (n.type === "service") return nodesById.get(n.serviceId)?.name ?? n.serviceId;
  if (n.type === "group") return n.label;
  if (n.type === "tray") return "Not linked at runtime";
  return `${n.serviceIds.length} more`;
}

type Shaped = { type: string; data: Record<string, unknown> } | null;

/** A focus-column card's links to its neighbour column: "8 · HTTP 2, Event 6", plus "both ways". */
function focusLinksText(n: DrawnServiceNode): string | null {
  if (!n.links) return null;
  const mix = kindMixText(n.links.kinds);
  const head = mix ? `${formatNumber(n.links.links)} · ${mix}` : formatNumber(n.links.links);
  return n.bothWays ? `${head} · both ways` : head;
}

function serviceData(n: DrawnServiceNode, { view, nodesById, healthByRepo, overlay, roleByNodeId }: FlowInputs): Shaped {
  const node = nodesById.get(n.serviceId);
  if (!node) return null;
  const role = roleByNodeId?.get(n.serviceId);
  const pair = view.between;
  const data: SystemMapNodeData = {
    node,
    health: healthByRepo?.get(node.repo) ?? null,
    overlay: resolveNodeOverlay(overlay, n.serviceId),
    role: role ? roleStyle(role.role).label : null,
    // Focus columns read left to right, so their connectors join card sides.
    horizontal: view.level === "focus",
    focusLinks: focusLinksText(n),
    endpoint: pair !== null && (n.serviceId === pair.a || n.serviceId === pair.b),
  };
  return { type: "systemService", data };
}

/** A service's repo health on the canonical 0-10 scale (repo health arrives 0-100); null when unscored. */
function score10(id: string, { nodesById, healthByRepo }: FlowInputs): number | null {
  const repo = nodesById.get(id)?.repo;
  const h = repo ? healthByRepo?.get(repo) : undefined;
  return h ? h.score / 10 : null;
}

function groupData(n: DrawnGroupNode, inputs: FlowInputs): Shaped {
  const data: GroupNodeData = {
    group: n,
    sourceLabel: groupSourceLabel(n.groupId, n.source, n.serviceIds.length),
    health: healthCounts(n.serviceIds.map((id) => score10(id, inputs))),
    overlay: groupOverlay(inputs.overlay, n.serviceIds),
  };
  return { type: "systemGroup", data };
}

function trayData(n: DrawnTrayNode, { nodesById }: FlowInputs): Shaped {
  const names = n.serviceIds.slice(0, TRAY_NAMES).map((id) => nodesById.get(id)?.name ?? id);
  const data: TrayNodeData = { tray: n, names };
  return { type: "systemTray", data };
}

function moreData(n: DrawnMoreNode, { view }: FlowInputs): Shaped {
  const data: MoreNodeData = { more: n, horizontal: view.level === "focus" };
  return { type: "systemMore", data };
}

function nodeData(n: DrawnNode, inputs: FlowInputs): Shaped {
  switch (n.type) {
    case "service":
      return serviceData(n, inputs);
    case "group":
      return groupData(n, inputs);
    case "tray":
      return trayData(n, inputs);
    default:
      return moreData(n, inputs);
  }
}

function parentOf(n: DrawnNode): string | null {
  return n.type === "service" || n.type === "more" ? n.parentId : null;
}

function isOpenContainer(n: DrawnNode): boolean {
  return (n.type === "group" || n.type === "tray") && n.open;
}

/** One React Flow node, or null when it has no position yet or its container is not drawn. */
function flowNode(n: DrawnNode, inputs: FlowInputs, placed: ReadonlySet<string>): Node | null {
  const pos = inputs.positions?.get(n.id);
  const parentId = parentOf(n);
  if (!pos || (parentId && !placed.has(parentId))) return null;
  const shaped = nodeData(n, inputs);
  if (!shaped) return null;
  return {
    id: n.id,
    type: shaped.type,
    position: { x: pos.x, y: pos.y },
    data: shaped.data,
    ...(parentId ? { parentId } : {}),
    // An open container sits under its cards and lines.
    ...(isOpenContainer(n) ? { width: pos.width, height: pos.height, zIndex: -1 } : {}),
    ...(inputs.selectedId === n.id ? { selected: true } : {}),
    ariaLabel: drawnLabel(n, inputs.nodesById),
  };
}

function singleEdge(e: DrawnSingleEdge, inputs: FlowInputs, name: (id: string) => string): Edge | null {
  const edge = inputs.edgesById.get(e.edgeId);
  if (!edge) return null;
  const data: SystemMapEdgeData = { edge, overlay: resolveEdgeOverlay(inputs.overlay, edge.id) };
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    type: "systemEdge",
    markerEnd: ARROW,
    data,
    ariaLabel: `${name(e.source)} to ${name(e.target)}, ${edgeLabel(edge)}`,
  };
}

function bandEdge(e: DrawnBand, inputs: FlowInputs, name: (id: string) => string): Edge {
  const sentence = bandSentence(name(e.source), name(e.target), e.summary);
  const data: BandEdgeData = { band: e, sentence, overlay: bandOverlay(inputs.overlay, e.summary.edgeIds) };
  return { id: e.id, source: e.source, target: e.target, type: "systemBand", markerEnd: BAND_ARROW, data, ariaLabel: sentence };
}

export function toFlowElements(inputs: FlowInputs): FlowElements {
  const { view, positions, nodesById, selectedId } = inputs;
  if (!positions) return { nodes: [], edges: [] };
  const placed = new Set<string>();
  const nodes: Node[] = [];
  for (const n of view.nodes) {
    const flow = flowNode(n, inputs, placed);
    if (!flow) continue;
    placed.add(n.id);
    nodes.push(flow);
  }

  const drawnById = new Map(view.nodes.map((n) => [n.id, n]));
  const name = (id: string) => drawnLabel(drawnById.get(id), nodesById);
  const edges: Edge[] = [];
  for (const e of view.edges) {
    if (!placed.has(e.source) || !placed.has(e.target)) continue;
    const flow = e.type === "edge" ? singleEdge(e, inputs, name) : bandEdge(e, inputs, name);
    if (flow) edges.push(selectedId === e.id ? { ...flow, selected: true } : flow);
  }
  if (view.focus) nodes.push(...columnLabelNodes(view, positions));
  return { nodes, edges };
}

/** The focus level's column headings, as inert nodes above each column. */
function columnLabelNodes(view: LevelView, positions: ReadonlyMap<string, C4LayoutPosition>): Node[] {
  return focusColumnLabels(view.focus!, positions).map((l): Node => {
    const data: ColumnLabelData = { label: l.label };
    return {
      id: l.id,
      type: "systemColumnLabel",
      position: { x: l.x, y: l.y },
      data,
      selectable: false,
      focusable: false,
      draggable: false,
    };
  });
}
