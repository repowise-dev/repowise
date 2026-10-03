/**
 * The turn lit on the map: each file Claude touched fills its whole tile in
 * the colour of its role, never per-pixel specks. A search's matches glow
 * faintly, files Repowise named take a deep plum, the edit's importers plum
 * (rippling out from the edit), opened files mist, and the edit or Claude's
 * current step amber. A lit file too small for a pixel still lights one full
 * cell where it sits, so it is never lost. Opened and edited files keep their
 * names, cut out of the tiles. Pure.
 */

import { cellSafe, fit } from "../format";
import { MAP_PALETTES, TERMINAL_DEFAULT, encodeCells, mix, type MapLayout, type MapPalette, type NameLabel, type PlacedFile } from "./map";

/** What the turn lit, as repo-relative paths. */
export interface Lit {
  hits: readonly string[];
  reads: readonly string[];
  named: readonly string[];
  importers: readonly string[];
  /** Every file the turn edited, oldest first; `edit` is the latest. */
  edits: readonly string[];
  edit: string | null;
  /** Claude's latest read or edit. */
  current: string | null;
}

export const NO_LIT: Lit = { hits: [], reads: [], named: [], importers: [], edits: [], edit: null, current: null };

/** The lit files as indices into `layout.files`, the names beside them, and the selection. */
export interface Overlay {
  hits: number[];
  reads: number[];
  named: number[];
  importers: number[];
  edit: number | null;
  current: number | null;
  names: NameLabel[];
  /** The inspector's selection, ringed. */
  selected: number | null;
}

/** One step of an animation, `t` from 0 to 1; t = 1 is the resting frame. */
export type Anim = { kind: "flash"; t: number } | { kind: "ripple"; t: number };

/** Animation lengths; frames go out every `FRAME_MS`, under the surface's 30 per second. */
export const FRAME_MS = 40;
export const FLASH_MS = 600;
export const RIPPLE_MS = 1_200;
const NAME_MAX = 18;
/** How bright a fresh search's glow starts, toward the opened-file mist. */
const FLASH_LIFT = 0.6;

/** A path's key in `layout.index`: lowercased where paths are case-insensitive. */
const keyOf = (layout: MapLayout, path: string): string => (layout.caseInsensitive ? path.toLowerCase() : path);

function indices(layout: MapLayout, paths: readonly string[], lowered = false): number[] {
  const out: number[] = [];
  for (const p of paths) {
    const i = lowered ? layout.lower.get(p) : layout.index.get(keyOf(layout, p));
    if (i !== undefined) out.push(i);
  }
  return out;
}

const one = (layout: MapLayout, path: string | null): number | null => (path === null ? null : (layout.index.get(keyOf(layout, path)) ?? null));

/** Lit paths outside the drawn feed (beyond its cap, or not indexed). */
export function notOnMap(layout: MapLayout, lit: Lit): number {
  const all = new Set([...lit.hits, ...lit.reads, ...lit.importers, ...(lit.edit === null ? [] : [lit.edit])].map((p) => keyOf(layout, p)));
  return [...all].filter((p) => !layout.index.has(p)).length;
}

/** The pixels a file lights: its own, or the one cell (two pixels) at its centre when it has none. */
export function litPixels(layout: MapLayout, f: PlacedFile): { x0: number; y0: number; x1: number; y1: number } {
  if (f.px1 > f.px0 && f.py1 > f.py0) return { x0: f.px0, y0: f.py0, x1: f.px1, y1: f.py1 };
  const x = Math.min(layout.width - 1, Math.max(0, Math.floor(f.rect.x + f.rect.w / 2)));
  const row = Math.min(layout.rows - 1, Math.max(0, Math.floor((f.rect.y + f.rect.h / 2) / 2)));
  return { x0: x, y0: row * 2, x1: x + 1, y1: row * 2 + 2 };
}

/** Rows a name may sit on, nearest first. */
const ROW_TRIES = [0, 1, -1, 2, -2];

const rowIn = (layout: MapLayout, row: number): boolean => row >= 0 && row < layout.rows;
const spanIn = (layout: MapLayout, col: number, len: number): boolean => col >= 0 && col + len <= layout.columns;
const inside = (layout: MapLayout, row: number, col: number, len: number): boolean => rowIn(layout, row) && spanIn(layout, col, len);

