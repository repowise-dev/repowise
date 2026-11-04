"use client";

/**
 * Overview minimap for the zoom canvas: the root's top two levels and a
 * rectangle for what the camera shows. Click or drag to move the camera there.
 *
 * Cheap by construction. The overview is painted once into an offscreen canvas
 * (again only on a new scene, theme or pixel ratio), and a camera change only
 * blits that bitmap and strokes one rect, called imperatively by the renderer's
 * view callback so camera motion never re-renders React.
 */

import { type RefObject, forwardRef, memo, useCallback, useEffect, useImperativeHandle, useMemo, useRef } from "react";
import type { Camera, Rect, Viewport, WorldPoint } from "./camera";
import {
  type MinimapFrame,
  minimapFrame,
  minimapToWorld,
  minimapViewRect,
  worldToMinimapRect,
} from "./minimap";
import { sceneRootRect } from "./relayout";
import type { ZoomScene } from "./scene";
import { type ZoomPalette, resolveZoomPalette } from "./theme";
import { useThemeVersion } from "../shared/use-theme-tokens";

export interface ZoomMinimapHandle {
  /** Repaint the viewport rect for this camera. */
  drawView: (cam: Camera, vp: Viewport) => void;
}

interface ZoomMinimapProps {
  scene: ZoomScene;
  /** Move the camera centre to this world point. */
  onNavigate: (point: WorldPoint) => void;
  className?: string;
  /** Accessible name; the host passes a translated one. */
  label?: string;
}

/** The minimap's longer side; it takes the overview's shape. */
const SIZE = 132;
const PAD = 6;

function paintOverview(
  ctx: CanvasRenderingContext2D,
  scene: ZoomScene,
  palette: ZoomPalette,
  frame: MinimapFrame,
) {
  ctx.fillStyle = palette.bg;
  ctx.fillRect(0, 0, frame.w, frame.h);
  const root = scene.nodes.get(scene.rootId);
  if (!root) return;
  ctx.lineWidth = 1;
  ctx.strokeStyle = palette.nodeBorder;
  for (const childId of root.children) {
    const r = scene.worldRects.get(childId);
    if (!r) continue;
    const m = worldToMinimapRect(r, frame);
    ctx.fillStyle = palette.nodeFillAlt;
    ctx.fillRect(m.x, m.y, m.w, m.h);
    ctx.strokeRect(m.x + 0.5, m.y + 0.5, Math.max(0, m.w - 1), Math.max(0, m.h - 1));
    // The second level as faint blocks, enough to recognise where you are.
    ctx.fillStyle = palette.gridStrong;
    for (const grandId of scene.nodes.get(childId)?.children ?? []) {
      const g = scene.worldRects.get(grandId);
      if (!g) continue;
      const gm = worldToMinimapRect(g, frame);
      ctx.fillRect(gm.x, gm.y, gm.w, gm.h);
    }
  }
}

/** Blit the overview and stroke the viewport rect into `mark`'s reused rect. */
function paintView(
  canvas: HTMLCanvasElement,
  overview: HTMLCanvasElement,
  palette: ZoomPalette,
  cam: Camera,
  vp: Viewport,
  mark: Rect,
  frame: MinimapFrame,
) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const dpr = canvas.width / frame.w;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.drawImage(overview, 0, 0);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const m = minimapViewRect(cam, vp, frame, mark);
  ctx.strokeStyle = palette.accent;
  ctx.lineWidth = 1.5;
  ctx.strokeRect(m.x + 0.75, m.y + 0.75, Math.max(0, m.w - 1.5), Math.max(0, m.h - 1.5));
}

/** Size both canvases to the pixel ratio and paint the static overview. */
function paintStatic(
  canvas: HTMLCanvasElement,
  overview: HTMLCanvasElement,
  scene: ZoomScene,
  frame: MinimapFrame,
) {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = overview.width = Math.round(frame.w * dpr);
  canvas.height = overview.height = Math.round(frame.h * dpr);
  const palette = resolveZoomPalette();
  const octx = overview.getContext("2d");
  if (octx) {
    octx.setTransform(dpr, 0, 0, dpr, 0, 0);
    paintOverview(octx, scene, palette, frame);
  }
  return palette;
}

/** Click or drag on the minimap to move the camera centre there. */
function useMinimapDrag(
  canvasRef: RefObject<HTMLCanvasElement | null>,
  onNavigateRef: RefObject<(point: WorldPoint) => void>,
  frame: MinimapFrame,
) {
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let dragging = false;
    const go = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      const k = frame.w / Math.max(1, rect.width);
      onNavigateRef.current(minimapToWorld((e.clientX - rect.left) * k, (e.clientY - rect.top) * k, frame));
    };
    const down = (e: PointerEvent) => {
      dragging = true;
      canvas.setPointerCapture(e.pointerId);
      go(e);
    };
    const move = (e: PointerEvent) => {
      if (dragging) go(e);
    };
    const up = (e: PointerEvent) => {
      dragging = false;
      if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
    };
    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("pointercancel", up);
    return () => {
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      canvas.removeEventListener("pointercancel", up);
    };
  }, [canvasRef, onNavigateRef, frame]);
}

const MinimapCanvas = forwardRef<ZoomMinimapHandle, ZoomMinimapProps>(function ZoomMinimap(
  { scene, onNavigate, className, label = "Overview map. Click or drag to move the view." },
  ref,
) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const overviewRef = useRef<HTMLCanvasElement | null>(null);
  const paletteRef = useRef<ZoomPalette | null>(null);
  const camRef = useRef<Camera | null>(null);
  const vpRef = useRef<Viewport | null>(null);
  // Reused every frame so a camera fly allocates nothing here.
  const markRef = useRef<Rect>({ x: 0, y: 0, w: 0, h: 0 });
  const themeVersion = useThemeVersion();
  const onNavigateRef = useRef(onNavigate);
  onNavigateRef.current = onNavigate;
  const frame = useMemo(() => minimapFrame(sceneRootRect(scene), SIZE, PAD), [scene]);
  const frameRef = useRef(frame);
  frameRef.current = frame;

  const drawView = useCallback((cam: Camera, vp: Viewport) => {
    camRef.current = cam;
    vpRef.current = vp;
    const canvas = canvasRef.current;
    const overview = overviewRef.current;
    const palette = paletteRef.current;
    if (canvas && overview && palette) {
      paintView(canvas, overview, palette, cam, vp, markRef.current, frameRef.current);
    }
  }, []);

  useImperativeHandle(ref, () => ({ drawView }), [drawView]);

  // The static overview: once per scene / theme, never per camera move.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const overview = overviewRef.current ?? document.createElement("canvas");
    overviewRef.current = overview;
    paletteRef.current = paintStatic(canvas, overview, scene, frame);
    if (camRef.current && vpRef.current) drawView(camRef.current, vpRef.current);
  }, [scene, frame, themeVersion, drawView]);

  useMinimapDrag(canvasRef, onNavigateRef, frame);

  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label={label}
      className={`touch-none cursor-pointer rounded-md border border-[var(--color-border-default)] shadow-sm ${className ?? ""}`}
      style={{ width: frame.w, height: frame.h }}
    />
  );
});

/** Memoised: its props are stable, so a canvas re-render (a hover) never repaints it. */
export const ZoomMinimap = memo(MinimapCanvas);
