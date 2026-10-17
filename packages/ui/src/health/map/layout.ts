/**
 * Where every node sits, as one pure function of the rows.
 *
 * Deterministic on purpose. A reader builds spatial memory of this field, and
 * that memory is only worth having if the same repository lays out the same
 * way every time, under every lens. Nothing here reads a lens, and nothing
 * here reads the canvas size either: the field is laid out once into a fixed
 * world square and the camera fits that square to whatever box it is given,
 * so a resize moves the camera and never re-runs the layout.
 *
 * A node's area is its line count everywhere on the field, so two files of
 * the same size are the same size in any galaxy. The grouping adapts to the
 * repository instead: a repository with one module is grouped by sub-folder,
 * and one with hundreds folds its smallest modules into one galaxy per parent,
 * so the field always lands on a readable number of galaxies.
 */

import { packEnclose, packSiblings } from "d3-hierarchy";
import type { CodeHealthMapFile, FileNode, Galaxy, GalaxyAgg } from "./types";

/** Side of the world square the field is laid out in. */
export const WORLD = 1000;

/** Fewer galaxies than this and the field regroups by sub-folder. */
const MIN_GALAXIES = 4;
/** More than this and the smallest modules fold into one galaxy per parent. */
export const MAX_GALAXIES = 40;
/** Lines below which a file is drawn at the floor size, so it stays a dot. */
const NLOC_FLOOR = 4;
/** Air around each dot, in floor radii, so neighbours never touch. */
const DOT_GAP = 0.45;
/** Air around each folder cluster, in floor radii, so folders read apart. */
const FOLDER_GAP = 1.6;
/**
 * Largest node radius, in world units. At a fitted 720px canvas that is about
 * 17px, which keeps a five-file repository from drawing five dinner plates.
 */
const MAX_NODE_WORLD = 24;

const UNGROUPED = "(ungrouped)";
const ROOT = "(root)";
const OTHER = "Other modules";

const byPath = (a: CodeHealthMapFile, b: CodeHealthMapFile) =>
  b.nloc - a.nloc || (a.file_path < b.file_path ? -1 : 1);

/**
 * Group flat file rows into per-module galaxies, dropping zero-NLOC files
 * (they cannot be sized). Files come back sorted biggest-first.
 */
export function groupByModule(files: CodeHealthMapFile[]): GalaxyAgg[] {
  return aggregate(files, (f) => f.module ?? UNGROUPED);
}

function aggregate(
  files: CodeHealthMapFile[],
  keyOf: (f: CodeHealthMapFile) => string,
): GalaxyAgg[] {
  const byKey = new Map<string, CodeHealthMapFile[]>();
  for (const f of files) {
    if (f.nloc <= 0) continue;
    const key = keyOf(f);
    const arr = byKey.get(key);
    if (arr) arr.push(f);
    else byKey.set(key, [f]);
  }
  const out: GalaxyAgg[] = [];
  for (const [module, group] of byKey) {
    group.sort(byPath);
    out.push({
      module,
      files: group,
      totalNloc: group.reduce((s, f) => s + f.nloc, 0),
      maxNloc: group[0]?.nloc ?? 1,
    });
  }
  // Biggest galaxies first -> stable z-order (small ones paint on top).
  out.sort((a, b) => b.totalNloc - a.totalNloc || (a.module < b.module ? -1 : 1));
  return out;
}

