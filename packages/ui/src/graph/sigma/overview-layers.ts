/**
 * Canvas layers for the Files overview, drawn inside Sigma's own layer stack
 * so that anything compositing `sigma.getCanvases()` (the PNG export) gets
 * them for free. A DOM SVG overlay would not be captured.
 *
 * Bottom to top, with Sigma's layers in brackets:
 *
 *   overviewBands  - one band per community pair, under the file edges
 *   [edges] [edgeLabels] [nodes] [labels]
 *   overviewFocus  - community names; then, while a file is focused, a veil in
 *                    the canvas colour and that file's own edges with direction
 *   [hovers] [hoverNodes] - the focused file and its neighbours, drawn by Sigma
 *                    above the veil because the reducer marks them highlighted
 *
 * The veil is what makes focus cheap. Dimming the other ~1,300 nodes through
 * the node reducer would re-reduce every node on every hover; painting one
 * rectangle over them costs the same at any graph size, and the reducer only
 * re-runs for the focused neighbourhood (see `use-sigma`).
 *
 * Per frame this draws at most 16 bands, the cluster labels, and the focused
 * file's edges: no allocation beyond gradients, and no work at all when the
 * layer has nothing to show.
 */
import type Sigma from "sigma";
import type { CommunityFamily } from "../../shared/use-theme-tokens";
import type { OverviewMeta } from "./files-overview";
import { LABEL_FONT } from "./constants";

export interface OverviewPalette {
  /** The canvas plane, RGB. The veil and the label plates are this colour. */
  bg: readonly [number, number, number];
  text: string;
  subtitle: string;
  /** The focused file's own dependencies (it imports them). */
  outgoing: string;
  /** Files that depend on the focused file. */
  incoming: string;
  dark: boolean;
}

export interface OverviewFocus {
  node: string;
  /** Edges to draw, kept by the caller so the per-frame pass never filters. */
  edges: { source: string; target: string; kind: string }[];
}

export interface OverviewLayerState {
  meta: OverviewMeta;
  family: (communityId: number) => CommunityFamily;
  labelFor: (communityId: number) => string;
  palette: OverviewPalette;
  focus: OverviewFocus | null;
  /** Bands stand for the cross-community edges, so that edge toggle owns them. */
  showBands: boolean;
  /** Communities the legend has switched off, or null for all on. */
  activeCommunities: ReadonlySet<number> | null;
}

interface FocusStyle {
  color: string;
  dash: number[];
  width: number;
  lines: Path2D;
  heads: Path2D | null;
}

const SOLID: number[] = [];
const CO_CHANGE_DASH = [4, 3];
const LOW_CONFIDENCE_DASH = [1.5, 2.5];
/** Arrowhead length, px. */
const ARROW = 7;

/** A community this small is drawn but not named: its name would cover it. */
const LABEL_MIN_MEMBERS = 8; // matches CLUSTER_MIN in files-overview.ts

function sizeCanvas(sigma: Sigma, canvas: HTMLCanvasElement): CanvasRenderingContext2D | null {
  const { width, height } = sigma.getDimensions();
  const ratio = typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;
  const w = Math.round(width * ratio);
  const h = Math.round(height * ratio);
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return ctx;
}

