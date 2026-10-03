/**
 * The living map: a squarified treemap of the health-map feed, sized by lines
 * of code and grouped by folder, rasterized into terminal cells. Each cell
 * holds two map pixels as an upper half block (top pixel the glyph, bottom
 * pixel the background), so the pixel grid is `columns x rows * 2`, near square.
 *
 * Pure: the layout, its base pixels and the cell encoding. views/overlay.ts
 * paints Claude's marks on top; register.ts blits the animated frames.
 */

import { hierarchy, treemap, treemapSquarify, type HierarchyRectangularNode } from "d3-hierarchy";
import { bandForScore, type HealthFileMetric } from "@repowise-dev/types/health";
import { BRAND, DARK, DARK_CANVAS, DARK_CANVAS_BAND } from "@repowise-dev/ui/brand";

export type MapFile = Pick<HealthFileMetric, "file_path" | "score" | "nloc">;

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A file's place: float rect in pixels, and the whole pixels it owns (`px1`, `py1` exclusive). */
export interface PlacedFile {
  path: string;
  score: number | null;
  nloc: number;
  group: number;
  rect: Rect;
  px0: number;
  py0: number;
  px1: number;
  py1: number;
}

export interface MapGroup {
  key: string;
  rect: Rect;
  nloc: number;
}

export interface MapLabel {
  row: number;
  col: number;
  text: string;
}

export interface MapLayout {
  columns: number;
  rows: number;
  /** Pixel grid: `columns` across, `rows * 2` down. */
  width: number;
  height: number;
  groups: MapGroup[];
  /** Every file with lines, drawn or not. */
  files: PlacedFile[];
  /** Indices into `files` of those that own at least one pixel. */
  drawn: number[];
  /** Drawn file by repo-relative path (lowercase where paths are case-insensitive). */
  index: Map<string, number>;
  caseInsensitive: boolean;
  labels: MapLabel[];
  base: Uint32Array;
}

const UPPER_HALF = 0x2580;
const hex = (s: string): number => parseInt(s.slice(1), 16);
export const ACCENT = hex(BRAND.accent);
/** The canvas ground and gutters: painted, never the terminal's own background. */
export const GROUND = hex(DARK.bgRoot);
const LABEL_FG = hex(DARK.textSecondary);
const NEUTRAL = hex(DARK_CANVAS.nodeNeutral);
const BLACK = 0x000000;

/** A folder holding more than this share of the lines is split into its subfolders. */
const SPLIT_SHARE = 0.25;
const MAX_SPLITS = 8;
/** Groups at least this many pixels each way get a one-pixel gutter on their right and bottom. */
const GUTTER_MIN = 6;
/** Files at least this many pixels across (or down) get a darker last column (or row), so neighbours of one band part. */
const EDGE_MIN = 3;

/** The color a file's score paints; neutral when it has none. */
export function colorForScore(score: number | null): number {
  return score === null ? NEUTRAL : hex(DARK_CANVAS_BAND[bandForScore(score)]);
}

/** Linear blend of two 0xRRGGBB colors; `amount` 1 is all `b`. */
export function mix(a: number, b: number, amount: number): number {
  const ch = (shift: number) => {
    const x = (a >> shift) & 0xff;
    const y = (b >> shift) & 0xff;
    return Math.round(x + (y - x) * amount) << shift;
  };
  return (ch(16) | ch(8) | ch(0)) >>> 0;
}

const byName = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

function dirsOf(path: string): string[] {
  return path.split("/").slice(0, -1);
}

function keyOf(path: string, split: ReadonlySet<string>): string {
  const dirs = dirsOf(path);
  let depth = Math.min(1, dirs.length);
  while (depth < dirs.length && split.has(dirs.slice(0, depth).join("/"))) depth++;
  return dirs.slice(0, depth).join("/");
}

/** The heaviest group over the share that splitting would actually divide, or null. */
function nextSplit(files: readonly MapFile[], split: ReadonlySet<string>, limit: number): string | null {
  const weight = new Map<string, number>();
  for (const f of files) {
    const key = keyOf(f.file_path, split);
    weight.set(key, (weight.get(key) ?? 0) + f.nloc);
  }
  const heavy = [...weight].filter(([key, w]) => key !== "" && w > limit).sort((a, b) => b[1] - a[1] || byName(a[0], b[0]));
  for (const [key] of heavy) {
    const deeper = new Set([...split, key]);
    const parts = new Set(files.filter((f) => keyOf(f.file_path, split) === key).map((f) => keyOf(f.file_path, deeper)));
    if (parts.size > 1) return key;
  }
  return null;
}

