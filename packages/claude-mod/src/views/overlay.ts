/**
 * Claude's marks on the map, as pixel layers over the base: the dotted path
 * joining reads, search matches (a flash that settles to a faint tint), read
 * outlines, the edited file, and the ripple with its importers. Pure.
 *
 * Ceiling: a mark reads as its own shape only on files of at least 3x3 pixels;
 * smaller files show a mark as a solid orange (read) or pale orange (edited) dot.
 */

import { relativeTo } from "../model/events";
import type { Callers, TrailState } from "../model/trail";
import { ACCENT, GROUND, encodeCells, mix, type MapLayout, type PlacedFile } from "./map";

/** What the trail and the ripple mark, as indices into `layout.files`. */
export interface Overlay {
  reads: number[];
  hits: number[];
  edited: number | null;
  callers: number[];
}

/** The overlay plus the counts the legend states beside it. */
export interface ResolvedOverlay {
  overlay: Overlay;
  reads: { count: number; capped: boolean; notDrawn: number };
  /** Files the latest search matched, drawn or not; null before any search. */
  matched: number | null;
  /** null while no edit has been seen. */
  edit: { name: string; callers: Callers | null; notDrawn: number } | null;
}

/** One step of an animation, `t` from 0 to 1; t = 1 is the resting frame. */
export type Anim = { kind: "flash"; t: number } | { kind: "ripple"; t: number };

export const EDIT_TINT = mix(ACCENT, 0xffffff, 0.45);
const READ_DIM = 0.4;
const PATH_AMOUNT = 0.85;
const MATCH_FLASH = 0.85;
const MATCH_REST = 0.25;
const RING_AMOUNT = 0.5;
const SMALL = 3;

/** A drawn file for a repo-relative path. */
function lookup(layout: MapLayout, rel: string): number | undefined {
  return layout.index.get(layout.caseInsensitive ? rel.toLowerCase() : rel);
}

/** A drawn file for an absolute path under `root`. */
function locate(layout: MapLayout, abs: string, root: string): number | undefined {
  const rel = relativeTo(abs, root, layout.caseInsensitive);
  return rel === null ? undefined : lookup(layout, rel);
}

function split<T>(items: readonly T[], find: (item: T) => number | undefined): { found: number[]; missing: number } {
  const found: number[] = [];
  for (const item of items) {
    const i = find(item);
    if (i !== undefined) found.push(i);
  }
  return { found, missing: items.length - found.length };
}

/**
 * Maps the trail's absolute paths onto drawn files under `repoRoot`. A path
 * outside the repo, outside the feed, or too small to draw is "not drawn".
 */
export function resolveOverlay(layout: MapLayout, trail: TrailState, repoRoot: string): ResolvedOverlay {
  const reads = split(trail.reads, (p) => locate(layout, p, repoRoot));
  const hits = split(trail.hits, (p) => locate(layout, p, repoRoot)).found;
  const callers = split(trail.callers?.status === "ready" ? trail.callers.paths : [], (p) => lookup(layout, p));
  const edited = trail.edit === null ? null : (locate(layout, trail.edit, repoRoot) ?? null);
  const edit =
    trail.edit === null
      ? null
      : { name: trail.edit.slice(trail.edit.lastIndexOf("/") + 1), callers: trail.callers, notDrawn: callers.missing };
  return {
    overlay: { reads: reads.found, hits, edited, callers: callers.found },
    reads: { count: trail.reads.length, capped: trail.readsCapped, notDrawn: reads.missing },
    matched: trail.searches === 0 ? null : trail.hits.length,
    edit,
  };
}

function center(f: PlacedFile): [number, number] {
  return [Math.floor((f.px0 + f.px1 - 1) / 2), Math.floor((f.py0 + f.py1 - 1) / 2)];
}

function distance(a: PlacedFile, b: PlacedFile): number {
  const [ax, ay] = center(a);
  const [bx, by] = center(b);
  return Math.hypot(ax - bx, ay - by);
}

/** How far the ripple travels: just past its farthest importer; 0 when there is nothing to ripple to. */
export function rippleRadius(layout: MapLayout, overlay: Overlay): number {
  const origin = overlay.edited === null ? undefined : layout.files[overlay.edited];
  if (!origin || overlay.callers.length === 0) return 0;
  return Math.max(...overlay.callers.map((i) => distance(origin, layout.files[i] as PlacedFile))) + 3;
}

interface Paint {
  layout: MapLayout;
  px: Uint32Array;
  marked: Uint8Array;
}

function inside(layout: MapLayout, x: number, y: number): boolean {
  return x >= 0 && y >= 0 && x < layout.width && y < layout.height;
}

function set(p: Paint, x: number, y: number, color: number): void {
  if (!inside(p.layout, x, y)) return;
  const width = p.layout.width;
  p.px[y * width + x] = color;
  p.marked[y * width + x] = 1;
}

function fillFile(p: Paint, f: PlacedFile, color: (current: number) => number): void {
  const w = p.layout.width;
  for (let y = f.py0; y < f.py1; y++) for (let x = f.px0; x < f.px1; x++) set(p, x, y, color(p.px[y * w + x] ?? GROUND));
}

