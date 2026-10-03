/**
 * The map: a squarified treemap of the health-map feed, sized by lines of
 * code and grouped by folder, rasterized into terminal cells. Each cell holds
 * two map pixels as a half block, so the pixel grid is `columns x rows * 2`,
 * near square.
 *
 * By default a ghost map: every file a quiet tile in two near-ground tones of
 * the theme's ramp, so the turn's marks (views/overlay.ts) are the only
 * colour. Health colours are a toggle. Where files are too many for a pixel
 * each, folders are drawn as tiles instead of per-file shimmer. Empty ground
 * is the terminal's own background. Pure.
 */

import { hierarchy, treemap, treemapSquarify, type HierarchyRectangularNode } from "d3-hierarchy";
import { bandForScore, type HealthFileMetric } from "@repowise-dev/types/health";
import { BRAND, DARK, DARK_CANVAS, DARK_CANVAS_BAND, LIGHT } from "@repowise-dev/ui/brand";
import type { ThemeName } from "./theme";

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

/** A name written on the map in its own color, on the terminal's background. */
export interface NameLabel extends MapLabel {
  color: number;
}

/** How the map is drawn: the theme's ramp, and health colours on or off. */
export interface MapStyle {
  theme: ThemeName;
  health: boolean;
}

/** The ghost tiles, folder names, and the turn's marks, per theme. */
export interface MapPalette {
  tileA: number;
  tileB: number;
  folder: number;
  hit: number;
  read: number;
  named: number;
  importer: number;
  edit: number;
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
  /** Every placed file by repo-relative path (lowercased where paths are case-insensitive), drawn or not: a lit one too small for a pixel still lights a cell. */
  index: Map<string, number>;
  /** The same, always lowercased: for paths that arrive lowercased (the files a Repowise reply named). */
  lower: Map<string, number>;
  caseInsensitive: boolean;
  labels: MapLabel[];
  base: Uint32Array;
  style: MapStyle;
  /** Too many files for a pixel each: folders are drawn as tiles. */
  dense: boolean;
  /** Every folder below the groups, parents first, as painted. */
  folders: FolderTile[];
}

const UPPER_HALF = 0x2580;
const LOWER_HALF = 0x2584;
const SPACE = 0x20;
const hex = (s: string): number => parseInt(s.slice(1), 16);
/**
 * The empty ground between tiles, as a pixel value: overlay marks blend with
 * it as a fixed dark, and the cell encoding draws it as the terminal's own
 * background, so the map blends into the pane.
 */
export const GROUND = hex(DARK.bgRoot);
/** A Raster color: the terminal's own default (foreground or background). */
export const TERMINAL_DEFAULT = 0x01000000;
export const LABEL_FG = hex(DARK.textSecondary);
const NEUTRAL = hex(DARK_CANVAS.nodeNeutral);
const BLACK = 0x000000;

/**
 * Measured on black and on the dock's ~#1f1f1f: dark tiles 1.4 to 1.6:1 (a
 * quiet plan), marks 2.4:1 (a search's glow) to 9:1 (the edit); on white,
 * light tiles 1.3 to 1.4:1 and marks 1.8 to 8.6:1.
 */
export const MAP_PALETTES: Record<ThemeName, MapPalette> = {
  dark: {
    tileA: mix(hex(DARK.bgElevated), hex(DARK.textTertiary), 0.12),
    tileB: mix(hex(DARK.bgElevated), hex(DARK.textTertiary), 0.22),
    folder: hex(DARK.textTertiary),
    hit: mix(hex(DARK.bgElevated), hex(DARK.textSecondary), 0.32),
    read: hex(DARK.textSecondary),
    named: mix(hex(DARK.accentSecondary), hex(DARK.bgRoot), 0.45),
    importer: hex(DARK.accentSecondary),
    edit: hex(BRAND.accent),
  },
  light: {
    tileA: mix(hex(LIGHT.bgInset), hex(LIGHT.textTertiary), 0.08),
    tileB: mix(hex(LIGHT.bgInset), hex(LIGHT.textTertiary), 0.16),
    folder: hex(LIGHT.textTertiary),
    hit: mix(hex(LIGHT.bgInset), hex(LIGHT.textSecondary), 0.3),
    read: hex(LIGHT.textSecondary),
    named: mix(hex(LIGHT.accentSecondary), hex(LIGHT.bgRoot), 0.55),
    importer: hex(LIGHT.accentSecondary),
    edit: hex(BRAND.accentTextLight),
  },
};

export const DEFAULT_STYLE: MapStyle = { theme: "dark", health: false };
/** Fewer files than this share own a pixel: the map draws folders, not files. */
const DENSE_SHARE = 0.6;

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
  /** Subfolders by name, while the tree is built. */
  kids?: Map<string, Datum>;
}

const folderDatum = (name: string): Datum => ({ name, children: [], kids: new Map() });