/**
 * Each file's group: its top folder, or deeper where one folder would hold
 * most of the map. The server's `module` is the top folder alone, which on
 * Django puts nearly everything in `django` and `tests`; splitting here keeps
 * `django/db` and `django/contrib` apart.
 */
export function groupKeys(files: readonly MapFile[]): string[] {
  const split = new Set<string>();
  const limit = files.reduce((sum, f) => sum + f.nloc, 0) * SPLIT_SHARE;
  for (let i = 0; i < MAX_SPLITS; i++) {
    const key = nextSplit(files, split, limit);
    if (key === null) break;
    split.add(key);
  }
  return files.map((f) => keyOf(f.file_path, split));
}

interface Datum {
  name: string;
  file?: MapFile;
  children?: Datum[];
}

/** The two-level tree (groups, then files), laid out by d3's squarified treemap. */
function treemapOf(files: readonly MapFile[], width: number, height: number): HierarchyRectangularNode<Datum> {
  const keys = groupKeys(files);
  const groups = new Map<string, Datum[]>();
  files.forEach((file, i) => {
    const key = keys[i] ?? "";
    groups.set(key, [...(groups.get(key) ?? []), { name: file.file_path, file }]);
  });
  const data: Datum = { name: "", children: [...groups].map(([name, children]) => ({ name, children })) };
  const root = hierarchy(data)
    .sum((d) => d.file?.nloc ?? 0)
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0) || byName(a.data.name, b.data.name));
  return treemap<Datum>().tile(treemapSquarify).size([width, height])(root);
}

const rectOf = (n: HierarchyRectangularNode<Datum>): Rect => ({ x: n.x0, y: n.y0, w: n.x1 - n.x0, h: n.y1 - n.y0 });

/** Whole pixels whose centres fall in [from, to). */
function pixelSpan(from: number, to: number): [number, number] {
  return [Math.ceil(from - 0.5), Math.ceil(to - 0.5)];
}

/** The pixels a group's files may use: all of it, less a one-pixel gutter on inner edges of large groups. */
function groupInterior(rect: Rect, width: number, height: number): { x0: number; y0: number; x1: number; y1: number } {
  const [x0, x1] = pixelSpan(rect.x, rect.x + rect.w);
  const [y0, y1] = pixelSpan(rect.y, rect.y + rect.h);
  const gutter = x1 - x0 >= GUTTER_MIN && y1 - y0 >= GUTTER_MIN;
  return { x0, y0, x1: gutter && x1 < width ? x1 - 1 : x1, y1: gutter && y1 < height ? y1 - 1 : y1 };
}

function placeFile(leaf: HierarchyRectangularNode<Datum>, group: number, inner: ReturnType<typeof groupInterior>): PlacedFile {
  const file = leaf.data.file as MapFile;
  const rect = rectOf(leaf);
  const [x0, x1] = pixelSpan(rect.x, rect.x + rect.w);
  const [y0, y1] = pixelSpan(rect.y, rect.y + rect.h);
  return {
    path: file.file_path,
    score: file.score,
    nloc: file.nloc,
    group,
    rect,
    px0: Math.max(x0, inner.x0),
    py0: Math.max(y0, inner.y0),
    px1: Math.min(x1, inner.x1),
    py1: Math.min(y1, inner.y1),
  };
}

function paintFile(base: Uint32Array, width: number, f: PlacedFile): void {
  const fill = colorForScore(f.score);
  const edge = mix(fill, BLACK, 0.3);
  const edgeX = f.px1 - f.px0 >= EDGE_MIN ? f.px1 - 1 : -1;
  const edgeY = f.py1 - f.py0 >= EDGE_MIN ? f.py1 - 1 : -1;
  for (let y = f.py0; y < f.py1; y++) {
    base.fill(y === edgeY ? edge : fill, y * width + f.px0, y * width + f.px1);
    if (edgeX >= 0) base[y * width + edgeX] = edge;
  }
}

