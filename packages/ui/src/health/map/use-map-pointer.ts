"use client";

/**
 * The pointer on the field: hover, click, drag to pan, pinch and wheel to zoom.
 *
 * Picking is a grid lookup, so it runs on every move. The hovered file is
 * mirrored in a ref, so a move over the same node is that lookup and nothing
 * else: React hears about hover only when it changes.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  MouseEvent as ReactMouseEvent,
  MutableRefObject,
  PointerEvent as ReactPointerEvent,
  RefObject,
} from "react";

import { panByScreen, screenToWorld, zoomAbout } from "../../zoom/camera";
import { discAt } from "./area-view";
import { hitTest, type HitGrid } from "./hit-test";
import type { FieldScene } from "./paint";
import type { CodeHealthMapFile } from "./types";
import { MIN_NODE_PX, type Invalidate, type LoopState } from "./use-field-canvas";
import { clampCam } from "./use-map-camera";

/** Pointer slop for picking a node, in px. */
const HIT_SLOP_PX = 3;
/** Movement before a press counts as a drag rather than a click. */
const DRAG_PX = 4;

/** What the pointer is over: a file dot, or an area's disc. */
export type Hover =
  | { kind: "file"; file: CodeHealthMapFile; x: number; y: number }
  | { kind: "area"; path: string; slot: boolean; x: number; y: number };

interface Point {
  x: number;
  y: number;
}

interface Drag extends Point {
  moved: boolean;
  /** Distance between two fingers, 0 when not pinching. */
  pinch: number;
}

const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);

/** Scroll distance in px, whatever unit the device reported it in. */
function wheelPixels(e: WheelEvent, pageHeight: number): number {
  if (e.deltaMode === 1) return e.deltaY * 16;
  if (e.deltaMode === 2) return e.deltaY * pageHeight;
  return e.deltaY;
}

/** Hover state, and a setter that only re-renders when the target changes. */
export function useHover(onHoverFile: ((file: CodeHealthMapFile | null) => void) | undefined) {
  const [hover, setHover] = useState<Hover | null>(null);
  const current = useRef<string | null>(null);
  const onHoverRef = useRef(onHoverFile);
  onHoverRef.current = onHoverFile;
  const setHovered = useCallback((next: Hover | null) => {
    const key = next ? (next.kind === "file" ? `f:${next.file.file_path}` : `a:${next.slot}:${next.path}`) : null;
    if (current.current === key) {
      // A big disc is hovered for a long way: let its card follow, coarsely.
      setHover((prev) =>
        next && prev && Math.hypot(next.x - prev.x, next.y - prev.y) > 24 ? next : prev,
      );
      return;
    }
    const wasFile = current.current?.startsWith("f:") ?? false;
    current.current = key;
    setHover(next);
    if (next?.kind === "file") onHoverRef.current?.(next.file);
    else if (wasFile) onHoverRef.current?.(null);
  }, []);
  return { hover, setHovered };
}

