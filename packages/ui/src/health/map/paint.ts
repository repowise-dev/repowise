/**
 * Painting the field onto a canvas, kept apart from React.
 *
 * Two kinds of mark. Area discs are a few hundred at most and are drawn in
 * screen space every frame, so a hairline stays a hairline at any zoom. File
 * dots are thousands that only change colour when the lens changes and only
 * change place when the camera moves, so they are grouped once per host into
 * batches that share a fill, each batch is one `Path2D` in world units, and a
 * frame fills the batches of the hosts it shows: the browser rasterizes the
 * dots and no JavaScript runs per file. Hover, selection, search and the
 * keyboard cursor paint on a second canvas above, so pointing at something
 * never repaints the field.
 */

import type { Host, PlacedFiles } from "./area-layout";
import type { DiscView, FieldView } from "./area-view";
import { mixRgb, rgba, type Palette, type Rgb } from "./colour";
import type { FileNode } from "./types";

export {
  createColourResolver,
  mixRgb,
  parseCssColour,
  resolvePalette,
  rgba,
  type Palette,
  type Rgb,
} from "./colour";

/** The placed files flattened for drawing: one index space for every node. */
export interface FieldScene {
  nodes: FileNode[];
  hosts: Host[];
  hostIndex: ReadonlyMap<string, number>;
  /** Node index -> host index. */
  hostOfNode: Int32Array;
  /** Path -> node index. */
  index: Map<string, number>;
}

export function buildScene(placed: PlacedFiles): FieldScene {
  const index = new Map<string, number>();
  placed.nodes.forEach((nd, i) => index.set(nd.file.file_path, i));
  return { nodes: placed.nodes, hosts: placed.hosts, hostIndex: placed.hostIndex, hostOfNode: placed.hostOfNode, index };
}

/** One fill within one host: the nodes that share a colour (or a pair, mid-tween). */
export interface Batch {
  host: number;
  from: Rgb;
  to: Rgb;
  /** Glow strength at each end, 0 or 1: only files that need attention glow. */
  glowFrom: number;
  glowTo: number;
  nodes: Int32Array;
  path: Path2D | null;
  glow: Path2D | null;
}

/**
 * Group each host's nodes by what they are filled with, so a frame fills one
 * path per colour per drawn host. Pure apart from `resolve`, which a test can
 * stub. `from` is the previous lens's fills during a tween, or null at rest.
 */
export function groupBatches(
  scene: FieldScene,
  to: readonly string[],
  from: readonly string[] | null,
  loud: (fill: string) => boolean,
  resolve: (css: string) => Rgb,
): Batch[] {
  const groups = new Map<string, { list: number[]; host: number; f: string; t: string }>();
  for (let i = 0; i < scene.nodes.length; i++) {
    const t = to[i]!;
    const f = from ? from[i]! : t;
    const host = scene.hostOfNode[i]!;
    const key = `${host}\u0000${f}\u0000${t}`;
    const g = groups.get(key);
    if (g) g.list.push(i);
    else groups.set(key, { list: [i], host, f, t });
  }
  const out: Batch[] = [];
  for (const g of groups.values()) {
    out.push({
      host: g.host,
      from: resolve(g.f),
      to: resolve(g.t),
      glowFrom: loud(g.f) ? 1 : 0,
      glowTo: loud(g.t) ? 1 : 0,
      nodes: Int32Array.from(g.list),
      path: null,
      glow: null,
    });
  }
  return out;
}

/** How much wider than its disc a node's glow reaches. */
const GLOW_SCALE = 2.2;

/**
 * Build each batch's paths, with every radius floored at `floor` world units
 * so a node never shrinks below a visible dot. Rebuilt only when the floor
 * moves a zoom step, not per frame.
 */