/** Stable, well-spread hash of a module name. Kept for hosts that key on it. */
export function moduleColorId(module: string): number {
  let h = 0;
  for (let i = 0; i < module.length; i++) h = (h * 31 + module.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function dirOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i < 0 ? "" : path.slice(0, i);
}

function dirPrefix(dir: string, depth: number): string {
  if (!dir) return ROOT;
  return dir.split("/").slice(0, depth).join("/");
}

/**
 * How files group into galaxies: by module, or by sub-folder when the modules
 * are too few to separate anything.
 *
 * The folder depth is the shallowest one that yields a readable count, so a
 * repository whose one module is `src` groups by `src/<area>`. If no depth
 * lands in range the depth with the most groups wins, and if even the
 * shallowest useful depth overshoots the ceiling the folding below takes over.
 */
export function galaxyKeyFor(files: CodeHealthMapFile[]): (f: CodeHealthMapFile) => string {
  const byModule = (f: CodeHealthMapFile) => f.module ?? UNGROUPED;
  const modules = new Set(files.map(byModule));
  if (modules.size >= MIN_GALAXIES) return byModule;

  const dirs = files.map((f) => dirOf(f.file_path));
  const deepest = dirs.reduce((m, d) => Math.max(m, d ? d.split("/").length : 0), 0);
  let chosen = 0;
  let chosenCount = modules.size;
  for (let depth = 1; depth <= deepest; depth++) {
    const count = new Set(dirs.map((d) => dirPrefix(d, depth))).size;
    if (count > MAX_GALAXIES) {
      if (chosen === 0) chosen = depth;
      break;
    }
    if (count > chosenCount) {
      chosen = depth;
      chosenCount = count;
    }
    if (count >= MIN_GALAXIES) break;
  }
  if (chosen === 0) return byModule;
  const depth = chosen;
  return (f) => dirPrefix(dirOf(f.file_path), depth);
}

/** One galaxy before placement: its files and how many modules it stands for. */
interface Group {
  key: string;
  label: string;
  members: number;
  files: CodeHealthMapFile[];
}

function parentOf(key: string): string {
  const i = key.lastIndexOf("/");
  return i < 0 ? ROOT : key.slice(0, i);
}

/**
 * Bound the galaxy count. The biggest groups keep a galaxy of their own and
 * the rest fold into one galaxy per parent folder; if that still overshoots,
 * every folded group shares one. Two passes, so it always terminates.
 */
export function foldGroups(aggs: GalaxyAgg[]): Group[] {
  const groups: Group[] = aggs.map((a) => ({
    key: a.module,
    label: a.module,
    members: 1,
    files: a.files,
  }));
  if (groups.length <= MAX_GALAXIES) return groups;
  const bySize = [...groups].sort(
    (a, b) => b.files.length - a.files.length || (a.key < b.key ? -1 : 1),
  );
  const keep = bySize.slice(0, MAX_GALAXIES - 1);
  const rest = bySize.slice(MAX_GALAXIES - 1);
  const fold = (keyOf: (g: Group) => string): Group[] => {
    const buckets = new Map<string, Group>();
    for (const g of rest) {
      const key = keyOf(g);
      const bucket = buckets.get(key);
      if (bucket) {
        bucket.files = bucket.files.concat(g.files);
        bucket.members += g.members;
      } else {
        buckets.set(key, { key, label: key, members: g.members, files: [...g.files] });
      }
    }
    return [...keep, ...buckets.values()];
  };
  const byParent = fold((g) => `${parentOf(g.key)}/…`);
  const out = byParent.length <= MAX_GALAXIES ? byParent : fold(() => OTHER);
  for (const g of out) {
    if (g.members > 1) {
      g.label = g.key === OTHER ? `${g.members} smaller modules` : `${g.key} · ${g.members} modules`;
      g.files.sort(byPath);
    }
  }
  return out;
}

/** The nloc a radius is computed from: floored, and capped at the 98th percentile. */
function sizeCap(files: CodeHealthMapFile[]): number {
  const sizes = files.map((f) => f.nloc).sort((a, b) => a - b);
  return Math.max(NLOC_FLOOR, sizes[Math.floor((sizes.length - 1) * 0.98)] ?? NLOC_FLOOR);
}

/** A placed node relative to its galaxy's centre, before the field is scaled. */
interface LocalNode {
  file: CodeHealthMapFile;
  x: number;
  y: number;
  r: number;
}

/**
 * Pack one galaxy: files packed tight within each folder, biggest first, then
 * the folders packed together. Related files sit side by side, nothing
 * overlaps, and the galaxy's disc is the smallest circle that holds them.
 */
function packGalaxy(
  files: CodeHealthMapFile[],
  radius: (nloc: number) => number,
  floorR: number,
): { nodes: LocalNode[]; R: number } {
  const dotPad = floorR * DOT_GAP;
  const byDir = new Map<string, CodeHealthMapFile[]>();
  for (const f of files) {
    const d = dirOf(f.file_path);
    const arr = byDir.get(d);
    if (arr) arr.push(f);
    else byDir.set(d, [f]);
  }
  const clusters = [...byDir.entries()].map(([dir, group]) => {
    const dots = packSiblings(group.map((file) => ({ file, r: radius(file.nloc) + dotPad })));
    const enc = packEnclose(dots) ?? { x: 0, y: 0, r: dotPad };
    return { dir, dots, ex: enc.x, ey: enc.y, r: enc.r + (byDir.size > 1 ? floorR * FOLDER_GAP : 0) };
  });
  clusters.sort((a, b) => b.r - a.r || (a.dir < b.dir ? -1 : 1));
  const placed = packSiblings(clusters);
  const enc = packEnclose(placed) ?? { x: 0, y: 0, r: floorR };
  const nodes: LocalNode[] = [];
  for (const c of placed) {
    for (const d of c.dots) {
      nodes.push({
        file: d.file,
        x: c.x - enc.x + (d.x - c.ex),
        y: c.y - enc.y + (d.y - c.ey),
        r: d.r - dotPad,
      });
    }
  }
  return { nodes, R: enc.r + floorR };
}

/**
 * The whole field, in world units: galaxies placed by circle packing, and
 * files inside each packed by folder (see {@link packGalaxy}).
 *
 * Each file's radius is the square root of its lines, so area is lines.
 */
export function layoutField(files: CodeHealthMapFile[]): Galaxy[] {
  const drawable = files.filter((f) => f.nloc > 0);
  if (drawable.length === 0) return [];
  const groups = foldGroups(aggregate(drawable, galaxyKeyFor(drawable)));
  const cap = sizeCap(drawable);
  const radius = (nloc: number) => Math.sqrt(Math.min(cap, Math.max(NLOC_FLOOR, nloc)));
  const floorR = Math.sqrt(NLOC_FLOOR);

  const local = groups.map((g) => {
    const { nodes, R } = packGalaxy(g.files, radius, floorR);
    const maxNode = nodes.reduce((m, n) => Math.max(m, n.r), 0);
    return { g, nodes, R, maxNode };
  });

  // Pack biggest first so the largest galaxy anchors the middle.
  const order = local.map((_, i) => i).sort((a, b) => local[b]!.R - local[a]!.R || a - b);
  const largest = local[order[0]!]!.R;
  const gap = floorR * 3 + largest * 0.03;
  const circles = packSiblings(order.map((i) => ({ r: local[i]!.R + gap, i })));
  const enclose = packEnclose(circles) ?? { x: 0, y: 0, r: largest };

  const maxNode = local.reduce((m, l) => Math.max(m, l.maxNode), 0);
  const scale = Math.min(((WORLD / 2) * 0.96) / Math.max(enclose.r, 1e-9), MAX_NODE_WORLD / maxNode);
  const half = WORLD / 2;

  return circles.map<Galaxy>((c) => {
    const { g, nodes, R } = local[c.i]!;
    const cx = half + (c.x - enclose.x) * scale;
    const cy = half + (c.y - enclose.y) * scale;
    return {
      module: g.key,
      label: g.label,
      members: g.members,
      cx,
      cy,
      R: R * scale,
      fileCount: nodes.length,
      colorId: moduleColorId(g.key),
      nodes: nodes.map<FileNode>((n) => ({
        file: n.file,
        x: cx + n.x * scale,
        y: cy + n.y * scale,
        r: n.r * scale,
      })),
    };
  });
}

/**
 * The same field fitted into an `S` square, for a host that draws it itself.
 * Returns an empty field for an empty input rather than throwing, because a
 * host renders before its data lands.
 */
export function packGalaxies(files: CodeHealthMapFile[], S: number): Galaxy[] {
  if (S === 0) return [];
  const k = S / WORLD;
  return layoutField(files).map((g) => ({
    ...g,
    cx: g.cx * k,
    cy: g.cy * k,
    R: g.R * k,
    nodes: g.nodes.map((n) => ({ ...n, x: n.x * k, y: n.y * k, r: n.r * k })),
  }));
}

/** The camera that shows the whole world square in a `w` by `h` box. */
export function fitCamera(w: number, h: number): { cx: number; cy: number; scale: number } {
  return { cx: WORLD / 2, cy: WORLD / 2, scale: (Math.max(1, Math.min(w, h)) * 0.94) / WORLD };
}
