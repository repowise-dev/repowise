/**
 * Search over everything on the zoom map: layers, groups, folders, files, and
 * (when the server sends them) containers and external systems. Pure,
 * browser-free.
 *
 * `buildSearchIndex` runs once per map and precomputes every lower-cased
 * string and word start, so `searchIndex` is one linear pass of `startsWith` /
 * `includes` with a bounded top-K insert: no per-query allocation per entry
 * and no sort of the whole match list (a two-letter query matches thousands).
 *
 * Ranking, best first: exact name, name prefix, a word in the name starting
 * with the query ("health" finds `code_health.py` and "Code Health"), name
 * substring, then path substring. Within a tier a coarser kind wins (a layer
 * named "core" before a file named `core.py`), then the shorter path.
 */

import type { ZoomKind, ZoomMap, ZoomNode, ZoomRelation } from "./types";

export type SearchKind = ZoomKind | "container" | "external";

export interface SearchEntry {
  /** The map node the camera flies to (a container's or external's main card). */
  targetId: string;
  /** Unique per entry: the node id, or the container / external id. */
  key: string;
  kind: SearchKind;
  name: string;
  /** Ancestor names, coarsest first; the root and repeated names left out. */
  crumbs: string[];
  path: string;
  // Precomputed for the scan.
  lname: string;
  lpath: string;
  words: string[];
  kindRank: number;
}

export interface SearchIndex {
  entries: SearchEntry[];
}

/** Coarser kinds rank first within a match tier. */
const KIND_RANK: Record<SearchKind, number> = {
  system: 0,
  container: 1,
  layer: 2,
  external: 3,
  group: 4,
  folder: 5,
  file: 6,
};

const TIER_EXACT = 0;
const TIER_PREFIX = 1;
const TIER_WORD = 2;
const TIER_NAME = 3;
const TIER_PATH = 4;

/**
 * Lower-cased words of a name, split on separators and camelCase humps:
 * `zoomDetail-panel.tsx` -> zoom, detail, panel, tsx.
 */