export function useMapPointer(opts: {
  container: RefObject<HTMLElement | null>;
  loop: MutableRefObject<LoopState>;
  invalidate: Invalidate;
  scene: FieldScene;
  grid: HitGrid;
  minNodeR: number;
  onSelectFile: (path: string) => void;
  onSelectArea: (path: string) => void;
  setHovered: (hover: Hover | null) => void;
}) {
  const { container, loop, invalidate, scene, grid, minNodeR, onSelectFile, onSelectArea, setHovered } = opts;
  const pointers = useRef(new Map<number, Point>());
  const drag = useRef<Drag | null>(null);
  // The reader is working in the map, so the wheel zooms instead of scrolling.
  const engaged = useRef(false);
  const suppressClick = useRef(false);

  const localPoint = useCallback(
    (clientX: number, clientY: number): Point => {
      const rect = container.current?.getBoundingClientRect();
      return { x: clientX - (rect?.left ?? 0), y: clientY - (rect?.top ?? 0) };
    },
    [container],
  );

  /** The drawn file or area disc under a point, as the last frame drew them. */
  const pick = useCallback(
    (p: Point): Hover | null => {
      const { cam, view, field } = loop.current;
      if (!field) return null;
      const w = screenToWorld(cam, view, p.x, p.y);
      const slop = (HIT_SLOP_PX + Math.max(0, MIN_NODE_PX - minNodeR * cam.scale)) / cam.scale;
      const node = hitTest(grid, w.wx, w.wy, slop);
      if (node >= 0 && field.drawnHosts[scene.hostOfNode[node]!]) {
        return { kind: "file", file: scene.nodes[node]!.file, x: p.x, y: p.y };
      }
      const disc = discAt(field, p.x, p.y);
      return disc ? { kind: "area", path: disc.path, slot: disc.state === "slot", x: p.x, y: p.y } : null;
    },
    [loop, grid, minNodeR, scene],
  );

  /** Apply a camera the pointer produced and repaint everything. */
  const moveCamera = useCallback(
    (next: (st: LoopState) => LoopState["cam"]) => {
      const st = loop.current;
      st.cam = clampCam(st, next(st));
      st.fly = null;
      st.userMoved = true;
      invalidate();
    },
    [loop, invalidate],
  );

  const onPointerDown = useCallback(
    (e: ReactPointerEvent) => {
      engaged.current = true;
      const p = localPoint(e.clientX, e.clientY);
      pointers.current.set(e.pointerId, p);
      (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
      const pts = [...pointers.current.values()];
      const pinch = pts.length === 2 ? distance(pts[0]!, pts[1]!) : 0;
      drag.current = { ...p, moved: drag.current?.moved ?? false, pinch };
    },
    [localPoint],
  );

  const pinchTo = useCallback(
    (d: Drag, a: Point, b: Point) => {
      const dist = distance(a, b);
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      moveCamera((st) => zoomAbout(st.cam, st.view, mid.x, mid.y, dist / d.pinch));
      d.pinch = dist;
      d.moved = true;
    },
    [moveCamera],
  );

  const panTo = useCallback(
    (d: Drag, p: Point): boolean => {
      const dx = p.x - d.x;
      const dy = p.y - d.y;
      if (!d.moved && Math.hypot(dx, dy) < DRAG_PX) return false;
      d.moved = true;
      d.x = p.x;
      d.y = p.y;
      moveCamera((st) => panByScreen(st.cam, dx, dy));
      return true;
    },
    [moveCamera],
  );

  const onPointerMove = useCallback(
    (e: ReactPointerEvent) => {
      const d = drag.current;
      if (!d || !pointers.current.has(e.pointerId)) return;
      const p = localPoint(e.clientX, e.clientY);
      pointers.current.set(e.pointerId, p);
      const pts = [...pointers.current.values()];
      if (pts.length === 2 && d.pinch > 0) pinchTo(d, pts[0]!, pts[1]!);
      else if (!panTo(d, p)) return;
      setHovered(null);
    },
    [localPoint, pinchTo, panTo, setHovered],
  );

  const onPointerUp = useCallback((e: ReactPointerEvent) => {
    pointers.current.delete(e.pointerId);
    const d = drag.current;
    if (pointers.current.size === 0) {
      suppressClick.current = d?.moved ?? false;
      drag.current = null;
    } else if (d) {
      const rest = [...pointers.current.values()][0]!;
      drag.current = { ...rest, moved: d.moved, pinch: 0 };
    }
  }, []);

  const onMouseMove = useCallback(
    (e: ReactMouseEvent) => {
      if (drag.current?.moved) return;
      setHovered(pick(localPoint(e.clientX, e.clientY)));
    },
    [localPoint, pick, setHovered],
  );

  const onMouseLeave = useCallback(() => {
    engaged.current = false;
    setHovered(null);
  }, [setHovered]);

  const onClick = useCallback(
    (e: ReactMouseEvent) => {
      if (suppressClick.current) {
        suppressClick.current = false;
        return;
      }
      engaged.current = true;
      const hit = pick(localPoint(e.clientX, e.clientY));
      if (hit?.kind === "file") onSelectFile(hit.file.file_path);
      else if (hit) onSelectArea(hit.path);
    },
    [localPoint, pick, onSelectFile, onSelectArea],
  );

  // Wheel zooms once the reader is working in the map (or pinches a
  // trackpad, which arrives as ctrl+wheel), so scrolling the page past the
  // map never gets trapped by it. Bound natively: React's wheel listener is
  // passive and cannot stop the page from scrolling.
  useEffect(() => {
    const node = container.current;
    if (!node) return;
    const onWheel = (e: WheelEvent) => {
      if (!engaged.current && !e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const rate = e.ctrlKey ? 0.01 : 0.0015;
      const factor = Math.exp(-wheelPixels(e, loop.current.view.h) * rate);
      const p = localPoint(e.clientX, e.clientY);
      moveCamera((st) => zoomAbout(st.cam, st.view, p.x, p.y, factor));
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [container, loop, localPoint, moveCamera]);

  return { onPointerDown, onPointerMove, onPointerUp, onMouseMove, onMouseLeave, onClick };
}
