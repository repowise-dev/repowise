/**
 * The canvas render loop: a thin, framework-agnostic shell around the pure math
 * and the recursive draw. Browser code.
 *
 * Dirty-flag `requestAnimationFrame`: the loop only paints when something
 * actually changed (`invalidate()`), then stops, so an idle canvas costs zero
 * CPU. A running camera fly (`animateTo` / `frameNode`) keeps the loop alive for
 * its duration and then settles. The backing store is sized to `devicePixelRatio`
 * for crisp text. While the camera moves the frame is drawn reduced: a pan
 * drops card shadows, the paper texture and relations, and a wheel zoom, key
 * press or fly drops only the shadows (each is a blur pass, about half the
 * raster cost of a frame). One full-quality repaint follows once the camera has
 * been still for `SETTLE_MS`; the loop idles without drawing until then.
 *
 * Hover is resolved here too, not per pointer event (see `pointer-pick.ts`):
 * the next frame picks once for the newest pointer position and paints the
 * result in that same frame.
 *
 * A WebGL upgrade path is open: the draw layer is isolated behind `drawScene`,
 * so a future GPU backend can replace it without touching camera, culling or
 * scene code.
 */

import { type Camera, type Viewport, cameraAfterResize, fitRoot, frameRect } from "./camera";
import { flyDuration, interpolateCamera } from "./camera-anim";
import { drawScene } from "./draw-tree";
import { FOCUS_MOVED, FRAME_FILL, FocusTracker, RAILS_MOVED } from "./focus-path";
import { PaperTexture } from "./paper";
import { type DrawStats, type PickEntry, createDrawStats, pickNode } from "./pick";
import { PixelRatioWatch } from "./pixel-ratio";
import { PointerPick } from "./pointer-pick";
import { sameVerbSet } from "./relation-summary";
import { carryCamera, sceneRootRect } from "./relayout";
import type { ZoomScene } from "./scene";
import type { ZoomPalette } from "./theme";
import type { ZoomNode } from "./types";

export interface FrameStats {
  drawn: number;
  culled: number;
  maxDepthDrawn: number;
  frameMs: number;
}

export interface FlyOptions {
  /** Explicit duration in ms; omitted -> derived from travel distance. */
  durationMs?: number;
  /** Skip the tween and jump (used for reduced-motion and URL restore). */
  reducedMotion?: boolean;
  /** Fraction of the viewport the framed node should fill. */
  fill?: number;
}

export interface ZoomRendererOptions {
  canvas: HTMLCanvasElement;
  palette: ZoomPalette;
  onStats?: ((stats: FrameStats) => void) | undefined;
  /** Fires (with the root -> focus chain) whenever the focus node changes. */
  onFocus?: ((chain: ZoomNode[]) => void) | undefined;
  /** Fires after a frame whose camera or viewport changed (the minimap). */
  onView?: ((cam: Camera, vp: Viewport) => void) | undefined;
  /** Fires once per frame after a `pointAt`, with the picked card and canvas-local point. */
  onHover?: ((hit: PickEntry | null, sx: number, sy: number) => void) | undefined;
}

/**
 * How long the camera must be still before the full-quality repaint. Long
 * enough to bridge the gap between wheel notches and pointer moves, so shadows
 * do not flicker back on mid-gesture; short enough to read as instant.
 */
const SETTLE_MS = 120;

interface Tween {
  from: Camera;
  to: Camera;
  start: number;
  dur: number;
}

function now(): number {
  return typeof performance !== "undefined" ? performance.now() : 0;
}

export class ZoomRenderer {
  private readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private palette: ZoomPalette;
  private readonly onStats: ((stats: FrameStats) => void) | undefined;
  private readonly onFocus: ((chain: ZoomNode[]) => void) | undefined;
  private readonly onView: ((cam: Camera, vp: Viewport) => void) | undefined;
  private readonly onHover: ((hit: PickEntry | null, sx: number, sy: number) => void) | undefined;
  /** Shared ruled-paper card texture; repaints once when the asset loads. */
  private readonly paper: PaperTexture;