interface NamePlacement {
  communityId: number;
  name: string;
  count: string;
  countW: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Graph units → pixels. The camera is a similarity transform, so one factor
 *  serves every radius and every curve. */
function pixelsPerUnit(sigma: Sigma, override?: CameraOverride): number {
  const o = sigma.graphToViewport({ x: 0, y: 0 }, override);
  const u = sigma.graphToViewport({ x: 100, y: 0 }, override);
  return Math.hypot(u.x - o.x, u.y - o.y) / 100;
}

type CameraOverride = { cameraState: ReturnType<ReturnType<Sigma["getCamera"]>["getState"]> };

/**
 * Where each community's name goes this frame: above its cluster (below it
 * when that would leave the canvas), largest first, a smaller cluster's name
 * yielding to a larger one's.
 */
function placeNames(
  sigma: Sigma,
  ctx: CanvasRenderingContext2D,
  state: OverviewLayerState,
): NamePlacement[] {
  const { width, height } = sigma.getDimensions();
  // Placed on `beforeRender`, when Sigma still holds the previous frame's
  // matrix: convert with the camera as it is now, or a camera jump leaves the
  // names where the clusters were.
  const override: CameraOverride = { cameraState: sigma.getCamera().getState() };
  const scale = pixelsPerUnit(sigma, override);
  if (!Number.isFinite(scale)) return [];
  const active = state.activeCommunities;
  const out: NamePlacement[] = [];
  for (const c of state.meta.clusters) {
    if (c.count < LABEL_MIN_MEMBERS || (active && !active.has(c.communityId))) continue;
    const p = sigma.graphToViewport({ x: c.cx, y: c.cy }, override);
    const r = c.r * scale;
    const name = state.labelFor(c.communityId);
    const count = String(c.count);
    ctx.font = `600 12px ${LABEL_FONT}`;
    const nameW = ctx.measureText(name).width;
    ctx.font = `400 11px ${LABEL_FONT}`;
    const countW = ctx.measureText(count).width;
    // pad, swatch, gap, name, gap, count, pad
    const w = 8 + 7 + 5 + nameW + 7 + countW + 8;
    const h = 20;
    let y = p.y - r - 14 - h / 2;
    if (y < 4) y = p.y + r + 14 - h / 2;
    const x = p.x - w / 2;
    if (x + w < 0 || x > width || y + h < 0 || y > height) continue;
    if (out.some((q) => x < q.x + q.w && x + w > q.x && y < q.y + q.h && y + h > q.y)) continue;
    out.push({ communityId: c.communityId, name, count, countW, x, y, w, h });
  }
  return out;
}

/**
 * Attach the two layers to a Sigma instance. Returns the detach function.
 * `getState` is read once per frame; returning null leaves both layers blank.
 * `reserve` is handed each community name's rectangle before Sigma draws its
 * file labels, so a file label never lands under a community name.
 */
export function attachOverviewLayers(
  sigma: Sigma,
  getState: () => OverviewLayerState | null,
  reserve?: (x: number, y: number, w: number, h: number) => void,
): () => void {
  const bands = sigma.createCanvas("overviewBands", { beforeLayer: "edges" });
  const focus = sigma.createCanvas("overviewFocus", { beforeLayer: "hovers" });
  for (const c of [bands, focus]) {
    c.style.position = "absolute";
    c.style.inset = "0";
    c.style.pointerEvents = "none";
  }

  // Names are placed on `beforeRender`, where they can reserve their space
  // ahead of the file labels, and drawn on `afterRender` from the same result.
  let names: NamePlacement[] | null = null;
  const place = () => {
    // Sigma sizes itself inside `render`, after this event; a canvas that just
    // changed size would otherwise place the names against the old one. A
    // no-op when nothing changed.
    sigma.resize();
    const state = getState();
    const ctx = focus.getContext("2d");
    names = state && ctx ? placeNames(sigma, ctx, state) : null;
    if (names && reserve) for (const n of names) reserve(n.x, n.y, n.w, n.h);
  };

  // Whether the layers hold pixels. A graph without overview meta (the
  // Communities view, a drilled-in community) then costs nothing per frame
  // after the one clear that leaving the overview needs.
  let dirty = false;
  const draw = () => {
    const state = getState();
    if (!state && !dirty) return;
    const bctx = sizeCanvas(sigma, bands);
    const fctx = sizeCanvas(sigma, focus);
    dirty = !!state;
    if (!state || !bctx || !fctx) return;
    const { meta, palette } = state;
    const { width, height } = sigma.getDimensions();
    const ratio = sigma.getCamera().ratio;
    // Sigma's zero-length camera animation (the reduced-motion path) renders
    // one frame with a NaN camera. Nothing drawn from it could be right, and
    // `createLinearGradient` throws on it.
    if (!Number.isFinite(ratio) || !Number.isFinite(pixelsPerUnit(sigma))) {
      names = null;
      return;
    }
    const active = state.activeCommunities;
    const on = (cid: number) => !active || active.has(cid);

    // ---- Bands ----
    if (state.showBands) {
      // Width tracks zoom gently: bands must not fatten into blobs close up
      // or vanish into hairlines far out.
      const zoomWidth = Math.min(2.2, Math.max(0.7, 1 / Math.sqrt(ratio)));
      bctx.lineCap = "round";
      for (const b of meta.bundles) {
        if (!on(b.a) || !on(b.b)) continue;
        const p0 = sigma.graphToViewport({ x: b.x0, y: b.y0 });
        const pc = sigma.graphToViewport({ x: b.cx, y: b.cy });
        const p1 = sigma.graphToViewport({ x: b.x1, y: b.y1 });
        const grad = bctx.createLinearGradient(p0.x, p0.y, p1.x, p1.y);
        grad.addColorStop(0, state.family(b.a).hub);
        grad.addColorStop(1, state.family(b.b).hub);
        bctx.globalAlpha = (palette.dark ? 0.42 : 0.3) + (palette.dark ? 0.45 : 0.4) * b.weight;
        bctx.strokeStyle = grad;
        bctx.lineWidth = (1.5 + 14 * b.weight) * zoomWidth;
        bctx.beginPath();
        bctx.moveTo(p0.x, p0.y);
        bctx.quadraticCurveTo(pc.x, pc.y, p1.x, p1.y);
        bctx.stroke();
      }
      bctx.globalAlpha = 1;
    }

    // ---- Community names, above each cluster ----
    const [br, bg, bb] = palette.bg;
    const plate = `rgba(${br},${bg},${bb},0.86)`;
    fctx.textBaseline = "middle";
    fctx.textAlign = "left";
    for (const n of names ?? placeNames(sigma, fctx, state)) {
      fctx.fillStyle = plate;
      fctx.beginPath();
      fctx.roundRect(n.x, n.y, n.w, n.h, 5);
      fctx.fill();
      fctx.fillStyle = state.family(n.communityId).hub;
      fctx.beginPath();
      fctx.arc(n.x + 8 + 3.5, n.y + n.h / 2, 3.5, 0, Math.PI * 2);
      fctx.fill();
      fctx.fillStyle = palette.text;
      fctx.font = `600 12px ${LABEL_FONT}`;
      fctx.fillText(n.name, n.x + 8 + 7 + 5, n.y + n.h / 2);
      fctx.fillStyle = palette.subtitle;
      fctx.font = `400 11px ${LABEL_FONT}`;
      fctx.fillText(n.count, n.x + n.w - 8 - n.countW, n.y + n.h / 2);
    }
    names = null;

    // ---- Focus: veil, then the focused file's own edges ----
    const f = state.focus;
    if (!f) return;
    // While the camera moves Sigma skips its hover layers (`hideLabelsOnMove`)
    // but still fires `afterRender`, so a veil drawn now would cover the
    // focused file and its neighbours until the camera settles. Same test
    // Sigma uses to decide it is moving.
    const captor = sigma.getMouseCaptor();
    if (
      sigma.getCamera().isAnimated() ||
      captor.isMoving ||
      captor.draggedEvents > 0 ||
      captor.currentWheelDirection !== 0
    ) {
      return;
    }
    fctx.fillStyle = `rgba(${br},${bg},${bb},${palette.dark ? 0.76 : 0.72})`;
    fctx.fillRect(0, 0, width, height);

    // One path per stroke style, stroked once, and one path of arrowheads per
    // colour, filled once: a hub file's few hundred edges are a handful of
    // draw calls rather than two per edge. Drawn incoming first, so the
    // file's own dependencies (the accent) sit on top.
    const styles: FocusStyle[] = [
      { color: palette.subtitle, dash: CO_CHANGE_DASH, width: 1.1, lines: new Path2D(), heads: null },
      { color: palette.incoming, dash: LOW_CONFIDENCE_DASH, width: 1.6, lines: new Path2D(), heads: null },
      { color: palette.incoming, dash: SOLID, width: 1.6, lines: new Path2D(), heads: new Path2D() },
      { color: palette.outgoing, dash: LOW_CONFIDENCE_DASH, width: 1.6, lines: new Path2D(), heads: null },
      { color: palette.outgoing, dash: SOLID, width: 1.6, lines: new Path2D(), heads: new Path2D() },
    ];
    const [coChangeStyle, inDotted, inSolid, outDotted, outSolid] = styles as [
      FocusStyle, FocusStyle, FocusStyle, FocusStyle, FocusStyle,
    ];
    for (const e of f.edges) {
      const s = sigma.getNodeDisplayData(e.source);
      const t = sigma.getNodeDisplayData(e.target);
      if (!s || !t || s.hidden || t.hidden) continue;
      const a = sigma.framedGraphToViewport(s);
      const b = sigma.framedGraphToViewport(t);
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const len = Math.hypot(dx, dy);
      if (len < 1) continue;
      // A gentle bend, always to the same side of the travel direction, so an
      // import and its reverse do not draw on top of each other.
      const cx = (a.x + b.x) / 2 - dy * 0.12;
      const cy = (a.y + b.y) / 2 + dx * 0.12;
      // End at the target's rim, where the arrowhead goes.
      const tx = b.x - cx;
      const ty = b.y - cy;
      const tl = Math.hypot(tx, ty) || 1;
      const ux = tx / tl;
      const uy = ty / tl;
      const rim = sigma.scaleSize(t.size) + 1.5;
      const ex = b.x - ux * rim;
      const ey = b.y - uy * rim;

      const outgoing = e.source === f.node;
      const style =
        e.kind === "dynamic"
          ? coChangeStyle
          : e.kind === "lowConfidence"
            ? outgoing ? outDotted : inDotted
            : outgoing ? outSolid : inSolid;
      style.lines.moveTo(a.x, a.y);
      style.lines.quadraticCurveTo(cx, cy, ex, ey);
      // Co-change has no direction, so it gets no arrowhead; a dotted
      // low-confidence edge borrows its colour's solid head.
      const heads = e.kind === "dynamic" ? null : (outgoing ? outSolid : inSolid).heads;
      if (heads) {
        heads.moveTo(ex, ey);
        heads.lineTo(ex - ux * ARROW - uy * ARROW * 0.5, ey - uy * ARROW + ux * ARROW * 0.5);
        heads.lineTo(ex - ux * ARROW + uy * ARROW * 0.5, ey - uy * ARROW - ux * ARROW * 0.5);
        heads.closePath();
      }
    }
    for (const style of styles) {
      fctx.strokeStyle = style.color;
      fctx.lineWidth = style.width;
      fctx.setLineDash(style.dash);
      fctx.stroke(style.lines);
      if (style.heads) {
        fctx.fillStyle = style.color;
        fctx.fill(style.heads);
      }
    }
    fctx.setLineDash(SOLID);
  };

  sigma.on("beforeRender", place);
  sigma.on("afterRender", draw);
  draw();
  return () => {
    sigma.off("beforeRender", place);
    sigma.off("afterRender", draw);
    try {
      sigma.killLayer("overviewBands");
      sigma.killLayer("overviewFocus");
    } catch {
      // Sigma was already killed, and took its layers with it.
    }
  };
}

