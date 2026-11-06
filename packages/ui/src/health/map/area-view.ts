/**
 * Semantic zoom, decided once per frame: which areas are solid discs, which
 * are faint containers around what is inside, which hosts paint their file
 * dots, and what the field should load next.
 *
 * Files are texture at every zoom: a folder's files load and draw as soon as
 * a dot would be at least a device pixel across ({@link MIN_DOT_DEVICE_R}),
 * so the root already shows every file coloured by the lens. Below that, or
 * before the files arrive, a folder is one solid disc in its own colour. A
 * folder also opens to show its sub-folders once it clears {@link OPEN_PX}.
 *
 * Pure: a camera, a box and the laid-out field in, a plain view out.
 */

import { ROOT_AREA } from "../../lib/areas";
import type { AreaIndex } from "./areas";
import type { AreaGeometry, Circle, Host } from "./area-layout";
import { displayChildren } from "./area-paths";
import type { Cam, View } from "./paint";

/** An area this small on screen draws no disc of its own; its files may still show. */
export const MIN_AREA_PX = 3;
/** Screen radius at which an area opens to show its sub-areas without its files. */
export const OPEN_PX = 90;
/**
 * Smallest dot worth drawing, as a radius in device pixels: half a device
 * pixel is a dot one device pixel across. Below it a folder stays solid.
 */
export const MIN_DOT_DEVICE_R = 0.5;
/** Share of a packed disc its dots cover, for estimating a dot's size before layout. */
const PACK_FILL = 0.5;
/** Largest area whose files are fetched in one request (the server's cap). */
export const MAX_AREA_FILES = 8000;
/** Pan slack, so discs just off screen are ready when they slide in. */
const MARGIN_PX = 48;

export type DiscState = "open" | "closed" | "slot";

export interface DiscView {
  path: string;
  /** Screen centre and radius, in CSS px. */
  x: number;
  y: number;
  r: number;
  state: DiscState;
}

export interface FieldView {
  /** Outer discs first, so a child paints over its parent. */
  discs: DiscView[];
  /** Per host: 1 when its file dots are drawn this frame. */
  drawnHosts: Uint8Array;
  /** Areas on screen and big enough to open whose children are not known yet. */
  wantChildren: string[];
  /** The shallowest areas dense enough on screen to show files not loaded yet. */
  wantFiles: string[];
}

export interface ViewInput {
  index: AreaIndex;
  geo: AreaGeometry;
  hosts: readonly Host[];
  /** Host key -> index into `hosts`. */
  hostIndex: ReadonlyMap<string, number>;
  /** Areas whose complete file list is loaded or on its way (covers descendants). */
  filesCovered: (path: string) => boolean;
  cam: Cam;
  view: View;
}

function toScreen(c: Circle, cam: Cam, view: View): { x: number; y: number; r: number } {
  return {
    x: view.w / 2 + (c.x - cam.cx) * cam.scale,
    y: view.h / 2 + (c.y - cam.cy) * cam.scale,
    r: c.r * cam.scale,
  };
}

const offscreen = (s: { x: number; y: number; r: number }, view: View) =>
  s.x + s.r < -MARGIN_PX ||
  s.y + s.r < -MARGIN_PX ||
  s.x - s.r > view.w + MARGIN_PX ||
  s.y - s.r > view.h + MARGIN_PX;

/** Whether dots of this world radius are at least a device pixel across. */
const visibleDot = (worldR: number, cam: Cam, view: View) => worldR * cam.scale * view.dpr >= MIN_DOT_DEVICE_R;

/** A disc of screen radius `r` holding `files` dots: would they be visible once loaded? */
const dotsWouldShow = (r: number, files: number, view: View) =>
  files > 0 && r * Math.sqrt(PACK_FILL / files) * view.dpr >= MIN_DOT_DEVICE_R;

type Screen = { x: number; y: number; r: number };

/** One frame's walk: the input, and the view it fills in. */
interface Walk {
  input: ViewInput;
  out: FieldView;
}

/** Draw a host's dots when they are dense enough; true when drawn. */
function tryHost(w: Walk, key: string): boolean {
  const i = w.input.hostIndex.get(key);
  if (i === undefined) return false;
  const h = w.input.hosts[i]!;
  if (!visibleDot(h.meanR, w.input.cam, w.input.view)) return false;
  w.out.drawnHosts[i] = 1;
  return true;
}

/**
 * Record what this area needs loaded. Returns whether its files are covered
 * for the areas below it, so only the shallowest dense area is fetched.
 */
function noteWants(w: Walk, path: string, s: Screen, covered: boolean): boolean {
  const { index, view } = w.input;
  const area = index.areas.get(path)!;
  const big = s.r >= OPEN_PX;
  if (big && area.hasChildren && !index.expanded.has(path)) w.out.wantChildren.push(path);
  if (covered || w.input.filesCovered(path)) return true;
  // Files load as soon as their dots would show, so the field has texture
  // from the root down; the shallowest such area is the one fetched.
  if (area.fileCount <= MAX_AREA_FILES && dotsWouldShow(s.r, area.fileCount, view)) {
    w.out.wantFiles.push(path);
    return true;
  }
  return false;
}

/** An area's own-files slot beside its sub-areas: dots when dense, else a quiet disc. */
function visitSlot(w: Walk, path: string): void {
  const slot = w.input.geo.slots.get(path);
  if (!slot || tryHost(w, `d:${path}`)) return;
  const ss = toScreen(slot, w.input.cam, w.input.view);
  if (ss.r >= MIN_AREA_PX && !offscreen(ss, w.input.view)) w.out.discs.push({ path, ...ss, state: "slot" });
}

function visit(w: Walk, path: string, covered: boolean): void {
  const { index, geo, cam, view } = w.input;
  const circle = geo.circles.get(path);
  if (!circle || !index.areas.has(path)) return;
  const s = toScreen(circle, cam, view);
  if (offscreen(s, view)) return;

  const below = noteWants(w, path, s, covered);
  const kids = index.expanded.has(path) ? displayChildren(index, path) : [];
  const leafFiles = kids.length === 0 && (tryHost(w, `d:${path}`) || tryHost(w, `w:${path}`));
  // A folder whose files are on the canvas is a faint container around them.
  const textured = kids.length > 0 && below && dotsWouldShow(s.r, index.areas.get(path)!.fileCount, view);
  const open = path === ROOT_AREA || (s.r >= OPEN_PX && kids.length > 0) || leafFiles || textured;
  if (s.r >= MIN_AREA_PX) w.out.discs.push({ path, ...s, state: open ? "open" : "closed" });
  if (!open || kids.length === 0) return;
  for (const k of kids) visit(w, k, below);
  visitSlot(w, path);
}

export function computeView(input: ViewInput): FieldView {
  const out: FieldView = {
    discs: [],
    drawnHosts: new Uint8Array(input.hosts.length),
    wantChildren: [],
    wantFiles: [],
  };
  visit({ input, out }, ROOT_AREA, false);
  return out;
}

/** The innermost disc under a screen point, or null. */
export function discAt(view: FieldView, x: number, y: number): DiscView | null {
  for (let i = view.discs.length - 1; i >= 0; i--) {
    const d = view.discs[i]!;
    if (Math.hypot(x - d.x, y - d.y) <= d.r) return d;
  }
  return null;
}
