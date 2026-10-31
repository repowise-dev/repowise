/**
 * The canvas's camera and selection commands, shared by its keyboard, pointer
 * and imperative handle so a button, a key and a host call do the same thing.
 * Browser-free: it drives the renderer through the narrow surface below and
 * owns the selected id. Methods are arrow fields so they pass as callbacks.
 *
 * While a node is selected the host covers the canvas's right edge with a
 * sheet, so the selection also sets the renderer's `insetRight`: every frame,
 * fit and dive after that lands in the clear part beside the sheet.
 */

import { type Viewport, clampCamera, clearWidth, fitRoot, frameRect, panToReveal, zoomAbout } from "./camera";
import { externalImporters } from "./externals";
import { FRAME_FILL } from "./focus-path";
import { type Direction, siblingInDirection, upTarget } from "./navigation";
import { sameCamera, sceneRootRect } from "./relayout";
import type { FlyOptions, ZoomRenderer } from "./renderer";
import type { ZoomScene } from "./scene";
import type { ZoomNode } from "./types";

/** A zoom-button or +/- step is a short glide, not a full fly. */
const ZOOM_STEP_MS = 160;
/**
 * A click rings its card at once but tells the host (which opens the sheet)
 * only after this long, so the two clicks of a double-click dive without
 * leaving a sheet behind. Also when the card pans clear of the sheet.
 */
const POINTER_SETTLE_MS = 350;
/** Below this much clear width beside the sheet, revealing only shoves cards around. */
const MIN_CLEAR_PX = 240;

interface ZoomActionContext {
  renderer: () => ZoomRenderer | null;
  scene: () => ZoomScene;
  node: (id: string) => ZoomNode | undefined;
  /** Screen px the host covers on the right while a node is selected. */
  insetRight: () => number;
  reducedMotion: () => boolean;
  onSelect: (node: ZoomNode | null) => void;
}

/** When a selection reaches the host: "now", or after the double-click window. */
type RevealMode = "now" | "deferred" | "none";

export class ZoomActions {
  /** The ringed card. */
  private selectedId: string | null = null;
  /** The selection the host last heard about (its sheet shows this one). */
  private committedId: string | null = null;
  private commitTimer: ReturnType<typeof setTimeout> | null = null;
  /** The node a pending click selection will hand the host. */
  private pending: ZoomNode | null = null;
  /** A pending click selection still pans its card clear when it lands. */
  private revealPending = false;

  constructor(private readonly ctx: ZoomActionContext) {}

  /** A camera command or a new press: a pending click selection lands without panning. */
  cancelReveal = (): void => {
    this.revealPending = false;
  };

  private fly(extra?: FlyOptions): FlyOptions {
    return { reducedMotion: this.ctx.reducedMotion(), ...extra };
  }

  flyTo = (id: string, opts?: FlyOptions): boolean => {
    this.cancelReveal();
    return this.ctx.renderer()?.frameNode(id, this.fly(opts)) ?? false;
  };

  fit = (opts?: FlyOptions): void => {
    this.cancelReveal();
    const r = this.ctx.renderer();
    if (r) r.animateTo(fitRoot(r.getViewport(), sceneRootRect(this.ctx.scene())), this.fly(opts));
  };

  /** Zoom about the centre of the clear area, stepping from where a running glide ends. */
  zoomBy = (factor: number): void => {
    this.cancelReveal();
    const r = this.ctx.renderer();
    if (!r) return;
    const vp = r.getViewport();
    const target = clampCamera(zoomAbout(r.getTargetCamera(), vp, clearWidth(vp) / 2, vp.h / 2, factor));
    r.animateTo(target, this.fly({ durationMs: ZOOM_STEP_MS }));
  };

  up = (): void => {
    const r = this.ctx.renderer();
    const id = r ? upTarget(this.ctx.scene(), r.getFocusChain(), r.getViewport()) : null;
    if (id === null || !this.flyTo(id)) this.fit();
  };

  /** The sheet's width while `id` is selected, or 0 when it would leave too little beside it. */
  private insetFor(id: string | null, w: number): number {
    const inset = this.ctx.insetRight();
    return id !== null && inset > 0 && w - inset >= MIN_CLEAR_PX ? inset : 0;
  }

  /**
   * The camera that frames the focus node (or fits the root) in `vp`: what a
   * dive, Up or fit leaves the camera on.
   */
  private focusFrame(vp: Viewport): ReturnType<typeof frameRect> | null {
    const r = this.ctx.renderer();
    const focus = r?.getFocusChain().at(-1);
    const scene = this.ctx.scene();
    if (!focus || focus.id === scene.rootId) return fitRoot(vp, sceneRootRect(scene));
    const rect = scene.worldRects.get(focus.id);
    return rect ? frameRect(vp, rect, FRAME_FILL) : null;
  }

  /**
   * Cover `px` on the right. Opening the sheet moves nothing (a selected card
   * pans clear on its own, see `reveal`); closing it re-frames a view that was
   * framed beside it, so the wider canvas does not leave it short.
   */
  private setInset(px: number): void {
    const r = this.ctx.renderer();
    if (!r) return;
    const before = r.getViewport();
    if (px === (before.insetRight ?? 0)) return;
    const was = px < (before.insetRight ?? 0) ? this.focusFrame(before) : null;
    const framed = was !== null && sameCamera(r.getTargetCamera(), was);
    r.setInsetRight(px);
    const next = framed ? this.focusFrame(r.getViewport()) : null;
    if (next) r.animateTo(next, this.fly());
  }

