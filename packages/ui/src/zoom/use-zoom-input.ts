/**
 * The canvas's input layer: wheel-zoom, drag-pan, hover, click, double-click
 * and keyboard, as native listeners so wheel can `preventDefault` (React's
 * onWheel is passive). Every command goes through `ZoomActions`; only the
 * continuous gestures (wheel, drag, arrow pan) set the camera directly.
 */

import { type RefObject, useEffect } from "react";
import { type Camera, clampCamera, panByScreen, wheelPixels, zoomAbout } from "./camera";
import type { ExternalCard, ExternalLane } from "./externals";
import type { Direction } from "./navigation";
import type { ZoomRenderer } from "./renderer";
import type { ZoomNode } from "./types";
import type { ZoomActions } from "./zoom-actions";

const WHEEL_ZOOM_RATE = 0.0015;
const CLICK_SLOP_PX = 4;
const KEY_PAN_PX = 80;
/** One +/- key (or zoom button) step. */
export const KEY_ZOOM_FACTOR = 1.35;

const ARROW_DIRECTION: Record<string, Direction> = {
  ArrowLeft: "left",
  ArrowRight: "right",
  ArrowUp: "up",
  ArrowDown: "down",
};
const PAN_STEP: Record<Direction, readonly [number, number]> = {
  left: [KEY_PAN_PX, 0],
  right: [-KEY_PAN_PX, 0],
  up: [0, KEY_PAN_PX],
  down: [0, -KEY_PAN_PX],
};

/** Set the canvas cursor, skipping the style write when it is already that. */
export function setCursor(canvas: HTMLCanvasElement, cursor: string): void {
  if (canvas.style.cursor !== cursor) canvas.style.cursor = cursor;
}

/** What the pointer is over: a node, or an external card in a parent's rail. */
export type HoverState = { sx: number; sy: number } & (
  | { node: ZoomNode }
  | { card: ExternalCard; lane: ExternalLane }
);

/**
 * The listeners for one canvas. Handlers read `renderer()` live (never a
 * captured instance) so they stay correct if the renderer is recreated, and a
 * drag resets cleanly on pointercancel.
 */