/** A label for a group `width` cells wide: the folder, its last two segments, or its last; printable ASCII only. */
export function labelText(key: string, width: number): string | null {
  const parts = key.split("/");
  const rungs = [key, parts.slice(-2).join("/"), parts[parts.length - 1] ?? ""];
  const text = rungs.find((r) => r.length > 0 && r.length <= width);
  return text !== undefined && /^[\x20-\x7e]+$/.test(text) ? text : null;
}

function labelFor(g: MapGroup, columns: number, rows: number): MapLabel | null {
  const col = Math.ceil(g.rect.x);
  const row = Math.ceil(g.rect.y / 2);
  const width = Math.min(columns, Math.floor(g.rect.x + g.rect.w)) - col - 1;
  const height = Math.floor((g.rect.y + g.rect.h) / 2) - row;
  if (g.key === "" || height < 2 || row >= rows) return null;
  const text = labelText(g.key, width);
  return text === null ? null : { row, col, text };
}

/** Group labels, largest groups first, where the group is wide and tall enough. */
function labelsFor(groups: readonly MapGroup[], columns: number, rows: number): MapLabel[] {
  return [...groups]
    .sort((a, b) => b.nloc - a.nloc)
    .map((g) => labelFor(g, columns, rows))
    .filter((l): l is MapLabel => l !== null);
}

/**
 * Lays the feed out on a `columns x rows` cell canvas. Deterministic: ties
 * break on path. Files too small for a whole pixel at this size are placed but
 * not drawn; the legend counts only what is drawn.
 */
export function layoutMap(feed: readonly MapFile[], columns: number, rows: number, caseInsensitive: boolean): MapLayout {
  const width = columns;
  const height = rows * 2;
  const root = treemapOf(
    feed.filter((f) => f.nloc > 0),
    width,
    height,
  );
  const groups: MapGroup[] = [];
  const files: PlacedFile[] = [];
  for (const node of root.children ?? []) {
    const group = groups.push({ key: node.data.name, rect: rectOf(node), nloc: node.value ?? 0 }) - 1;
    const inner = groupInterior(rectOf(node), width, height);
    for (const leaf of node.children ?? []) files.push(placeFile(leaf, group, inner));
  }
  const base = new Uint32Array(width * height).fill(GROUND);
  const drawn: number[] = [];
  const index = new Map<string, number>();
  files.forEach((f, i) => {
    if (f.px1 <= f.px0 || f.py1 <= f.py0) return;
    drawn.push(i);
    index.set(caseInsensitive ? f.path.toLowerCase() : f.path, i);
    paintFile(base, width, f);
  });
  return { columns, rows, width, height, groups, files, drawn, index, caseInsensitive, labels: labelsFor(groups, columns, rows), base };
}

/** True when a mark covers either pixel of any of the label's cells: then the whole label gives way. */
function labelCovered(label: MapLabel, marked: Uint8Array | undefined, width: number): boolean {
  if (marked === undefined) return false;
  for (let c = label.col; c < label.col + label.text.length; c++) {
    if (marked[2 * label.row * width + c] || marked[(2 * label.row + 1) * width + c]) return true;
  }
  return false;
}

/**
 * Packs pixels into the Raster's `cells`: base64 of little-endian u32 triplets
 * `[codePoint, fg, bg]`, two pixels per cell. Labels sit on cells no mark
 * covers. Uint32Array is little-endian on every platform Claude Code runs on.
 */
export function encodeCells(layout: MapLayout, pixels: Uint32Array, marked?: Uint8Array): string {
  const { columns, rows, width } = layout;
  const words = new Uint32Array(columns * rows * 3);
  for (let i = 0; i < columns * rows; i++) {
    const r = Math.floor(i / columns);
    const c = i % columns;
    words[i * 3] = UPPER_HALF;
    words[i * 3 + 1] = pixels[2 * r * width + c] ?? GROUND;
    words[i * 3 + 2] = pixels[(2 * r + 1) * width + c] ?? GROUND;
  }
  for (const label of layout.labels) {
    if (labelCovered(label, marked, width)) continue;
    for (let k = 0; k < label.text.length && label.col + k < columns; k++) {
      const o = (label.row * columns + label.col + k) * 3;
      words.set([label.text.charCodeAt(k), LABEL_FG, GROUND], o);
    }
  }
  return (new Uint8Array(words.buffer) as Uint8Array & { toBase64(): string }).toBase64();
}