/** Files the group holds, under nested folders from the group's own folder down. */
function insertFile(group: Datum, key: string, file: MapFile): void {
  const rel = key === "" ? file.file_path : file.file_path.slice(key.length + 1);
  let node = group;
  for (const dir of rel.split("/").slice(0, -1)) {
    let next = node.kids?.get(dir);
    if (next === undefined) {
      next = folderDatum(dir);
      node.kids?.set(dir, next);
      node.children?.push(next);
    }
    node = next;
  }
  node.children?.push({ name: file.file_path, file });
}

/**
 * The tree (groups, then each folder inside them, then files), laid out by
 * d3's squarified treemap, so every folder is one rectangle.
 */
function treemapOf(files: readonly MapFile[], width: number, height: number): HierarchyRectangularNode<Datum> {
  const keys = groupKeys(files);
  const groups = new Map<string, Datum>();
  files.forEach((file, i) => {
    const key = keys[i] ?? "";
    const group = groups.get(key) ?? folderDatum(key);
    groups.set(key, group);
    insertFile(group, key, file);
  });
  const data: Datum = { name: "", children: [...groups.values()] };
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

/** A file's tile: its health band, or one of the two ghost tones, alternating. */
function tileFor(f: PlacedFile, i: number, style: MapStyle): number {
  const pal = MAP_PALETTES[style.theme];
  if (style.health) return colorForScore(f.score);
  return i % 2 === 0 ? pal.tileA : pal.tileB;
}

/** A rectangle of pixels in one ghost tone. */
function paintBox(base: Uint32Array, width: number, box: { x0: number; y0: number; x1: number; y1: number }, fill: number): void {
  for (let y = box.y0; y < box.y1; y++) base.fill(fill, y * width + box.x0, y * width + box.x1);
}

/** A folder inside a group: its pixels (within the group's interior) and its tone, alternating among its siblings. */
export interface FolderTile {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  tone: 0 | 1;
}

/** Every folder below the groups, parents before children, so the deepest folder big enough to show wins. */
function folderTiles(group: HierarchyRectangularNode<Datum>, inner: ReturnType<typeof groupInterior>): FolderTile[] {
  const out: FolderTile[] = [];
  for (const node of group.descendants()) {
    if (node === group || node.children === undefined) continue;
    const tone = ((node.parent?.children ?? []).indexOf(node) % 2) as 0 | 1;
    const [x0, x1] = pixelSpan(node.x0, node.x1);
    const [y0, y1] = pixelSpan(node.y0, node.y1);
    out.push({ x0: Math.max(x0, inner.x0), y0: Math.max(y0, inner.y0), x1: Math.min(x1, inner.x1), y1: Math.min(y1, inner.y1), tone });
  }
  return out;
}

function paintFile(base: Uint32Array, width: number, f: PlacedFile, fill: number): void {
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

/** Folder names only where a group is big enough to read as a region, and at most a few: never noisy. */
const LABEL_MIN_COLS = 12;
const LABEL_MIN_ROWS = 3;
const MAX_LABELS = 8;

function labelFor(g: MapGroup, columns: number, rows: number): MapLabel | null {
  const col = Math.ceil(g.rect.x);
  const row = Math.ceil(g.rect.y / 2);
  const width = Math.min(columns, Math.floor(g.rect.x + g.rect.w)) - col - 1;
  const height = Math.floor((g.rect.y + g.rect.h) / 2) - row;
  const small = height < LABEL_MIN_ROWS || width < LABEL_MIN_COLS;
  if (g.key === "" || small || row >= rows) return null;
  const text = labelText(g.key, width);
  return text === null ? null : { row, col, text };
}

/** Group labels, largest groups first, where the group is wide and tall enough. */
function labelsFor(groups: readonly MapGroup[], columns: number, rows: number): MapLabel[] {
  return [...groups]
    .sort((a, b) => b.nloc - a.nloc)
    .map((g) => labelFor(g, columns, rows))
    .filter((l): l is MapLabel => l !== null)
    .slice(0, MAX_LABELS);
}

/**
 * The base pixels: ghost folder tones under everything (no holes where files
 * are too small), then each file's tile, unless the map is dense, where the
 * folder tones alone are drawn.
 */
function paintBase(
  map: { width: number; height: number; groups: MapGroup[]; folders: FolderTile[]; files: PlacedFile[]; drawn: number[] },
  style: MapStyle,
  dense: boolean,
): Uint32Array {
  const { width, height } = map;
  const base = new Uint32Array(width * height).fill(GROUND);
  const pal = MAP_PALETTES[style.theme];
  const tone = (t: number) => (t === 0 ? pal.tileB : pal.tileA);
  if (!style.health) map.groups.forEach((g, i) => paintBox(base, width, groupInterior(g.rect, width, height), tone(i % 2)));
  if (!style.health) for (const f of map.folders) paintBox(base, width, f, tone(1 - f.tone));
  if (dense && !style.health) return base;
  for (const i of map.drawn) paintFile(base, width, map.files[i] as PlacedFile, tileFor(map.files[i] as PlacedFile, i, style));
  return base;
}

/**
 * Lays the feed out on a `columns x rows` cell canvas. Deterministic: ties
 * break on path. Files too small for a whole pixel at this size are placed but
 * not drawn; the scope line counts only what is drawn.
 */
/** The canvas a layout is for: its size in cells, how paths compare, and how it is drawn. */
export interface MapCanvas {
  columns: number;
  rows: number;
  caseInsensitive: boolean;
  style?: MapStyle;
}

export function layoutMap(feed: readonly MapFile[], canvas: MapCanvas): MapLayout {
  const { columns, rows, caseInsensitive } = canvas;
  const style = canvas.style ?? DEFAULT_STYLE;
  const width = columns;
  const height = rows * 2;
  const root = treemapOf(
    feed.filter((f) => f.nloc > 0),
    width,
    height,
  );
  const groups: MapGroup[] = [];
  const files: PlacedFile[] = [];
  const folders: FolderTile[] = [];
  for (const node of root.children ?? []) {
    const group = groups.push({ key: node.data.name, rect: rectOf(node), nloc: node.value ?? 0 }) - 1;
    const inner = groupInterior(rectOf(node), width, height);
    for (const leaf of node.leaves()) files.push(placeFile(leaf, group, inner));
    folders.push(...folderTiles(node, inner));
  }
  const drawn: number[] = [];
  const index = new Map<string, number>();
  const lower = new Map<string, number>();
  files.forEach((f, i) => {
    index.set(caseInsensitive ? f.path.toLowerCase() : f.path, i);
    lower.set(f.path.toLowerCase(), i);
    if (f.px1 > f.px0 && f.py1 > f.py0) drawn.push(i);
  });
  const dense = drawn.length < files.length * DENSE_SHARE;
  const base = paintBase({ width, height, groups, folders, files, drawn }, style, dense);
  return { columns, rows, width, height, groups, files, drawn, index, caseInsensitive, labels: labelsFor(groups, columns, rows), base, style, dense, lower, folders };
}

/**
 * One cell holding a top and a bottom pixel. A ground half is the terminal's
 * own background: the other half drawn as the glyph (upper or lower half block).
 */
export function halfCell(top: number, bottom: number): [number, number, number] {
  if (top === GROUND && bottom === GROUND) return [SPACE, TERMINAL_DEFAULT, TERMINAL_DEFAULT];
  if (top === GROUND) return [LOWER_HALF, bottom, TERMINAL_DEFAULT];
  return [UPPER_HALF, top, bottom === GROUND ? TERMINAL_DEFAULT : bottom];
}

/** True when a mark covers either pixel of any of the label's cells: then the whole label gives way. */
function labelCovered(label: MapLabel, marked: Uint8Array | undefined, width: number): boolean {
  if (marked === undefined) return false;
  for (let c = label.col; c < label.col + label.text.length; c++) {
    if (marked[2 * label.row * width + c] || marked[(2 * label.row + 1) * width + c]) return true;
  }
  return false;
}

/** The background a label's cell keeps: the tile under it, or the terminal's own. */
const cellGround = (pixel: number | undefined): number => (pixel === undefined || pixel === GROUND ? TERMINAL_DEFAULT : pixel);

function writeText(words: Uint32Array, layout: MapLayout, label: MapLabel, colors: (col: number) => [number, number]): void {
  for (let k = 0; k < label.text.length && label.col + k < layout.columns; k++) {
    const col = label.col + k;
    words.set([label.text.charCodeAt(k), ...colors(col)], (label.row * layout.columns + col) * 3);
  }
}

/** Folder names, faint, on the tile they name; a mark under any of a name's cells hides the whole name. */
function writeFolderLabels(words: Uint32Array, layout: MapLayout, pixels: Uint32Array, marked: Uint8Array | undefined): void {
  const fg = layout.style.health ? LABEL_FG : MAP_PALETTES[layout.style.theme].folder;
  for (const label of layout.labels) {
    if (labelCovered(label, marked, layout.width)) continue;
    writeText(words, layout, label, (col) => [fg, cellGround(pixels[2 * label.row * layout.width + col])]);
  }
}

/**
 * Packs pixels into the Raster's `cells`: base64 of little-endian u32 triplets
 * `[codePoint, fg, bg]`, two pixels per cell. Folder names sit on cells no
 * mark covers; the names of lit files are cut out of the tiles onto the
 * terminal's background. Uint32Array is little-endian on every platform
 * Claude Code runs on.
 */
export function encodeCells(layout: MapLayout, pixels: Uint32Array, marked?: Uint8Array, names: readonly NameLabel[] = []): string {
  const { columns, rows, width } = layout;
  const words = new Uint32Array(columns * rows * 3);
  for (let i = 0; i < columns * rows; i++) {
    const r = Math.floor(i / columns);
    const c = i % columns;
    words.set(halfCell(pixels[2 * r * width + c] ?? GROUND, pixels[(2 * r + 1) * width + c] ?? GROUND), i * 3);
  }
  writeFolderLabels(words, layout, pixels, marked);
  for (const name of names) writeText(words, layout, name, () => [name.color, TERMINAL_DEFAULT]);
  return (new Uint8Array(words.buffer) as Uint8Array & { toBase64(): string }).toBase64();
}