  private scene: ZoomScene | null = null;
  private cam: Camera = { cx: 0.5, cy: 0.5, scale: 600 };
  private vp: Viewport = { w: 1, h: 1 };
  private dpr = 1;
  private hasFramedScene = false;

  private selectedId: string | null = null;
  private hoveredId: string | null = null;
  private readonly pointer = new PointerPick();
  private relationVerbs: ReadonlySet<string> | null = null;
  private highlightIds: ReadonlySet<string> | null = null;
  private showTestLinks = false;
  /** Until when the frame drops shadows, texture and relations (a pan). */
  private panUntil = 0;
  /** Until when the frame drops only shadows (zoom, keys). */
  private motionUntil = 0;
  /** The last drawn frame was reduced, so a full repaint is owed on settle. */
  private reduced = false;
  private tween: Tween | null = null;
  private readonly focus = new FocusTracker();
  private lastViewCam: Camera | null = null;
  private lastViewVp: Viewport | null = null;
  private dirty = true;
  private rafId: number | null = null;
  /** The last drawn frame, for hit-testing; rewritten in place each frame. */
  private readonly lastStats: DrawStats = createDrawStats();
  private hasDrawn = false;
  private readonly pixelRatio: PixelRatioWatch;

  constructor(opts: ZoomRendererOptions) {
    this.canvas = opts.canvas;
    const ctx = opts.canvas.getContext("2d");
    if (!ctx) throw new Error("ZoomRenderer: 2D canvas context unavailable");
    this.ctx = ctx;
    this.palette = opts.palette;
    this.onStats = opts.onStats;
    this.onFocus = opts.onFocus;
    this.onView = opts.onView;
    this.onHover = opts.onHover;
    // Repaint once the paper photo decodes so the first frame picks up the texture.
    this.paper = new PaperTexture(() => this.invalidate());
    this.resize();
    this.pixelRatio = new PixelRatioWatch(() => this.resize());
  }

  getCamera(): Camera {
    return this.cam;
  }

  getViewport(): Viewport {
    return this.vp;
  }

  /** Where the camera is headed: the running fly's target, else the camera. */
  getTargetCamera(): Camera {
    return this.tween?.to ?? this.cam;
  }

  /** The root -> focus chain as of the last drawn frame. */
  getFocusChain(): readonly ZoomNode[] {
    return this.focus.chain;
  }

  /**
   * Screen px a host overlay covers on the right edge (see
   * `Viewport.insetRight`). Moves nothing; later framing and the focus chain
   * measure the clear part of the canvas.
   */
  setInsetRight(px: number): void {
    if (px === (this.vp.insetRight ?? 0)) return;
    this.vp = { ...this.vp, insetRight: px };
    this.invalidate();
  }

  /** Manual camera set (pan/zoom): cancels any running fly. */
  setCamera(cam: Camera): void {
    this.motionUntil = now() + SETTLE_MS;
    this.jumpTo(cam);
  }

  /** Move the camera without counting it as motion (a jump draws at full quality). */
  private jumpTo(cam: Camera): void {
    this.tween = null;
    this.cam = cam;
    this.invalidate();
  }

  setPalette(palette: ZoomPalette): void {
    this.palette = palette;
    this.invalidate();
  }

  setSelected(id: string | null): void {
    if (id === this.selectedId) return;
    this.selectedId = id;
    this.invalidate();
  }

  /** The hovered node lifts its incident relations and firms its border. */
  setHovered(id: string | null): void {
    this.pointer.clear();
    if (id === this.hoveredId) return;
    this.hoveredId = id;
    this.invalidate();
  }

  /** Restrict drawn relations to these verbs, or null for all of them. */
  setRelationVerbs(verbs: ReadonlySet<string> | null): void {
    if (sameVerbSet(verbs, this.relationVerbs)) return;
    this.relationVerbs = verbs;
    this.invalidate();
  }

  /** Dim every card outside this set (a picked container), or null for none. */
  setHighlight(ids: ReadonlySet<string> | null): void {
    if (ids === this.highlightIds) return;
    this.highlightIds = ids;
    this.invalidate();
  }

  /** Draw the test layer's relations too (see `DrawOptions.showTestLinks`). */
  setShowTestLinks(show: boolean): void {
    if (show === this.showTestLinks) return;
    this.showTestLinks = show;
    this.invalidate();
  }

