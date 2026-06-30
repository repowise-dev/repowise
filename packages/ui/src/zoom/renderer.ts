/**
 * The canvas render loop: a thin, framework-agnostic shell around the pure math
 * and the recursive draw. Browser code.
 *
 * Dirty-flag `requestAnimationFrame`: the loop only paints when something
 * actually changed (`invalidate()`), then stops, so an idle canvas costs zero
 * CPU. A running camera fly (`animateTo` / `frameNode`) keeps the loop alive for
 * its duration and then settles. The backing store is sized to `devicePixelRatio`
 * for crisp text, and a short low-detail window after a pan suppresses
 * labels/relations so dragging stays smooth. A WebGL upgrade path is open: the
 * draw layer is isolated behind `drawScene`, so a future GPU backend can replace
 * it without touching camera, culling or scene code.
 */

import { type Camera, type Viewport, fitRoot, frameRect } from "./camera";
import { flyDuration, interpolateCamera } from "./camera-anim";
import { type DrawStats, drawScene, pickNode } from "./draw-tree";
import { focusChain } from "./focus-path";
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
}

const PAN_LOW_DETAIL_FRAMES = 2;

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

  private scene: ZoomScene | null = null;
  private cam: Camera = { cx: 0.5, cy: 0.5, scale: 600 };
  private vp: Viewport = { w: 1, h: 1 };
  private dpr = 1;
  private hasFramedScene = false;

  private selectedId: string | null = null;
  private hoveredId: string | null = null;
  private lowDetailFrames = 0;
  private tween: Tween | null = null;
  private lastFocusId: string | null = null;
  private dirty = true;
  private rafId: number | null = null;
  private lastStats: DrawStats | null = null;

  constructor(opts: ZoomRendererOptions) {
    this.canvas = opts.canvas;
    const ctx = opts.canvas.getContext("2d");
    if (!ctx) throw new Error("ZoomRenderer: 2D canvas context unavailable");
    this.ctx = ctx;
    this.palette = opts.palette;
    this.onStats = opts.onStats;
    this.onFocus = opts.onFocus;
    this.resize();
  }

  getCamera(): Camera {
    return this.cam;
  }

  getViewport(): Viewport {
    return this.vp;
  }

  /** Manual camera set (pan/zoom): cancels any running fly. */
  setCamera(cam: Camera): void {
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
    if (id === this.hoveredId) return;
    this.hoveredId = id;
    this.invalidate();
  }

  setScene(scene: ZoomScene): void {
    this.scene = scene;
    // Only frame once we have a real layout size; otherwise the first resize
    // callback (which delivers the true dimensions) does the initial fit.
    if (!this.hasFramedScene && this.vp.w > 1) {
      this.cam = fitRoot(this.vp);
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
      this.setCamera(target);
      return;
    }
    const dur = opts.durationMs ?? flyDuration(this.cam, target);
    if (dur <= 0) {
      this.setCamera(target);
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
    this.animateTo(frameRect(this.vp, rect, opts.fill ?? 0.72), opts);
    return true;
  }

  /** Re-read the element size and resize the backing store for crisp pixels. */
  resize(): void {
    const cssW = Math.max(1, this.canvas.clientWidth);
    const cssH = Math.max(1, this.canvas.clientHeight);
    const dpr = typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;
    this.dpr = dpr;
    this.vp = { w: cssW, h: cssH };
    this.canvas.width = Math.round(cssW * dpr);
    this.canvas.height = Math.round(cssH * dpr);
    // Deferred initial fit, once the element has a real size.
    if (this.scene && !this.hasFramedScene && cssW > 1) {
      this.cam = fitRoot(this.vp);
      this.hasFramedScene = true;
    }
    this.invalidate();
  }

  /** Flag the next pan window so labels/relations are suppressed briefly. */
  markPanFrame(): void {
    this.lowDetailFrames = PAN_LOW_DETAIL_FRAMES;
  }

  /** Hit-test the most recently drawn frame. */
  pick(sx: number, sy: number): string | null {
    return this.lastStats ? pickNode(this.lastStats, sx, sy) : null;
  }

  invalidate(): void {
    this.dirty = true;
    if (this.rafId === null && typeof requestAnimationFrame !== "undefined") {
      this.rafId = requestAnimationFrame(this.frame);
    }
  }

  destroy(): void {
    if (this.rafId !== null && typeof cancelAnimationFrame !== "undefined") {
      cancelAnimationFrame(this.rafId);
    }
    this.rafId = null;
  }

  private emitFocus(): void {
    if (!this.scene || !this.onFocus) return;
    const chain = focusChain(this.scene, this.cam, this.vp);
    const id = chain.length > 0 ? chain[chain.length - 1]!.id : this.scene.rootId;
    if (id !== this.lastFocusId) {
      this.lastFocusId = id;
      this.onFocus(chain);
    }
  }

  private readonly frame = (): void => {
    this.rafId = null;

    let animating = false;
    if (this.tween) {
      const t = (now() - this.tween.start) / this.tween.dur;
      if (t >= 1) {
        this.cam = this.tween.to;
        this.tween = null;
      } else {
        this.cam = interpolateCamera(this.tween.from, this.tween.to, t);
        animating = true;
      }
      this.dirty = true;
    }

    if (!this.dirty) return;

    const start = now();
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);

    if (this.scene) {
      const lowDetail = this.lowDetailFrames > 0;
      this.lastStats = drawScene(this.ctx, this.scene, this.cam, this.vp, this.palette, {
        selectedId: this.selectedId,
        hoveredId: this.hoveredId,
        lowDetail,
      });
      if (this.onStats) {
        this.onStats({
          drawn: this.lastStats.drawn,
          culled: this.lastStats.culled,
          maxDepthDrawn: this.lastStats.maxDepthDrawn,
          frameMs: now() - start,
        });
      }
      this.emitFocus();
    }

    this.dirty = false;
    if (this.lowDetailFrames > 0) this.lowDetailFrames--;
    // Keep the loop alive while a fly is in flight or a pan window is settling.
    if (animating || this.lowDetailFrames > 0) this.invalidate();
  };
}