export function nameWords(name: string): string[] {
  return name
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

function entry(
  targetId: string,
  key: string,
  kind: SearchKind,
  name: string,
  crumbs: string[],
  path: string,
): SearchEntry {
  return {
    targetId,
    key,
    kind,
    name,
    crumbs,
    path,
    lname: name.toLowerCase(),
    lpath: path.toLowerCase(),
    words: nameWords(name),
    kindRank: KIND_RANK[kind],
  };
}

/** Ancestor-name chains, built top-down so each node's chain is its parent's plus one. */
function crumbsByNode(map: ZoomMap, byId: Map<string, ZoomNode>): Map<string, string[]> {
  const out = new Map<string, string[]>();
  const resolve = (n: ZoomNode): string[] => {
    const hit = out.get(n.id);
    if (hit) return hit;
    const parent = n.parent_id ? byId.get(n.parent_id) : undefined;
    // The system root names the whole repository; repeating it on every row is
    // noise, as is a folder repeating the group it sits in ("tests / tests").
    const above = !parent || !parent.parent_id ? [] : resolve(parent);
    const chain =
      !parent || !parent.parent_id || above[above.length - 1] === parent.name
        ? above
        : [...above, parent.name];
    out.set(n.id, chain);
    return chain;
  };
  for (const n of map.nodes) resolve(n);
  return out;
}

/**
 * Each container's main card: the coarsest node lying wholly inside it, the one
 * holding most files on a tie.
 */
function containerCards(map: ZoomMap): Map<string, ZoomNode> {
  const best = new Map<string, ZoomNode>();
  for (const n of map.nodes) {
    const ids = n.container_ids;
    if (!ids || ids.length !== 1) continue;
    const cur = best.get(ids[0]!);
    if (
      !cur ||
      n.level < cur.level ||
      (n.level === cur.level && n.metrics.file_count > cur.metrics.file_count)
    ) {
      best.set(ids[0]!, n);
    }
  }
  return best;
}

/**
 * An external's main card: follow its heaviest import down the tree, from the
 * root's strongest child to that child's strongest child, and stop at a
 * folder. Counts aggregate upward, so "the box with the most edges" is always
 * a layer; the descent lands on the folder that actually does the importing.
 */
function externalCards(map: ZoomMap, byId: Map<string, ZoomNode>): Map<string, ZoomNode> {
  // external id -> parent id -> strongest relation under that parent
  const strongest = new Map<string, Map<string, ZoomRelation>>();
  for (const r of map.external_relations ?? []) {
    let byParent = strongest.get(r.target_id);
    if (!byParent) strongest.set(r.target_id, (byParent = new Map()));
    const cur = byParent.get(r.parent_id);
    if (!cur || r.edge_count > cur.edge_count) byParent.set(r.parent_id, r);
  }
  const out = new Map<string, ZoomNode>();
  for (const [extId, byParent] of strongest) {
    const card = descend(byParent, map.root_id, byId);
    if (card) out.set(extId, card);
  }
  return out;
}

/** Walk the strongest relation per parent down from `rootId`, stopping at a folder. */
function descend(
  byParent: Map<string, ZoomRelation>,
  rootId: string,
  byId: Map<string, ZoomNode>,
): ZoomNode | undefined {
  let card: ZoomNode | undefined;
  for (let r = byParent.get(rootId); r; r = byParent.get(card.id)) {
    const node = byId.get(r.source_id);
    if (!node) break;
    card = node;
    if (node.kind === "folder" || node.kind === "file") break;
  }
  return card;
}

/** Index every searchable thing on the map. Run once per map. */
export function buildSearchIndex(map: ZoomMap): SearchIndex {
  const byId = new Map(map.nodes.map((n) => [n.id, n] as const));
  const crumbs = crumbsByNode(map, byId);
  const entries: SearchEntry[] = [];
  for (const n of map.nodes) {
    if (n.kind === "system") continue;
    entries.push(entry(n.id, n.id, n.kind, n.name, crumbs.get(n.id) ?? [], n.path));
  }
  const containerBest = containerCards(map);
  for (const c of map.containers ?? []) {
    const card = containerBest.get(c.id);
    if (!card) continue;
    entries.push(entry(card.id, c.id, "container", c.name, [], c.path));
  }
  const extCards = externalCards(map, byId);
  for (const x of map.externals ?? []) {
    const card = extCards.get(x.id);
    if (!card) continue;
    // Named by where it is used most, which is where the search lands.
    entries.push(entry(card.id, x.id, "external", x.display_name || x.name, [card.name], ""));
  }
  return { entries };
}

function tier(e: SearchEntry, q: string): number {
  if (e.lname === q) return TIER_EXACT;
  if (e.lname.startsWith(q)) return TIER_PREFIX;
  for (const w of e.words) if (w.startsWith(q)) return TIER_WORD;
  if (e.lname.includes(q)) return TIER_NAME;
  if (e.lpath.includes(q)) return TIER_PATH;
  return -1;
}

/** Lower is better: tier, then kind, then path length. */
function score(e: SearchEntry, t: number): number {
  return t * 1e7 + e.kindRank * 1e5 + Math.min(e.lpath.length, 99_999);
}

/**
 * The best `limit` entries for `query` (trimmed, case-insensitive). Queries
 * shorter than two characters match nothing: one letter hits most of the map.
 */
export function searchIndex(index: SearchIndex, query: string, limit = 10): SearchEntry[] {
  const q = query.trim().toLowerCase();
  if (q.length < 2 || limit <= 0) return [];
  // Bounded insertion into a sorted top-K: O(n * K) worst case, K is small.
  const top: { e: SearchEntry; s: number }[] = [];
  for (const e of index.entries) {
    const t = tier(e, q);
    if (t < 0) continue;
    const s = score(e, t);
    if (top.length === limit && s >= top[limit - 1]!.s) continue;
    let i = top.length;
    while (i > 0 && top[i - 1]!.s > s) i--;
    top.splice(i, 0, { e, s });
    if (top.length > limit) top.pop();
  }
  return top.map((x) => x.e);
}
