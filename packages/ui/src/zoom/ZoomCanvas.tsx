"use client";

/**
 * React host for the continuous-zoom canvas. Owns the `<canvas>`, the
 * `ZoomRenderer` instance, and the interaction layer: wheel-zoom, drag-pan,
 * keyboard pan/zoom, hover popover and click-to-select. It is a reusable
 * primitive: navigation chrome (breadcrumb, search box, detail panel, URL sync)
 * lives in the host page and drives the canvas through the imperative handle
 * (`flyTo`, `reset`) and the `onFocusChange` callback, so web and hosted share
 * the exact same canvas.
 */

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import { type Camera, clampCamera, fitRoot, panByScreen, zoomAbout } from "./camera";
import { type FlyOptions, ZoomRenderer, type FrameStats } from "./renderer";
import { buildScene } from "./scene";
import { resolveZoomPalette } from "./theme";
import type { ZoomMap, ZoomNode } from "./types";
import { useThemeVersion } from "../shared/use-theme-tokens";

export interface ZoomCanvasHandle {
  /** Fly the camera to frame a node by id. Returns false if it is not laid out. */
  flyTo: (id: string, opts?: FlyOptions) => boolean;
  /** Fly back to the whole-system overview. */
  reset: (opts?: FlyOptions) => void;
}

export interface ZoomCanvasProps {
  data: ZoomMap;
  className?: string;
  onSelect?: (node: ZoomNode | null) => void;
  /** Fires with the root -> focus chain whenever the focused node changes. */
  onFocusChange?: (chain: ZoomNode[]) => void;
  /** Node id to jump to on first render (e.g. from a shared URL). */
  initialFocusId?: string;
  /** Show a small live frame-stat overlay (drawn/culled/fps). Dev aid. */
  showStats?: boolean;
}

const WHEEL_ZOOM_RATE = 0.0015;
const CLICK_SLOP_PX = 4;
const KEY_PAN_PX = 80;
const KEY_ZOOM_FACTOR = 1.35;

interface HoverState {
  node: ZoomNode;
  sx: number;
  sy: number;
}

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

