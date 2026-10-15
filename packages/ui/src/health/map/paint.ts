/**
 * Painting the field onto a canvas, kept apart from React.
 *
 * The field is a few thousand discs that only change colour when the lens
 * changes and only change place when the camera moves. So the nodes are
 * grouped once into batches that share a fill, each batch becomes one
 * `Path2D` in world units, and a frame is a transform plus one `fill()` per
 * batch: the browser rasterizes the discs and no JavaScript runs per node.
 * Hover, selection, search and the keyboard cursor paint on a second canvas
 * above it, so pointing at a node never repaints the field.
 *
 * Colours arrive as CSS strings (token `var()`s and `color-mix()`es, so the
 * key and the field read one source) and are resolved to RGB once per theme
 * through the browser's own computed style.
 */

import type { FileNode, Galaxy } from "./types";

export type Rgb = readonly [number, number, number];

const FALLBACK: Rgb = [128, 128, 128];

/** Parse a computed colour: `rgb()`, `rgba()`, `color(srgb …)` or hex. */
export function parseCssColour(value: string): Rgb | null {
  const v = value.trim();
  let m = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i.exec(v);
  if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
  m = /^color\(srgb\s+([\d.e-]+)\s+([\d.e-]+)\s+([\d.e-]+)/i.exec(v);
  if (m) return [Number(m[1]) * 255, Number(m[2]) * 255, Number(m[3]) * 255];
  m = /^#([0-9a-f]{6})$/i.exec(v);
  if (m) {
    const n = parseInt(m[1]!, 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  return null;
}

/**
 * A cached CSS-colour-to-RGB resolver bound to one element, so `var()` reads
 * the theme in force there. Build a new one when the theme flips.
 */
export function createColourResolver(host: HTMLElement | null): (css: string) => Rgb {
  const cache = new Map<string, Rgb>();
  let probe: HTMLSpanElement | null = null;
  return (css) => {
    const hit = cache.get(css);
    if (hit) return hit;
    let rgb: Rgb | null = parseCssColour(css);
    if (!rgb && host && typeof getComputedStyle !== "undefined") {
      if (!probe) {
        probe = document.createElement("span");
        probe.style.display = "none";
        host.appendChild(probe);
      }
      probe.style.color = "";
      probe.style.color = css;
      rgb = parseCssColour(getComputedStyle(probe).color);
    }
    const out = rgb ?? FALLBACK;
    cache.set(css, out);
    return out;
  };
}

export function rgba([r, g, b]: Rgb, a = 1): string {
  return `rgba(${Math.round(r)},${Math.round(g)},${Math.round(b)},${a})`;
}

export function mixRgb(a: Rgb, b: Rgb, t: number): Rgb {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

/** Theme colours the field paints with besides the node fills. */
export interface Palette {
  ink: Rgb;
  ring: Rgb;
  accent: Rgb;
}

export function resolvePalette(resolve: (css: string) => Rgb): Palette {
  return {
    ink: resolve("var(--color-text-primary)"),
    ring: resolve("var(--color-border-default)"),
    accent: resolve("var(--color-accent-primary)"),
  };
}

/** The laid-out field flattened for drawing: one index space for every node. */
export interface FieldScene {
  galaxies: Galaxy[];
  nodes: FileNode[];
  /** Node index -> galaxy index. */
  galaxyOf: Int32Array;
  /** Path -> node index. */
  index: Map<string, number>;
}

export function buildScene(galaxies: Galaxy[]): FieldScene {
  const nodes: FileNode[] = [];
  const owner: number[] = [];
  galaxies.forEach((g, gi) => {
    for (const nd of g.nodes) {
      nodes.push(nd);
      owner.push(gi);
    }
  });
  const index = new Map<string, number>();
  nodes.forEach((nd, i) => index.set(nd.file.file_path, i));
  return { galaxies, nodes, galaxyOf: Int32Array.from(owner), index };
}

/** One fill: the nodes that share a colour (or a colour pair, mid-tween). */
export interface Batch {
  from: Rgb;
  to: Rgb;
  /** Glow strength at each end, 0 or 1: only files that need attention glow. */
  glowFrom: number;
  glowTo: number;
  /** Nodes outside the focused galaxy, drawn dimmed. */
  faded: boolean;
  nodes: Int32Array;
  path: Path2D | null;
  glow: Path2D | null;
}

/**
 * Group the nodes by what they are filled with. Pure apart from `resolve`,
 * which a test can stub. `from` is the previous lens's fills during a tween,
 * or null at rest; `loud` says whether a fill marks a problem.
 */
export function groupBatches(
  scene: FieldScene,
  to: readonly string[],
  from: readonly string[] | null,
  focus: number,
  loud: (fill: string) => boolean,
  resolve: (css: string) => Rgb,
): Batch[] {
  const groups = new Map<string, { list: number[]; f: string; t: string; faded: boolean }>();
  for (let i = 0; i < scene.nodes.length; i++) {
    const t = to[i]!;
    const f = from ? from[i]! : t;
    const faded = focus >= 0 && scene.galaxyOf[i] !== focus;
    const key = `${faded ? 1 : 0}\u0000${f}\u0000${t}`;
    const g = groups.get(key);
    if (g) g.list.push(i);
    else groups.set(key, { list: [i], f, t, faded });
  }
  const out: Batch[] = [];
  for (const g of groups.values()) {
    out.push({
      from: resolve(g.f),
      to: resolve(g.t),
      glowFrom: loud(g.f) ? 1 : 0,
      glowTo: loud(g.t) ? 1 : 0,
      faded: g.faded,
      nodes: Int32Array.from(g.list),
      path: null,
      glow: null,
    });
  }
  // Dimmed galaxies first, so the focused one paints over its neighbours.
  out.sort((a, b) => Number(b.faded) - Number(a.faded));
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

/** Opacity of a node outside the focused galaxy. */
const FADED_ALPHA = 0.16;
/** Peak opacity of a glow. Soft enough to read as light, not as a ring. */
const GLOW_ALPHA = 0.16;

function screen(cam: Cam, view: View, x: number, y: number): [number, number] {
  return [view.w / 2 + (x - cam.cx) * cam.scale, view.h / 2 + (y - cam.cy) * cam.scale];
}

/**
 * The base layer: each galaxy's nebula and hairline, then the nodes.
 * `t` is the lens tween's progress, 1 at rest.
 */
export function drawField(
  ctx: CanvasRenderingContext2D,
  scene: FieldScene,
  batches: Batch[],
  cam: Cam,
  view: View,
  t: number,
  focus: number,
  palette: Palette,
): void {
  const { dpr } = view;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, view.w, view.h);

  // Galaxies are neutral: a soft cloud and a hairline, and no hue, because
  // the nodes inside already speak in red, amber and green.
  scene.galaxies.forEach((g, gi) => {
    const [sx, sy] = screen(cam, view, g.cx, g.cy);
    const R = g.R * cam.scale;
    if (sx + R < 0 || sy + R < 0 || sx - R > view.w || sy - R > view.h || R < 1) return;
    const faded = focus >= 0 && gi !== focus;
    const cloud = ctx.createRadialGradient(sx, sy, 0, sx, sy, R * 1.12);
    cloud.addColorStop(0, rgba(palette.ink, faded ? 0.012 : 0.04));
    cloud.addColorStop(1, rgba(palette.ink, 0));
    ctx.fillStyle = cloud;
    ctx.beginPath();
    ctx.arc(sx, sy, R * 1.12, 0, Math.PI * 2);
    ctx.fill();
    ctx.lineWidth = gi === focus ? 1.25 : 1;
    ctx.strokeStyle = rgba(palette.ring, faded ? 0.3 : gi === focus ? 1 : 0.6);
    ctx.beginPath();
    ctx.arc(sx, sy, R, 0, Math.PI * 2);
    ctx.stroke();
  });

  const s = cam.scale;
  ctx.setTransform(dpr * s, 0, 0, dpr * s, dpr * (view.w / 2 - cam.cx * s), dpr * (view.h / 2 - cam.cy * s));
  for (const b of batches) {
    if (!b.path) continue;
    const alpha = b.faded ? FADED_ALPHA : 1;
    const rgb = t >= 1 ? b.to : mixRgb(b.from, b.to, t);
    const glow = b.glowFrom + (b.glowTo - b.glowFrom) * t;
    if (b.glow && glow > 0) {
      ctx.globalAlpha = GLOW_ALPHA * glow * alpha;
      ctx.fillStyle = rgba(rgb);
      ctx.fill(b.glow);
    }
    ctx.globalAlpha = alpha;
    ctx.fillStyle = rgba(rgb);
    ctx.fill(b.path);
  }
  ctx.globalAlpha = 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

export interface Marks {
  selected: number;
  hovered: number;
  cursor: number;
  matches: readonly number[];
}

/**
 * The top layer: rings in screen pixels, so a ring is the same weight at any
 * zoom. Cheap enough to repaint on every hover change.
 */
export function drawMarks(
  ctx: CanvasRenderingContext2D,
  scene: FieldScene,
  cam: Cam,
  view: View,
  marks: Marks,
  fillOf: (i: number) => Rgb,
  floorPx: number,
  palette: Palette,
): void {
  const { dpr } = view;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, view.w, view.h);
  const ring = (i: number, pad: number, width: number, colour: string, dash?: number[]) => {
    const nd = scene.nodes[i];
    if (!nd) return;
    const [sx, sy] = screen(cam, view, nd.x, nd.y);
    const r = Math.max(nd.r * cam.scale, floorPx) + pad;
    if (sx + r < 0 || sy + r < 0 || sx - r > view.w || sy - r > view.h) return;
    ctx.setLineDash(dash ?? []);
    ctx.lineWidth = width;
    ctx.strokeStyle = colour;
    ctx.beginPath();
    ctx.arc(sx, sy, r, 0, Math.PI * 2);
    ctx.stroke();
  };
  const solid = (i: number, grow: number) => {
    const nd = scene.nodes[i];
    if (!nd) return;
    const [sx, sy] = screen(cam, view, nd.x, nd.y);
    ctx.fillStyle = rgba(fillOf(i));
    ctx.beginPath();
    ctx.arc(sx, sy, Math.max(nd.r * cam.scale, floorPx) + grow, 0, Math.PI * 2);
    ctx.fill();
  };
  const accent = rgba(palette.accent);
  for (const i of marks.matches) ring(i, 2.5, 1.5, accent);
  if (marks.hovered >= 0 && marks.hovered !== marks.selected) {
    solid(marks.hovered, 1);
    ring(marks.hovered, 2, 1.5, rgba(palette.ink, 0.85));
  }
  if (marks.selected >= 0) {
    solid(marks.selected, 0);
    ring(marks.selected, 3, 2, accent);
  }
  if (marks.cursor >= 0) ring(marks.cursor, 5.5, 2, accent, [3, 2]);
  ctx.setLineDash([]);
}