/** Whether `len` cells from `col` on `row` are inside the map and a cell clear of other names. */
function freeSpan(taken: ReadonlySet<number>, layout: MapLayout, at: { row: number; col: number }, len: number): boolean {
  if (!inside(layout, at.row, at.col, len)) return false;
  for (let c = at.col - 1; c <= at.col + len; c++) if (taken.has(at.row * layout.columns + c)) return false;
  return true;
}

/** A name beside its tile, right of it or else left, on the nearest free row; null when there is no room. */
function placeName(layout: MapLayout, taken: Set<number>, f: PlacedFile, color: number): NameLabel | null {
  const text = fit(cellSafe(f.path.slice(f.path.lastIndexOf("/") + 1)), NAME_MAX);
  const box = litPixels(layout, f);
  const mid = Math.floor((box.y0 + box.y1 - 1) / 4);
  const tries = ROW_TRIES.flatMap((dr) => [box.x1 + 1, box.x0 - text.length - 1].map((col) => ({ row: mid + dr, col })));
  const at = tries.find((t) => freeSpan(taken, layout, t, text.length));
  if (at === undefined) return null;
  for (let c = at.col; c < at.col + text.length; c++) taken.add(at.row * layout.columns + c);
  return { ...at, text, color };
}

/** Names for the edit and the current step (amber) first, then the opened files, newest first. */
function namesFor(layout: MapLayout, o: Pick<Overlay, "reads" | "edit" | "current">): NameLabel[] {
  const pal = MAP_PALETTES[layout.style.theme];
  const order = [...new Set([o.edit, o.current, ...[...o.reads].reverse()].filter((i): i is number => i !== null))];
  const taken = new Set<number>();
  const hot = new Set([o.edit, o.current]);
  return order
    .map((i) => placeName(layout, taken, layout.files[i] as PlacedFile, hot.has(i) ? pal.edit : TERMINAL_DEFAULT))
    .filter((n): n is NameLabel => n !== null);
}

/** The lit files on this layout, with their names placed; `selected` (a path) is ringed. */
export function resolveLit(layout: MapLayout, lit: Lit, selected: string | null = null): Overlay {
  const o = {
    hits: indices(layout, lit.hits),
    reads: indices(layout, lit.reads),
    // Flow keeps the files a reply named lowercased.
    named: indices(layout, lit.named, true),
    importers: indices(layout, lit.importers),
    edit: one(layout, lit.edit),
    current: one(layout, lit.current),
  };
  return { ...o, names: namesFor(layout, o), selected: one(layout, selected) };
}

function center(layout: MapLayout, f: PlacedFile): [number, number] {
  const b = litPixels(layout, f);
  return [Math.floor((b.x0 + b.x1 - 1) / 2), Math.floor((b.y0 + b.y1 - 1) / 2)];
}

function distance(layout: MapLayout, a: PlacedFile, b: PlacedFile): number {
  const [ax, ay] = center(layout, a);
  const [bx, by] = center(layout, b);
  return Math.hypot(ax - bx, ay - by);
}

/** How far the ripple travels: just past its farthest importer; 0 when there is nothing to ripple to. */
export function rippleRadius(layout: MapLayout, overlay: Overlay): number {
  const origin = overlay.edit === null ? undefined : layout.files[overlay.edit];
  if (!origin || overlay.importers.length === 0) return 0;
  return Math.max(...overlay.importers.map((i) => distance(layout, origin, layout.files[i] as PlacedFile))) + 3;
}

interface Paint {
  layout: MapLayout;
  px: Uint32Array;
  marked: Uint8Array;
}

function fillFile(p: Paint, f: PlacedFile, color: number): void {
  const { width } = p.layout;
  const b = litPixels(p.layout, f);
  for (let y = b.y0; y < b.y1; y++) {
    p.px.fill(color, y * width + b.x0, y * width + b.x1);
    p.marked.fill(1, y * width + b.x0, y * width + b.x1);
  }
}