export const ZoomCanvas = forwardRef<ZoomCanvasHandle, ZoomCanvasProps>(function ZoomCanvas(
  { data, className, onSelect, onFocusChange, initialFocusId, showStats },
  ref,
) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rendererRef = useRef<ZoomRenderer | null>(null);
  const themeVersion = useThemeVersion();
  const reducedMotion = usePrefersReducedMotion();
  const reducedRef = useRef(reducedMotion);
  reducedRef.current = reducedMotion;

  const scene = useMemo(() => buildScene(data), [data]);
  // Node index is derived from the data (stable identity) so the interaction
  // listeners are not torn down when the scene is rebuilt.
  const nodeById = useMemo(
    () => new Map(data.nodes.map((n) => [n.id, n] as const)),
    [data],
  );

  const [hover, setHover] = useState<HoverState | null>(null);
  const [stats, setStats] = useState<FrameStats | null>(null);

  // Keep the latest callbacks in refs so the renderer and the native event
  // listeners (each created once) always call the current closure without being
  // torn down and recreated on every render.
  const onFocusRef = useRef(onFocusChange);
  onFocusRef.current = onFocusChange;
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const onStatsRef = useRef<((s: FrameStats) => void) | undefined>(undefined);
  onStatsRef.current = showStats ? setStats : undefined;

  useImperativeHandle(
    ref,
    (): ZoomCanvasHandle => ({
      flyTo: (id, opts) =>
        rendererRef.current?.frameNode(id, { reducedMotion: reducedRef.current, ...opts }) ?? false,
      reset: (opts) => {
        const r = rendererRef.current;
        if (!r) return;
        r.animateTo(fitRoot(r.getViewport()), { reducedMotion: reducedRef.current, ...opts });
      },
    }),
    [],
  );

  // Create the renderer once the canvas mounts.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const renderer = new ZoomRenderer({
      canvas,
      palette: resolveZoomPalette(),
      onStats: (s) => onStatsRef.current?.(s),
      onFocus: (chain) => onFocusRef.current?.(chain),
    });
    rendererRef.current = renderer;

    const resizeObs = new ResizeObserver(() => renderer.resize());
    resizeObs.observe(canvas);

    return () => {
      resizeObs.disconnect();
      renderer.destroy();
      rendererRef.current = null;
    };
  }, []);

  // Feed the scene + repaint on data change.
  useEffect(() => {
    rendererRef.current?.setScene(scene);
  }, [scene]);

  // One-shot: jump to the URL-provided focus node once the canvas has done its
  // initial fit (it needs a real viewport size before a node rect can be framed).
  const initialAppliedRef = useRef(false);
  useEffect(() => {
    if (!initialFocusId || initialAppliedRef.current) return;
    let raf = 0;
    const tryApply = () => {
      const r = rendererRef.current;
      if (r?.isFramed()) {
        if (r.frameNode(initialFocusId, { reducedMotion: true })) initialAppliedRef.current = true;
      } else {
        raf = requestAnimationFrame(tryApply);
      }
    };
    raf = requestAnimationFrame(tryApply);
    return () => cancelAnimationFrame(raf);
  }, [initialFocusId, scene]);

  // Re-resolve theme colors on light/dark switch.
  useEffect(() => {
    rendererRef.current?.setPalette(resolveZoomPalette());
  }, [themeVersion]);

  // Interaction: wheel-zoom + drag-pan + hover + click + keyboard. Native
  // listeners so we can call preventDefault on wheel (React onWheel is passive).
  // Handlers read `rendererRef.current` live (never a captured instance) so they
  // stay correct if the renderer is recreated, and reset cleanly on pointercancel.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const localPoint = (e: { clientX: number; clientY: number }) => {
      const rect = canvas.getBoundingClientRect();
      return { sx: e.clientX - rect.left, sy: e.clientY - rect.top };
    };

    let dragging = false;
    let moved = 0;
    let last = { x: 0, y: 0 };

    const endDrag = (e: PointerEvent) => {
      dragging = false;
      canvas.style.cursor = "grab";
      if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const renderer = rendererRef.current;
      if (!renderer) return;
      const { sx, sy } = localPoint(e);
      const factor = Math.exp(-e.deltaY * WHEEL_ZOOM_RATE);
      const next = clampCamera(zoomAbout(renderer.getCamera(), renderer.getViewport(), sx, sy, factor));
      renderer.setCamera(next);
    };

    const onPointerDown = (e: PointerEvent) => {
      dragging = true;
      moved = 0;
      last = { x: e.clientX, y: e.clientY };
      canvas.setPointerCapture(e.pointerId);
      canvas.style.cursor = "grabbing";
      setHover(null);
      rendererRef.current?.setHovered(null);
    };

    const onPointerMove = (e: PointerEvent) => {
      const renderer = rendererRef.current;
      if (!renderer) return;
      if (dragging) {
        const dx = e.clientX - last.x;
        const dy = e.clientY - last.y;
        moved += Math.abs(dx) + Math.abs(dy);
        last = { x: e.clientX, y: e.clientY };
        const cam: Camera = clampCamera(panByScreen(renderer.getCamera(), dx, dy));
        renderer.setCamera(cam);
        renderer.markPanFrame();
        return;
      }
      const { sx, sy } = localPoint(e);
      const id = renderer.pick(sx, sy);
      renderer.setHovered(id);
      const node = id ? nodeById.get(id) : undefined;
      setHover(node ? { node, sx, sy } : null);
      canvas.style.cursor = node ? "pointer" : "grab";
    };

    const selectAt = (sx: number, sy: number) => {
      const renderer = rendererRef.current;
      if (!renderer) return;
      const id = renderer.pick(sx, sy);
      const node = id ? (nodeById.get(id) ?? null) : null;
      renderer.setSelected(node?.id ?? null);
      onSelectRef.current?.(node);
    };

    const onPointerUp = (e: PointerEvent) => {
      if (dragging && moved <= CLICK_SLOP_PX) {
        const { sx, sy } = localPoint(e);
        selectAt(sx, sy);
      }
      endDrag(e);
    };

    const onPointerCancel = (e: PointerEvent) => endDrag(e);
    const onPointerLeave = () => {
      setHover(null);
      rendererRef.current?.setHovered(null);
    };

    const onKeyDown = (e: KeyboardEvent) => {
      const renderer = rendererRef.current;
      if (!renderer) return;
      const vp = renderer.getViewport();
      const cam = renderer.getCamera();
      switch (e.key) {
        case "ArrowLeft":
          renderer.setCamera(clampCamera(panByScreen(cam, KEY_PAN_PX, 0)));
          break;
        case "ArrowRight":
          renderer.setCamera(clampCamera(panByScreen(cam, -KEY_PAN_PX, 0)));
          break;
        case "ArrowUp":
          renderer.setCamera(clampCamera(panByScreen(cam, 0, KEY_PAN_PX)));
          break;
        case "ArrowDown":
          renderer.setCamera(clampCamera(panByScreen(cam, 0, -KEY_PAN_PX)));
          break;
        case "+":
        case "=":
          renderer.setCamera(clampCamera(zoomAbout(cam, vp, vp.w / 2, vp.h / 2, KEY_ZOOM_FACTOR)));
          break;
        case "-":
        case "_":
          renderer.setCamera(clampCamera(zoomAbout(cam, vp, vp.w / 2, vp.h / 2, 1 / KEY_ZOOM_FACTOR)));
          break;
        case "Enter":
          selectAt(vp.w / 2, vp.h / 2);
          break;
        case "Escape":
          renderer.setSelected(null);
          onSelectRef.current?.(null);
          setHover(null);
          return; // do not preventDefault on Escape
        default:
          return;
      }
      e.preventDefault();
    };

    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("pointercancel", onPointerCancel);
    canvas.addEventListener("pointerleave", onPointerLeave);
    canvas.addEventListener("keydown", onKeyDown);
    return () => {
      canvas.removeEventListener("wheel", onWheel);
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
      canvas.removeEventListener("pointerup", onPointerUp);
      canvas.removeEventListener("pointercancel", onPointerCancel);
      canvas.removeEventListener("pointerleave", onPointerLeave);
      canvas.removeEventListener("keydown", onKeyDown);
    };
  }, [nodeById]);

  return (
    <div className={`relative h-full w-full overflow-hidden ${className ?? ""}`}>
      <canvas
        ref={canvasRef}
        tabIndex={0}
        role="application"
        aria-label="Zoomable system map. Drag to pan, scroll to zoom, arrow keys to pan, plus and minus to zoom, Enter to open the centre node."
        className="h-full w-full touch-none outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
        style={{ cursor: "grab" }}
      />
      {hover && <HoverCard hover={hover} canvas={canvasRef.current} />}
      {showStats && stats && <StatsOverlay stats={stats} laidOut={scene.laidOutCount} />}
    </div>
  );
});

