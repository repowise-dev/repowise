/**
 * Nested circle packing over the area hierarchy, and the files placed in it.
 *
 * Each unfolded area packs its drawn children, plus one slot for the files
 * directly in it, by lines of code (area is lines), then scales the pack into
 * its own disc. A disc's place depends only on its parent's children, so
 * unfolding a deeper level never moves anything already drawn: it fills a
 * disc that was solid a moment ago. That is what lets the hierarchy load
 * lazily without the field jumping under the reader.
 *
 * Pure.
 */

import { packEnclose, packSiblings } from "d3-hierarchy";
import { ROOT_AREA } from "../../lib/areas";
import type { AreaIndex } from "./areas";
import { directNloc, displayChildren, hostKey, hostOf, type FileHost } from "./area-paths";
import { WORLD, packCluster } from "./layout";
import type { CodeHealthMapFile, FileNode } from "./types";

export interface Circle {
  x: number;
  y: number;
  r: number;
}

export interface AreaGeometry {
  /** Every drawn area's disc, in world units. */
  circles: ReadonlyMap<string, Circle>;
  /** Each unfolded area's own-files slot, when it has files of its own. */
  slots: ReadonlyMap<string, Circle>;
}

/** The root disc: the world square, with a margin. */
export const ROOT_CIRCLE: Circle = { x: WORLD / 2, y: WORLD / 2, r: (WORLD / 2) * 0.96 };
/** How much of its parent a pack fills, so a ring reads around its children. */
const FILL = 0.9;
/** Air between sibling discs, as a share of the median sibling radius. */
const SIBLING_GAP = 0.08;
const SLOT = "\u0000slot";

interface Item {
  key: string;
  r: number;
}

/** Pack `items` into `parent`, biggest first. Returns each item's circle. */
function packInto(parent: Circle, items: Item[]): Map<string, Circle> {
  const out = new Map<string, Circle>();
  if (items.length === 0) return out;
  if (items.length === 1) {
    // A lone child is its parent's disc, so unfolding a chain never shrinks it.
    out.set(items[0]!.key, { ...parent });
    return out;
  }
  items.sort((a, b) => b.r - a.r || (a.key < b.key ? -1 : 1));
  const gap = (items[Math.floor(items.length / 2)]!.r || 1) * SIBLING_GAP;
  const placed = packSiblings(items.map((it) => ({ ...it, r: it.r + gap })));
  const enc = packEnclose(placed) ?? { x: 0, y: 0, r: 1 };
  const k = (parent.r * FILL) / Math.max(enc.r, 1e-9);
  for (const p of placed) {
    out.set(p.key, {
      x: parent.x + (p.x - enc.x) * k,
      y: parent.y + (p.y - enc.y) * k,
      r: (p.r - gap) * k,
    });
  }
  return out;
}

export function layoutAreas(index: AreaIndex): AreaGeometry {
  const circles = new Map<string, Circle>();
  const slots = new Map<string, Circle>();
  if (!index.areas.has(ROOT_AREA)) return { circles, slots };
  circles.set(ROOT_AREA, ROOT_CIRCLE);
  const queue = [ROOT_AREA];
  while (queue.length) {
    const path = queue.shift()!;
    const area = index.areas.get(path);
    const circle = circles.get(path);
    if (!area || !circle || !index.expanded.has(path)) continue;
    const kids = displayChildren(index, path);
    // An area with files and no sub-areas hosts them in its whole disc.
    if (kids.length === 0) {
      if (area.directFileCount > 0) slots.set(path, circle);
      continue;
    }
    const items: Item[] = kids.map((k) => ({
      key: k,
      r: Math.sqrt(Math.max(index.areas.get(k)?.nloc ?? 1, 1)),
    }));
    if (area.directFileCount > 0) {
      items.push({ key: SLOT, r: Math.sqrt(directNloc(index, area)) });
    }
    const packed = packInto(circle, items);
    for (const [key, c] of packed) {
      if (key === SLOT) slots.set(path, c);
      else {
        circles.set(key, c);
        queue.push(key);
      }
    }
  }
  return { circles, slots };
}

/** One disc's worth of files: what they sit in and which nodes they are. */
export interface Host {
  key: string;
  area: string;
  direct: boolean;
  circle: Circle;
  /** Indices into the scene's node list. */
  nodes: Int32Array;
  /** Mean dot radius in world units, for the "is a dot still visible" test. */
  meanR: number;
}

export interface PlacedFiles {
  nodes: FileNode[];
  hosts: Host[];
  /** Host key -> host index. */
  hostIndex: ReadonlyMap<string, number>;
  /** Node index -> host index. */
  hostOfNode: Int32Array;
  /** Files with nowhere to sit (no rollup covers their directory). */
  unplaced: number;
}

/** Largest dot, as a share of its host's radius, so two files are not plates. */
const MAX_DOT_SHARE = 0.32;

/**
 * Place every file in the disc that hosts it, packed by folder and scaled to
 * fit. Files of a host keep their relative sizes; across hosts the scale
 * follows each host's disc, as nested packs do.
 */
export function layoutFiles(
  files: readonly CodeHealthMapFile[],
  index: AreaIndex,
  geo: AreaGeometry,
): PlacedFiles {
  const groups = new Map<string, { host: FileHost; circle: Circle; files: CodeHealthMapFile[] }>();
  let unplaced = 0;
  for (const f of files) {
    if (f.nloc <= 0) continue;
    const host = hostOf(index, f.file_path);
    const circle = host ? (host.direct ? geo.slots.get(host.area) : geo.circles.get(host.area)) : undefined;
    if (!host || !circle) {
      unplaced += 1;
      continue;
    }
    const key = hostKey(host);
    const g = groups.get(key);
    if (g) g.files.push(f);
    else groups.set(key, { host, circle, files: [f] });
  }
  const nodes: FileNode[] = [];
  const owner: number[] = [];
  const hosts: Host[] = [];
  for (const [key, g] of [...groups.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))) {
    const { nodes: local, R } = packCluster(g.files);
    const maxR = local.reduce((m, n) => Math.max(m, n.r), 0);
    const k = Math.min((g.circle.r * 0.94) / Math.max(R, 1e-9), (g.circle.r * MAX_DOT_SHARE) / Math.max(maxR, 1e-9));
    const start = nodes.length;
    for (const n of local) {
      nodes.push({ file: n.file, x: g.circle.x + n.x * k, y: g.circle.y + n.y * k, r: n.r * k });
      owner.push(hosts.length);
    }
    hosts.push({
      key,
      area: g.host.area,
      direct: g.host.direct,
      circle: g.circle,
      nodes: Int32Array.from({ length: nodes.length - start }, (_, i) => start + i),
      meanR: (local.reduce((sum, n) => sum + n.r, 0) / Math.max(local.length, 1)) * k,
    });
  }
  const hostIndex = new Map(hosts.map((h, i) => [h.key, i]));
  return { nodes, hosts, hostIndex, hostOfNode: Int32Array.from(owner), unplaced };
}