function outlineFile(p: Paint, f: PlacedFile, color: number): void {
  for (let x = f.px0; x < f.px1; x++) {
    set(p, x, f.py0, color);
    set(p, x, f.py1 - 1, color);
  }
  for (let y = f.py0; y < f.py1; y++) {
    set(p, f.px0, y, color);
    set(p, f.px1 - 1, y, color);
  }
}

const isSmall = (f: PlacedFile): boolean => f.px1 - f.px0 < SMALL || f.py1 - f.py0 < SMALL;

/** Every third pixel along the straight line between two consecutive reads. */
function paintPath(p: Paint, overlay: Overlay): void {
  const files = p.layout.files;
  for (let k = 1; k < overlay.reads.length; k++) {
    const [ax, ay] = center(files[overlay.reads[k - 1] as number] as PlacedFile);
    const [bx, by] = center(files[overlay.reads[k] as number] as PlacedFile);
    const steps = Math.max(Math.abs(bx - ax), Math.abs(by - ay));
    for (let s = 1; s < steps; s += 3) {
      const x = Math.round(ax + ((bx - ax) * s) / steps);
      const y = Math.round(ay + ((by - ay) * s) / steps);
      set(p, x, y, mix(p.px[y * p.layout.width + x] ?? GROUND, ACCENT, PATH_AMOUNT));
    }
  }
}

function paintHits(p: Paint, overlay: Overlay, amount: number): void {
  for (const i of overlay.hits) fillFile(p, p.layout.files[i] as PlacedFile, (c) => mix(c, ACCENT, amount));
}

/** A read: its interior dimmed so the orange outline holds against warm bands. */
function paintReads(p: Paint, overlay: Overlay): void {
  for (const i of overlay.reads) {
    const f = p.layout.files[i] as PlacedFile;
    fillFile(p, f, (c) => mix(c, 0x000000, READ_DIM));
    outlineFile(p, f, ACCENT);
  }
}

/** The edited file: pale orange inside an orange outline (a small one is all pale, unlike a small read). */
function paintEdited(p: Paint, f: PlacedFile): void {
  fillFile(p, f, () => EDIT_TINT);
  if (!isSmall(f)) outlineFile(p, f, ACCENT);
}

function paintRing(p: Paint, origin: PlacedFile, reach: number): void {
  const [ox, oy] = center(origin);
  const { width, height } = p.layout;
  for (let y = Math.max(0, Math.floor(oy - reach)); y <= Math.min(height - 1, oy + reach); y++) {
    for (let x = Math.max(0, Math.floor(ox - reach)); x <= Math.min(width - 1, ox + reach); x++) {
      const d = Math.hypot(x - ox, y - oy);
      if (d <= reach && d > reach - 2) set(p, x, y, mix(p.px[y * width + x] ?? GROUND, ACCENT, RING_AMOUNT));
    }
  }
}

/** An importer: an orange pixel with a dark one beside it in the same cell, so it holds on any band. */
function paintCallers(p: Paint, overlay: Overlay, origin: PlacedFile | undefined, reach: number): void {
  for (const i of overlay.callers) {
    const f = p.layout.files[i] as PlacedFile;
    if (origin !== undefined && distance(origin, f) > reach) continue;
    const [x, y] = center(f);
    set(p, x, y, ACCENT);
    set(p, x, y ^ 1, GROUND);
  }
}

function matchAmount(anim: Anim | undefined): number {
  return anim?.kind === "flash" ? MATCH_REST + (MATCH_FLASH - MATCH_REST) * (1 - anim.t) : MATCH_REST;
}

function rippleReach(layout: MapLayout, overlay: Overlay, anim: Anim | undefined): number {
  return anim?.kind === "ripple" && anim.t < 1 ? rippleRadius(layout, overlay) * anim.t : Infinity;
}

/** Map pixels for one frame: the base map with the trail, the edit and the ripple painted on. */
export function framePixels(layout: MapLayout, overlay: Overlay, anim?: Anim): { pixels: Uint32Array; marked: Uint8Array } {
  const p: Paint = { layout, px: layout.base.slice(), marked: new Uint8Array(layout.width * layout.height) };
  const origin = overlay.edited === null ? undefined : layout.files[overlay.edited];
  const reach = rippleReach(layout, overlay, anim);
  paintPath(p, overlay);
  paintHits(p, overlay, matchAmount(anim));
  paintReads(p, overlay);
  if (origin !== undefined) paintEdited(p, origin);
  if (origin !== undefined && reach < Infinity) paintRing(p, origin, reach);
  paintCallers(p, overlay, origin, reach);
  return { pixels: p.px, marked: p.marked };
}

/** One frame's cells: the resting frame without `anim`. */
export function frameCells(layout: MapLayout, overlay: Overlay, anim?: Anim): string {
  const { pixels, marked } = framePixels(layout, overlay, anim);
  return encodeCells(layout, pixels, marked);
}

/** Animation lengths; frames go out every `FRAME_MS`, under the surface's 30 per second. */
export const FRAME_MS = 40;
export const FLASH_MS = 600;
export const RIPPLE_MS = 1_200;