class ZoomInput {
  private dragging = false;
  private moved = 0;
  private lastX = 0;
  private lastY = 0;
  private readonly keyCommands: Record<string, () => void>;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly renderer: () => ZoomRenderer | null,
    private readonly actions: ZoomActions,
    private readonly hideHover: () => void,
  ) {
    this.keyCommands = {
      "+": () => actions.zoomBy(KEY_ZOOM_FACTOR),
      "=": () => actions.zoomBy(KEY_ZOOM_FACTOR),
      "-": () => actions.zoomBy(1 / KEY_ZOOM_FACTOR),
      _: () => actions.zoomBy(1 / KEY_ZOOM_FACTOR),
      "0": () => actions.fit(),
      Enter: actions.enter,
    };
  }

  private readonly listeners: [string, (e: never) => void][] = [
    ["pointerdown", this.onPointerDown.bind(this)],
    ["pointermove", this.onPointerMove.bind(this)],
    ["pointerup", this.onPointerUp.bind(this)],
    ["pointercancel", this.endDrag.bind(this)],
    ["pointerleave", this.onPointerLeave.bind(this)],
    ["dblclick", this.onDoubleClick.bind(this)],
    ["keydown", this.onKeyDown.bind(this)],
  ];
  private readonly wheel = this.onWheel.bind(this);

  attach(): void {
    this.canvas.addEventListener("wheel", this.wheel, { passive: false });
    for (const [type, fn] of this.listeners) this.canvas.addEventListener(type, fn as EventListener);
  }

  detach(): void {
    this.canvas.removeEventListener("wheel", this.wheel);
    for (const [type, fn] of this.listeners) this.canvas.removeEventListener(type, fn as EventListener);
  }

  private localPoint(e: { clientX: number; clientY: number }) {
    const rect = this.canvas.getBoundingClientRect();
    return { sx: e.clientX - rect.left, sy: e.clientY - rect.top };
  }

  private clearHover(): void {
    this.hideHover();
    this.renderer()?.setHovered(null);
  }

  private endDrag(e: PointerEvent): void {
    this.dragging = false;
    setCursor(this.canvas, "grab");
    if (this.canvas.hasPointerCapture(e.pointerId)) this.canvas.releasePointerCapture(e.pointerId);
  }

  private onWheel(e: WheelEvent): void {
    e.preventDefault();
    const r = this.renderer();
    if (!r) return;
    const { sx, sy } = this.localPoint(e);
    const factor = Math.exp(-wheelPixels(e, r.getViewport().h) * WHEEL_ZOOM_RATE);
    r.setCamera(clampCamera(zoomAbout(r.getCamera(), r.getViewport(), sx, sy, factor)));
  }

  private onPointerDown(e: PointerEvent): void {
    this.actions.cancelReveal();
    this.dragging = true;
    this.moved = 0;
    this.lastX = e.clientX;
    this.lastY = e.clientY;
    this.canvas.setPointerCapture(e.pointerId);
    setCursor(this.canvas, "grabbing");
    this.clearHover();
  }

  private onPointerMove(e: PointerEvent): void {
    const r = this.renderer();
    if (!r) return;
    if (this.dragging) this.drag(r, e);
    // The renderer picks once per frame for the newest point and reports the
    // hovered card through its `onHover` (see `ZoomRenderer.pointAt`).
    else r.pointAt(e.clientX, e.clientY);
  }

  private drag(r: ZoomRenderer, e: PointerEvent): void {
    const dx = e.clientX - this.lastX;
    const dy = e.clientY - this.lastY;
    this.moved += Math.abs(dx) + Math.abs(dy);
    this.lastX = e.clientX;
    this.lastY = e.clientY;
    const cam: Camera = clampCamera(panByScreen(r.getCamera(), dx, dy));
    r.setCamera(cam);
    r.markPanFrame();
  }

  private onPointerUp(e: PointerEvent): void {
    if (this.dragging && this.moved <= CLICK_SLOP_PX) {
      const { sx, sy } = this.localPoint(e);
      this.actions.selectAt(sx, sy);
    }
    this.endDrag(e);
  }

  private onPointerLeave(): void {
    this.clearHover();
  }

  private onDoubleClick(e: MouseEvent): void {
    const { sx, sy } = this.localPoint(e);
    // Double-click dives into whatever card is under the cursor (a single
    // click just selects); empty space is ignored so a stray double-click
    // never yanks the camera.
    this.actions.diveAt(sx, sy);
  }

  private panStep(dir: Direction): void {
    const r = this.renderer();
    if (!r) return;
    const [dx, dy] = PAN_STEP[dir];
    r.setCamera(clampCamera(panByScreen(r.getCamera(), dx, dy)));
  }

  private onKeyDown(e: KeyboardEvent): void {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "Escape") {
      // Not prevented: Escape may still mean something to an enclosing dialog.
      this.hideHover();
      this.actions.escape();
      return;
    }
    const dir = ARROW_DIRECTION[e.key];
    const command = this.keyCommands[e.key];
    if (!dir && !command) return;
    e.preventDefault();
    // With a card selected the arrows walk its siblings; otherwise they pan.
    if (!dir) command!();
    else if (!this.actions.step(dir)) this.panStep(dir);
  }
}

export function useZoomInput(
  canvasRef: RefObject<HTMLCanvasElement | null>,
  rendererRef: RefObject<ZoomRenderer | null>,
  actions: ZoomActions,
  hideHover: () => void,
): void {
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const input = new ZoomInput(canvas, () => rendererRef.current, actions, hideHover);
    input.attach();
    return () => input.detach();
  }, [canvasRef, rendererRef, actions, hideHover]);
}