export function attachPaths(batches: Batch[], scene: FieldScene, floor: number): void {
  if (typeof Path2D === "undefined") return;
  for (const b of batches) {
    const path = new Path2D();
    const glow = b.glowFrom || b.glowTo ? new Path2D() : null;
    for (const i of b.nodes) {
      const nd = scene.nodes[i]!;
      const r = Math.max(nd.r, floor);
      path.moveTo(nd.x + r, nd.y);
      path.arc(nd.x, nd.y, r, 0, Math.PI * 2);
      if (glow) {
        glow.moveTo(nd.x + r * GLOW_SCALE, nd.y);
        glow.arc(nd.x, nd.y, r * GLOW_SCALE, 0, Math.PI * 2);
      }
    }
    b.path = path;
    b.glow = glow;
  }
}

export interface Cam {
  cx: number;
  cy: number;
  scale: number;
}

export interface View {
  w: number;
  h: number;
  dpr: number;
}

/** What one frame paints with: the files, the camera, the box, the theme. */
export interface Frame {
  scene: FieldScene;
  cam: Cam;
  view: View;
  palette: Palette;
  /** Which areas show as what, decided for this camera. */
  field: FieldView;
  /** An area's resolved fill under the active lens. */
  areaRgb: (path: string) => Rgb;
}

/** Peak opacity of a glow. Soft enough to read as light, not as a ring. */
const GLOW_ALPHA = 0.16;
/** A solid area disc: the lens colour, a touch quieter than a file dot. */
const CLOSED_ALPHA = 0.82;
/** An own-files slot not yet showing its files. */
const SLOT_ALPHA = 0.45;

function screen(cam: Cam, view: View, x: number, y: number): [number, number] {
  return [view.w / 2 + (x - cam.cx) * cam.scale, view.h / 2 + (y - cam.cy) * cam.scale];
}

/** Whether a disc of screen radius `r` at `(x, y)` misses the box. */
function offscreen(view: View, x: number, y: number, r: number): boolean {
  return x + r < 0 || y + r < 0 || x - r > view.w || y - r > view.h;
}

/** Reset to CSS-pixel space and clear. */
function clear(ctx: CanvasRenderingContext2D, view: View): void {
  ctx.setTransform(view.dpr, 0, 0, view.dpr, 0, 0);
  ctx.clearRect(0, 0, view.w, view.h);
}

/**
 * One area disc. A solid disc carries the lens colour; an open one is a ring
 * with a breath of ink, so what is inside it does the talking.
 */
function drawDisc(ctx: CanvasRenderingContext2D, f: Frame, d: DiscView): void {
  const { palette } = f;
  ctx.beginPath();
  ctx.arc(d.x, d.y, d.r, 0, Math.PI * 2);
  if (d.state === "open") {
    ctx.fillStyle = rgba(palette.ink, 0.025);
    ctx.fill();
    ctx.lineWidth = 1;
    ctx.strokeStyle = rgba(palette.ring, 0.7);
    ctx.stroke();
    return;
  }
  ctx.fillStyle = rgba(f.areaRgb(d.path), d.state === "slot" ? SLOT_ALPHA : CLOSED_ALPHA);
  ctx.fill();
  if (d.r >= 6) {
    ctx.lineWidth = 1;
    ctx.strokeStyle = rgba(palette.ring, d.state === "slot" ? 0.35 : 0.6);
    ctx.stroke();
  }
}

/** One batch at tween progress `t`: its glow, if it has one, then its dots. */
function drawBatch(ctx: CanvasRenderingContext2D, b: Batch, t: number): void {
  if (!b.path) return;
  const fill = rgba(t >= 1 ? b.to : mixRgb(b.from, b.to, t));
  const glow = b.glowFrom + (b.glowTo - b.glowFrom) * t;
  ctx.fillStyle = fill;
  if (b.glow && glow > 0) {
    ctx.globalAlpha = GLOW_ALPHA * glow;
    ctx.fill(b.glow);
  }
  ctx.globalAlpha = 1;
  ctx.fill(b.path);
}

/**
 * The base layer: the area discs, then the file dots of every host this
 * frame draws. `t` is the lens tween's progress, 1 at rest.
 */