function fillAll(p: Paint, files: readonly number[], color: number): void {
  for (const i of files) fillFile(p, p.layout.files[i] as PlacedFile, color);
}

/** The ripple's ring while it travels, in the importers' plum. */
function paintRing(p: Paint, origin: PlacedFile, reach: number, color: number): void {
  const [ox, oy] = center(p.layout, origin);
  const { width, height } = p.layout;
  for (let y = Math.max(0, Math.floor(oy - reach)); y <= Math.min(height - 1, oy + reach); y++) {
    for (let x = Math.max(0, Math.floor(ox - reach)); x <= Math.min(width - 1, ox + reach); x++) {
      const d = Math.hypot(x - ox, y - oy);
      if (d <= reach && d > reach - 2) p.px[y * width + x] = mix(p.px[y * width + x] as number, color, 0.5);
    }
  }
}

function hitColor(pal: MapPalette, anim: Anim | undefined): number {
  return anim?.kind === "flash" ? mix(pal.hit, pal.read, FLASH_LIFT * (1 - anim.t)) : pal.hit;
}

/** Importers the ripple has reached; all of them at rest. */
function reached(layout: MapLayout, overlay: Overlay, reach: number): number[] {
  const origin = overlay.edit === null ? undefined : layout.files[overlay.edit];
  if (origin === undefined || reach === Infinity) return overlay.importers;
  return overlay.importers.filter((i) => distance(layout, origin, layout.files[i] as PlacedFile) <= reach);
}

function rippleReach(layout: MapLayout, overlay: Overlay, anim: Anim | undefined): number {
  return anim?.kind === "ripple" && anim.t < 1 ? rippleRadius(layout, overlay) * anim.t : Infinity;
}

const inRange = (v: number, n: number): boolean => v >= 0 && v < n;

function set(p: Paint, x: number, y: number, color: number): void {
  if (!inRange(x, p.layout.width) || !inRange(y, p.layout.height)) return;
  p.px[y * p.layout.width + x] = color;
  p.marked[y * p.layout.width + x] = 1;
}

/** The pixels around a box (one outside it on every side). */
function around(box: { x0: number; y0: number; x1: number; y1: number }): [number, number][] {
  const out: [number, number][] = [];
  for (let x = box.x0 - 1; x <= box.x1; x++) out.push([x, box.y0 - 1], [x, box.y1]);
  for (let y = box.y0; y < box.y1; y++) out.push([box.x0 - 1, y], [box.x1, y]);
  return out;
}

/** The inspector's ring: the pixels just around the selected tile, in the theme's brightest neutral. */
function paintSelection(p: Paint, f: PlacedFile, color: number): void {
  for (const [x, y] of around(litPixels(p.layout, f))) set(p, x, y, color);
}

/** Map pixels for one frame: the base with the turn's roles filled in, weakest first so the strongest shows. */
export function framePixels(layout: MapLayout, overlay: Overlay, anim?: Anim): { pixels: Uint32Array; marked: Uint8Array } {
  const p: Paint = { layout, px: layout.base.slice(), marked: new Uint8Array(layout.width * layout.height) };
  const pal = MAP_PALETTES[layout.style.theme];
  const reach = rippleReach(layout, overlay, anim);
  fillAll(p, overlay.hits, hitColor(pal, anim));
  fillAll(p, overlay.named, pal.named);
  fillAll(p, reached(layout, overlay, reach), pal.importer);
  fillAll(p, overlay.reads, pal.read);
  fillAll(p, [overlay.edit, overlay.current].filter((i): i is number => i !== null), pal.edit);
  const origin = overlay.edit === null ? undefined : layout.files[overlay.edit];
  if (origin !== undefined && reach < Infinity) paintRing(p, origin, reach, pal.importer);
  const selected = overlay.selected === null ? undefined : layout.files[overlay.selected];
  if (selected !== undefined) paintSelection(p, selected, pal.ring);
  return { pixels: p.px, marked: p.marked };
}

/** One frame's cells: the resting frame without `anim`. */
export function frameCells(layout: MapLayout, overlay: Overlay, anim?: Anim): string {
  const { pixels, marked } = framePixels(layout, overlay, anim);
  return encodeCells(layout, pixels, marked, overlay.names);
}