  setScene(scene: ZoomScene): void {
    const prev = this.scene;
    this.scene = scene;
    if (prev !== scene) this.focus.reset(prev?.nodes !== scene.nodes);
    // A rebuilt scene (new data, or a new canvas aspect) moves the cards: keep
    // the camera on what it was showing rather than on stale coordinates.
    if (this.hasFramedScene && prev && prev !== scene) {
      this.cam = carryCamera(prev, scene, this.cam, this.vp);
      if (this.tween) this.tween.to = carryCamera(prev, scene, this.tween.to, this.vp);
    }
    // Only frame once we have a real layout size; otherwise the first resize
    // callback (which delivers the true dimensions) does the initial fit.
    if (!this.hasFramedScene && this.vp.w > 1) {
      this.cam = fitRoot(this.vp, sceneRootRect(scene));
      this.hasFramedScene = true;
    }
    this.invalidate();
  }

  /** Has the scene been framed at least once (initial fit done)? */
  isFramed(): boolean {
    return this.hasFramedScene;
  }

  /**
   * Animate the camera to `target`. Reduced-motion or duration<=0 jumps. Calling
   * this mid-fly is intentional: `this.cam` is the live tweened position the last
   * frame drew, so a new fly starts smoothly from there (and `flyDuration` sizes
   * itself to the now-shorter remaining travel) rather than snapping.
   */
  animateTo(target: Camera, opts: FlyOptions = {}): void {
    if (opts.reducedMotion) {
      this.jumpTo(target);
      return;
    }
    const dur = opts.durationMs ?? flyDuration(this.cam, target);
    if (dur <= 0) {
      this.jumpTo(target);
      return;
    }
    this.tween = { from: this.cam, to: target, start: now(), dur };
    this.invalidate();
  }

  /** Fly the camera to frame a node by id. Returns false if it is not laid out. */
  frameNode(id: string, opts: FlyOptions = {}): boolean {
    if (!this.scene) return false;
    const rect = this.scene.worldRects.get(id);
    if (!rect) return false;
    this.animateTo(frameRect(this.vp, rect, opts.fill ?? FRAME_FILL), opts);
    return true;
  }

  /** Re-read the element size and resize the backing store for crisp pixels. */
  resize(): void {
    const cssW = Math.max(1, this.canvas.clientWidth);
    const cssH = Math.max(1, this.canvas.clientHeight);
    const dpr = typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;
    this.dpr = dpr;
    const prev = this.vp;
    this.vp = { w: cssW, h: cssH, insetRight: prev.insetRight ?? 0 };
    // Keep the camera where it is rather than refitting, so a container resize
    // (window, sidebar) never jumps the map; see `cameraAfterResize`.
    if (this.hasFramedScene) this.keepCameraAcrossResize(prev);
    // Assigning the backing size reallocates and clears it even when unchanged,
    // so only touch it on a real change.
    const bw = Math.round(cssW * dpr);
    const bh = Math.round(cssH * dpr);
    if (this.canvas.width !== bw) this.canvas.width = bw;
    if (this.canvas.height !== bh) this.canvas.height = bh;
    // Deferred initial fit, once the element has a real size.
    if (this.scene && !this.hasFramedScene && cssW > 1) {
      this.cam = fitRoot(this.vp, sceneRootRect(this.scene));
      this.hasFramedScene = true;
    }
    this.invalidate();
  }

  private keepCameraAcrossResize(prev: Viewport): void {
    if (!this.scene) return;
    const root = sceneRootRect(this.scene);
    this.cam = cameraAfterResize(this.cam, prev, this.vp, root);
    if (this.tween) this.tween.to = cameraAfterResize(this.tween.to, prev, this.vp, root);
  }

  /** Flag a pan so shadows, texture and relations are suppressed until it settles. */
  markPanFrame(): void {
    this.panUntil = now() + SETTLE_MS;
  }

  /** Hit-test the most recently drawn frame: the card's id. */
  pick(sx: number, sy: number): string | null {
    return this.pickEntry(sx, sy)?.id ?? null;
  }