  /** After a canvas resize: drop the sheet's strip when it would leave too little beside it. */
  refitInset = (): void => {
    const r = this.ctx.renderer();
    if (r) r.setInsetRight(this.insetFor(this.committedId, r.getViewport().w));
  };

  /** Drop a pending click selection's timer (the canvas unmounting). */
  dispose = (): void => {
    this.cancelCommit();
  };

  /**
   * Pan the node into the clear viewport. A pointer selection (`always` unset)
   * only moves sideways, out from under the host's right-edge sheet: the card
   * was under the pointer, so a vertical jump would only pull it away.
   */
  private reveal(id: string, always = false): void {
    const r = this.ctx.renderer();
    const rect = this.ctx.scene().worldRects.get(id);
    if (!r || !rect) return;
    const vp = r.getViewport();
    const inset = vp.insetRight ?? 0;
    if (inset <= 0 && !always) return;
    const from = r.getTargetCamera();
    const to = panToReveal(from, vp, rect, inset);
    if (to.cx === from.cx && (!always || to.cy === from.cy)) return;
    r.animateTo(clampCamera(always ? to : { ...to, cy: from.cy }), this.fly());
  }

  private cancelCommit(): void {
    if (this.commitTimer !== null) clearTimeout(this.commitTimer);
    this.commitTimer = null;
    this.pending = null;
  }

  /** Tell the host, cover the sheet's strip, and pan the card clear when asked. */
  private commit(node: ZoomNode | null, reveal: boolean): void {
    this.cancelCommit();
    const r = this.ctx.renderer();
    this.committedId = node?.id ?? null;
    if (r) this.setInset(this.insetFor(this.committedId, r.getViewport().w));
    this.ctx.onSelect(node);
    if (node && reveal) this.reveal(node.id);
  }

  select = (id: string | null, reveal: RevealMode = "now"): void => {
    this.cancelCommit();
    const node = id ? (this.ctx.node(id) ?? null) : null;
    this.selectedId = node?.id ?? null;
    this.ctx.renderer()?.setSelected(this.selectedId);
    if (reveal !== "deferred") {
      this.commit(node, reveal === "now");
      return;
    }
    this.revealPending = true;
    this.pending = node;
    this.commitTimer = setTimeout(() => this.commit(node, this.revealPending), POINTER_SETTLE_MS);
  };

  /** A node id under a screen point; undefined for an external card, which is not selectable. */
  private cardAt(sx: number, sy: number): string | null | undefined {
    const id = this.ctx.renderer()?.pick(sx, sy) ?? null;
    return id && !this.ctx.node(id) ? undefined : id;
  }

  selectAt = (sx: number, sy: number): void => {
    const id = this.cardAt(sx, sy);
    // Clicking an external card keeps the selection.
    if (id !== undefined) this.select(id, "deferred");
  };

  /**
   * Double-click: dive into the card under the point, or for an external card
   * into the child here that imports it most (its rail shows the external
   * again, so repeated double-clicks walk down to the folder using it). Clicks
   * still inside the double-click window select nothing: the selection goes
   * back to what the host already shows.
   */
  diveAt = (sx: number, sy: number): void => {
    if (this.commitTimer !== null) {
      this.cancelCommit();
      this.selectedId = this.committedId;
      this.ctx.renderer()?.setSelected(this.selectedId);
    }
    const hit = this.ctx.renderer()?.pickEntry(sx, sy);
    if (!hit) return;
    const id = hit.lane ? externalImporters(hit.lane, hit.id)[0]?.sourceId : hit.id;
    if (id) this.flyTo(id);
  };

  /** After new data: drop a selection whose node is gone, else hand the host the fresh node. */
  resync = (): void => {
    if (this.selectedId) this.select(this.selectedId, "none");
  };

  /** Move the selection to a sibling and pan it into view. False when nothing is selected. */
  step = (dir: Direction): boolean => {
    if (!this.selectedId) return false;
    const next = siblingInDirection(this.ctx.scene(), this.selectedId, dir);
    if (next) {
      this.select(next, "none");
      this.reveal(next, true);
    }
    return true;
  };

  /** Enter dives into the selection, or selects the card at the centre of the clear area. */
  enter = (): void => {
    const r = this.ctx.renderer();
    // A click still waiting out the double-click window lands first, so the
    // dive frames beside the sheet it opens.
    if (this.commitTimer !== null) this.commit(this.pending, false);
    if (this.selectedId) {
      this.flyTo(this.selectedId);
      return;
    }
    if (!r) return;
    const vp = r.getViewport();
    const id = this.cardAt(clearWidth(vp) / 2, vp.h / 2);
    if (id) this.select(id);
  };

  /** Escape closes the selection first, then climbs a level. */
  escape = (): void => {
    if (this.selectedId) this.select(null);
    else this.up();
  };
}
