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
import { hitTest, type HitGrid } from "./hit-test";
import type { FieldScene } from "./paint";
import type { CodeHealthMapFile, Galaxy } from "./types";
import { MIN_NODE_PX, type Invalidate, type LoopState } from "./use-field-canvas";
import { clampCam } from "./use-map-camera";

/** Pointer slop for picking a node, in px. */
const HIT_SLOP_PX = 3;
/** Movement before a press counts as a drag rather than a click. */
const DRAG_PX = 4;

export interface Hover {
  file: CodeHealthMapFile;
  x: number;
  y: number;
}

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

/** The smallest galaxy whose ring contains a world point. */
function galaxyAt(galaxies: Galaxy[], wx: number, wy: number): string | null {
  let inside: string | null = null;
  let best = Infinity;
  for (const g of galaxies) {
    if (Math.hypot(wx - g.cx, wy - g.cy) <= g.R && g.R < best) {
      inside = g.module;
      best = g.R;
    }
  }
  return inside;
}

/** Scroll distance in px, whatever unit the device reported it in. */
function wheelPixels(e: WheelEvent, pageHeight: number): number {
  if (e.deltaMode === 1) return e.deltaY * 16;
  if (e.deltaMode === 2) return e.deltaY * pageHeight;
  return e.deltaY;
}

/** Hover state, and a setter that only re-renders when the file changes. */
export function useHover(onHoverFile: ((file: CodeHealthMapFile | null) => void) | undefined) {
  const [hover, setHover] = useState<Hover | null>(null);
  const hovered = useRef<CodeHealthMapFile | null>(null);
  const onHoverRef = useRef(onHoverFile);
  onHoverRef.current = onHoverFile;
  const setHovered = useCallback((file: CodeHealthMapFile | null, x = 0, y = 0) => {
    if (hovered.current === file) return;
    hovered.current = file;
    setHover(file ? { file, x, y } : null);
    onHoverRef.current?.(file);
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
  galaxies: Galaxy[];
  focusModule: string | null;
  focusOn: (module: string | null) => void;
  onSelect: (path: string) => void;
  setHovered: (file: CodeHealthMapFile | null, x?: number, y?: number) => void;
}) {
  const { container, loop, invalidate, scene, grid, minNodeR, galaxies, focusModule, focusOn, onSelect, setHovered } =
    opts;
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

  const pick = useCallback(
    (p: Point) => {
      const { cam, view } = loop.current;
      const w = screenToWorld(cam, view, p.x, p.y);
      const slop = (HIT_SLOP_PX + Math.max(0, MIN_NODE_PX - minNodeR * cam.scale)) / cam.scale;
      return { node: hitTest(grid, w.wx, w.wy, slop), world: w };
    },
    [loop, grid, minNodeR],
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
      const p = localPoint(e.clientX, e.clientY);
      const { node } = pick(p);
      setHovered(node >= 0 ? scene.nodes[node]!.file : null, p.x, p.y);
    },
    [localPoint, pick, scene, setHovered],
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
      const { node, world } = pick(localPoint(e.clientX, e.clientY));
      if (node >= 0) {
        onSelect(scene.nodes[node]!.file.file_path);
        return;
      }
      // Inside a galaxy's ring zooms to it; empty space returns to the whole.
      const inside = galaxyAt(galaxies, world.wx, world.wy);
      if (inside && inside !== focusModule) focusOn(inside);
      else if (focusModule) focusOn(null);
    },
    [localPoint, pick, onSelect, scene, galaxies, focusModule, focusOn],
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