  /** Hit-test the most recently drawn frame: the whole entry (an external's lane too). */
  pickEntry(sx: number, sy: number): PickEntry | null {
    return this.hasDrawn ? pickNode(this.lastStats, sx, sy) : null;
  }

  /** Hover the card under a client point; picked and painted on the next frame. */
  pointAt(clientX: number, clientY: number): void {
    this.pointer.move(clientX, clientY);
    this.schedule();
  }

  invalidate(): void {
    this.dirty = true;
    this.schedule();
  }

  private schedule(): void {
    if (this.rafId === null && typeof requestAnimationFrame !== "undefined") {
      this.rafId = requestAnimationFrame(this.frame);
    }
  }

  /** Pick once for the newest pointer position; a changed hover dirties this frame. */
  private resolvePointer(): void {
    const p = this.pointer.take();
    if (!p) return;
    const box = this.canvas.getBoundingClientRect();
    const sx = p.x - box.left;
    const sy = p.y - box.top;
    const hit = this.pickEntry(sx, sy);
    const id = hit?.id ?? null;
    if (id !== this.hoveredId) {
      this.hoveredId = id;
      this.dirty = true;
    }
    this.onHover?.(hit, sx, sy);
  }

  destroy(): void {
    if (this.rafId !== null && typeof cancelAnimationFrame !== "undefined") {
      cancelAnimationFrame(this.rafId);
    }
    this.rafId = null;
    this.pixelRatio.destroy();
  }

  private emitFocus(scene: ZoomScene): void {
    const moved = this.focus.update(scene, this.cam, this.vp);
    if (moved & RAILS_MOVED) this.invalidate();
    if (moved & FOCUS_MOVED) this.onFocus?.(this.focus.chain);
  }

  private emitView(): void {
    if (!this.onView || (this.cam === this.lastViewCam && this.vp === this.lastViewVp)) return;
    this.lastViewCam = this.cam;
    this.lastViewVp = this.vp;
    this.onView(this.cam, this.vp);
  }

  private readonly frame = (): void => {
    this.rafId = null;
    const t0 = now();
    this.resolvePointer();

    const animating = this.advanceTween(t0);

    const lowDetail = t0 < this.panUntil;
    const motion = animating || t0 < this.motionUntil;
    // The camera has settled since a reduced frame: repaint it at full quality.
    if (this.reduced && !lowDetail && !motion) this.dirty = true;

    if (this.dirty) {
      this.draw(lowDetail, motion);
      this.reduced = lowDetail || motion;
    }
    // Keep ticking (without drawing) while a fly runs or a reduced frame waits
    // for the settle repaint; an idle canvas schedules nothing.
    if (animating || this.reduced) this.schedule();
  };

  /** Step a running fly to time `t0`; true while it still has frames to go. */
  private advanceTween(t0: number): boolean {
    if (!this.tween) return false;
    this.dirty = true;
    const t = (t0 - this.tween.start) / this.tween.dur;
    if (t >= 1) {
      this.cam = this.tween.to;
      this.tween = null;
      return false;
    }
    this.cam = interpolateCamera(this.tween.from, this.tween.to, t);
    return true;
  }

  private draw(lowDetail: boolean, motion: boolean): void {
    this.dirty = false;
    if (!this.scene) return;
    const start = now();
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    this.hasDrawn = true;
    drawScene(this.ctx, this.scene, this.cam, this.vp, this.palette, {
      selectedId: this.selectedId,
      hoveredId: this.hoveredId,
      lowDetail,
      motion,
      paper: this.paper,
      relationVerbs: this.relationVerbs,
      highlightIds: this.highlightIds,
      showTestLinks: this.showTestLinks,
      lanes: this.focus.lanes,
    }, this.lastStats);
    if (this.onStats) {
      this.onStats({
        drawn: this.lastStats.drawn,
        culled: this.lastStats.culled,
        maxDepthDrawn: this.lastStats.maxDepthDrawn,
        frameMs: now() - start,
      });
    }
    this.emitFocus(this.scene);
    this.emitView();
    // The cards moved under a resting pointer: pick again against this frame.
    if (this.pointer.afterDraw(this.cam, this.vp, this.scene)) this.schedule();
  }
}