const HOVER_KIND_LABEL: Record<ZoomNode["kind"], string> = {
  system: "System",
  layer: "Layer",
  group: "Group",
  folder: "Folder",
  file: "File",
};

/**
 * A deliberately light tooltip: kind, name, path and a one-line summary. The
 * full metric breakdown lives in the side panel (open on click), so the hover
 * stays calm and premium instead of dumping numbers.
 */
function HoverCard({ hover, canvas }: { hover: HoverState; canvas: HTMLCanvasElement | null }) {
  const { node } = hover;
  // Flip the card toward the interior when the cursor is near the right/bottom
  // edge, so it never spills off-canvas (N2).
  const w = canvas?.clientWidth ?? 0;
  const h = canvas?.clientHeight ?? 0;
  const flipX = w > 0 && hover.sx > w * 0.66;
  const flipY = h > 0 && hover.sy > h * 0.66;
  const tx = flipX ? "calc(-100% - 14px)" : "14px";
  const ty = flipY ? "calc(-100% - 14px)" : "14px";
  return (
    <div
      className="pointer-events-none absolute z-10 max-w-[16rem] rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-3 py-2 text-xs shadow-xl"
      style={{ left: hover.sx, top: hover.sy, transform: `translate(${tx}, ${ty})` }}
    >
      <div className="text-[10px] font-medium uppercase tracking-wide text-[var(--color-text-tertiary)]">
        {HOVER_KIND_LABEL[node.kind]}
      </div>
      <div className="mt-0.5 font-semibold text-[var(--color-text-primary)]">{node.name}</div>
      {node.path && node.path !== node.name && (
        <div className="truncate text-[var(--color-text-tertiary)]">{node.path}</div>
      )}
      {node.summary && (
        <div className="mt-1 line-clamp-2 text-[var(--color-text-secondary)]">{node.summary}</div>
      )}
    </div>
  );
}

function StatsOverlay({ stats, laidOut }: { stats: FrameStats; laidOut: number }) {
  return (
    <div className="pointer-events-none absolute bottom-2 left-2 z-10 rounded bg-[var(--color-bg-overlay)] px-2 py-1 font-mono text-[10px] text-[var(--color-text-secondary)]">
      {stats.drawn} drawn · {stats.culled} culled · depth {stats.maxDepthDrawn} ·{" "}
      {stats.frameMs.toFixed(2)}ms · {laidOut} nodes
    </div>
  );
}
