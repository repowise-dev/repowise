/**
 * A deterministic zoom map the size of a real repository, plus a canvas stub
 * that counts calls, for the perf tests. Shaped after the repowise index the
 * hover freeze was reported on: 11 layers, 33 groups, ~660 folders, ~6,900 files,
 * ~4,400 relations, and two flat folders whose first file is a hub touching
 * every sibling: one of 271 files, and one of 330 that the per-parent child cap
 * (`MAX_CHILDREN_DRAWN`) trims.
 */

import type { ZoomKind, ZoomMap, ZoomNode, ZoomRelation } from "../../src/zoom/types";

export const HUB_FOLDER = "f:0:0:0";
export const HUB_FILE = `${HUB_FOLDER}/0`;
export const CAPPED_FOLDER = "f:0:0:1";
export const CAPPED_HUB = `${CAPPED_FOLDER}/0`;
const FLAT_FOLDERS: Record<string, number> = { [HUB_FOLDER]: 271, [CAPPED_FOLDER]: 330 };

function node(id: string, parent: string | null, kind: ZoomKind, rank: number): ZoomNode {
  return {
    id,
    parent_id: parent,
    level: 0,
    kind,
    name: `${kind} ${id}`,
    path: id,
    children: [],
    importance: ((rank * 37) % 100) / 100,
    sibling_rank: rank + 1,
    metrics: {
      file_count: 1,
      descendant_count: 0,
      hotspot_count: 0,
      dead_count: 0,
      entry_point_count: 0,
      on_flow_count: 0,
    },
    summary: "A summary line long enough to be truncated on a small card.",
    language: kind === "file" ? "ts" : null,
    page_id: "",
    health_score: rank % 3 === 0 ? 7.5 : null,
    is_entry_point: rank % 11 === 0,
    is_hotspot: rank % 13 === 0,
    is_dead: false,
    is_test: false,
    on_flow: false,
  };
}

const VERBS = ["imports", "calls", "co-changes", "uses"];

type Rel = (parent: string, a: string, b: string, i: number) => void;
type Child = (id: string, parent: string, kind: ZoomKind, rank: number) => string;

/** One folder of files with a few relations among them; a flat folder adds a hub. */
function addFolder(folder: string, child: Child, rel: Rel): void {
  const files = FLAT_FOLDERS[folder] ?? 10;
  for (let i = 0; i < files; i++) child(`${folder}/${i}`, folder, "file", i);
  for (let i = 0; i < 5; i++) rel(folder, `${folder}/${i}`, `${folder}/${(i + 3) % files}`, i);
  if (files === 10) return;
  for (let i = 1; i < files; i++) rel(folder, `${folder}/0`, `${folder}/${i}`, i);
}

function addGroup(group: string, prefix: string, child: Child, rel: Rel): void {
  for (let f = 0; f < 20; f++) {
    const folder = child(`f:${prefix}:${f}`, group, "folder", f);
    addFolder(folder, child, rel);
    if (f > 0) rel(group, `f:${prefix}:${f - 1}`, folder, f);
  }
}

export function syntheticMap(): ZoomMap {
  const nodes: ZoomNode[] = [];
  const relations: ZoomRelation[] = [];
  const rel: Rel = (parent, a, b, i) => {
    relations.push({
      parent_id: parent,
      source_id: a,
      target_id: b,
      label: VERBS[i % VERBS.length]!,
      edge_count: 1 + ((i * 7) % 19),
      coupling: i % 5 === 0 ? "tight" : "loose",
    });
  };
  const root = node("root", null, "system", 0);
  nodes.push(root);
  const byId = new Map<string, ZoomNode>([["root", root]]);
  const child: Child = (id, parent, kind, rank) => {
    const n = node(id, parent, kind, rank);
    nodes.push(n);
    byId.get(parent)!.children.push(id);
    byId.set(id, n);
    return id;
  };

  for (let l = 0; l < 11; l++) {
    const layer = child(`l:${l}`, "root", "layer", l);
    for (let g = 0; g < 3; g++) {
      const group = child(`g:${l}:${g}`, layer, "group", g);
      addGroup(group, `${l}:${g}`, child, rel);
      if (g > 0) rel(layer, `g:${l}:${g - 1}`, group, g);
    }
    for (let k = 0; k < 3; k++) rel("root", layer, `l:${(l + k + 1) % 11}`, l + k);
  }

  return {
    root_id: "root",
    project_name: "synthetic",
    total_files: nodes.filter((n) => n.kind === "file").length,
    max_depth: 4,
    truncated: false,
    nodes,
    relations,
  };
}

/** A 2D context stand-in that records how often each method is called or property set. */
export function countingContext(): {
  ctx: CanvasRenderingContext2D;
  counts: Map<string, number>;
} {
  const counts = new Map<string, number>();
  const bump = (k: string): void => {
    counts.set(k, (counts.get(k) ?? 0) + 1);
  };
  const state: Record<string, unknown> = {};
  const ctx = new Proxy(state, {
    get(target, key: string) {
      if (key in target) return target[key];
      return (...args: unknown[]) => {
        bump(key);
        if (key === "measureText") return { width: String(args[0]).length * 7 };
        return undefined;
      };
    },
    set(target, key: string, value) {
      target[key] = value;
      bump(`set:${key}`);
      return true;
    },
  });
  return { ctx: ctx as unknown as CanvasRenderingContext2D, counts };
}

export const EXTERNAL_COUNT = 12;

/**
 * `map` plus external systems wired at every level: each folder's files import
 * a few of `EXTERNAL_COUNT` externals, and each group, layer and the root
 * carry the rolled-up child -> external links the backend serves. Every parent
 * then reaches more externals than a rail draws.
 */
export function withExternals(map: ZoomMap): ZoomMap {
  const kinds = ["db", "network", "service", "library"];
  const externals = Array.from({ length: EXTERNAL_COUNT }, (_, i) => ({
    id: `ext:pkg${i}`,
    name: `pkg${i}`,
    display_name: `Pkg${i}`,
    kind: kinds[i % kinds.length]!,
    category: "service",
    io_kind: null,
    ecosystem: "npm",
    file_count: 100 - i,
    edge_count: 200 - i,
  }));
  const external_relations: ZoomRelation[] = [];
  for (const n of map.nodes) {
    if (n.parent_id === null) continue;
    for (let k = 0; k < 3; k++) {
      external_relations.push({
        parent_id: n.parent_id,
        source_id: n.id,
        target_id: `ext:pkg${(n.sibling_rank + k * 5) % EXTERNAL_COUNT}`,
        label: "imports",
        edge_count: 1 + ((n.sibling_rank * 3 + k) % 9),
        coupling: k === 0 ? "tight" : "loose",
      });
    }
  }
  return { ...map, externals, external_relations };
}

export const TEST_LAYER = "l:10";

/**
 * `map` with every file under its last layer flagged `is_test`, which makes
 * that layer the test layer the layout sets aside (see `testLayerIds`).
 */
export function withTestLayer(map: ZoomMap): ZoomMap {
  return {
    ...map,
    nodes: map.nodes.map((n) => (n.kind === "file" && n.id.startsWith("f:10:") ? { ...n, is_test: true } : n)),
  };
}