export function drawField(ctx: CanvasRenderingContext2D, f: Frame, batches: Batch[], t: number): void {
  const { cam, view, field } = f;
  clear(ctx, view);
  for (const d of field.discs) drawDisc(ctx, f, d);
  const s = cam.scale;
  const dp = view.dpr;
  ctx.setTransform(dp * s, 0, 0, dp * s, dp * (view.w / 2 - cam.cx * s), dp * (view.h / 2 - cam.cy * s));
  for (const b of batches) if (field.drawnHosts[b.host]) drawBatch(ctx, b, t);
  ctx.globalAlpha = 1;
  ctx.setTransform(dp, 0, 0, dp, 0, 0);
}

export interface Marks {
  selected: number;
  hovered: number;
  cursor: number;
  matches: readonly number[];
  /** Discs to ring this frame, when drawn. */
  selectedArea: DiscView | null;
  hoveredArea: DiscView | null;
  cursorArea: DiscView | null;
  /** A node's resolved fill, for repainting it above its neighbours. */
  fillOf: (i: number) => Rgb;
  /** Smallest drawn radius, in px, so a ring hugs a floored dot. */
  floorPx: number;
}

/** Screen centre and radius of node `i`, or null when it is not drawn. */
function nodeOnScreen(f: Frame, i: number, floorPx: number): [number, number, number] | null {
  const nd = f.scene.nodes[i];
  if (!nd || !f.field.drawnHosts[f.scene.hostOfNode[i]!]) return null;
  const [sx, sy] = screen(f.cam, f.view, nd.x, nd.y);
  return [sx, sy, Math.max(nd.r * f.cam.scale, floorPx)];
}

/**
 * The top layer: rings in screen pixels, so a ring is the same weight at any
 * zoom. Cheap enough to repaint on every hover change.
 */
export function drawMarks(ctx: CanvasRenderingContext2D, f: Frame, marks: Marks): void {
  clear(ctx, f.view);
  const circle = (x: number, y: number, r: number, width: number, colour: string, dash: number[] = []) => {
    if (offscreen(f.view, x, y, r)) return;
    ctx.setLineDash(dash);
    ctx.lineWidth = width;
    ctx.strokeStyle = colour;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.stroke();
  };
  const ring = (i: number, pad: number, width: number, colour: string, dash: number[] = []) => {
    const at = nodeOnScreen(f, i, marks.floorPx);
    if (at) circle(at[0], at[1], at[2] + pad, width, colour, dash);
  };
  const solid = (i: number, grow: number) => {
    const at = nodeOnScreen(f, i, marks.floorPx);
    if (!at) return;
    ctx.fillStyle = rgba(marks.fillOf(i));
    ctx.beginPath();
    ctx.arc(at[0], at[1], at[2] + grow, 0, Math.PI * 2);
    ctx.fill();
  };
  const accent = rgba(f.palette.accent);
  const ink = rgba(f.palette.ink, 0.85);
  const { hoveredArea: hov, selectedArea: sel, cursorArea: cur } = marks;
  if (hov && hov.path !== sel?.path) circle(hov.x, hov.y, hov.r + 1, 1.5, ink);
  if (sel) circle(sel.x, sel.y, sel.r + 1.5, 2, accent);
  if (cur) circle(cur.x, cur.y, cur.r + 4, 2, accent, [3, 2]);
  for (const i of marks.matches) ring(i, 2.5, 1.5, accent);
  if (marks.hovered >= 0 && marks.hovered !== marks.selected) {
    solid(marks.hovered, 1);
    ring(marks.hovered, 2, 1.5, ink);
  }
  if (marks.selected >= 0) {
    solid(marks.selected, 0);
    ring(marks.selected, 3, 2, accent);
  }
  if (marks.cursor >= 0) ring(marks.cursor, 5.5, 2, accent, [3, 2]);
  ctx.setLineDash([]);
}
