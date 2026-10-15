"use client";

/**
 * The Code Health galaxy: one field, one geometry, a switchable lens.
 *
 * This module is the facade. The layout, the lens encodings, the hit grid, the
 * canvas painter, the hover card and the key are separate modules under
 * `map/`, and everything a host used to import is still re-exported here.
 * What is left in this file is composition: sizing, camera, selection,
 * keyboard navigation, and which layer repaints when.
 *
 * The geometry is deterministic and lens-independent on purpose. A reader
 * builds spatial memory of this field, and switching a lens must recolor it
 * rather than redraw it, or that memory is worth nothing.
 *
 * The field paints on a canvas. As SVG it was one element per file, so every
 * hover, zoom frame and lens switch reconciled thousands of nodes; on a canvas
 * the camera and the pointer live in refs, a frame is one transform and one
 * fill per colour, and React re-renders only for what it actually shows: the
 * galaxy labels, the hover card and the notes around the field.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  PointerEvent as ReactPointerEvent,
} from "react";

import { EmptyState } from "../shared/empty-state";
import { isLoudFill } from "./tokens";
import { useThemeVersion } from "../shared/use-theme-tokens";
import { usePrefersReducedMotion } from "../hooks/use-prefers-reduced-motion";
import { panByScreen, screenToWorld, zoomAbout, frameRect } from "../zoom/camera";
import { easeInOutCubic, flyDuration, interpolateCamera } from "../zoom/camera-anim";
import {
  HEALTH_UNSUPPORTED_NOTICE,
  NEUTRAL_FILL,
  OVERLAY_ORDER,
  OVERLAY_SPECS,
  QUIET_FILL,
  lensAvailable,
  noFileScored,
} from "./map/lens";
import { WORLD, fitCamera, layoutField } from "./map/layout";
import { buildHitGrid, hitTest } from "./map/hit-test";
import {
  attachPaths,
  buildScene,
  createColourResolver,
  drawField,
  drawMarks,
  groupBatches,
  resolvePalette,
  type Batch,
  type Cam,
  type Palette,
  type Rgb,
} from "./map/paint";
import { HoverCard, SEARCH_MARK_CAP } from "./map/overlay";
import { MapLegendRows, MapLensSwitcher } from "./map/legend";
import type { CodeHealthMapFile, CodeHealthOverlay, MapScope } from "./map/types";

export type {
  CodeHealthMapFile,
  CodeHealthOverlay,
  FileNode,
  Galaxy,
  GalaxyAgg,
  MapScope,
  PerformanceActionability,
} from "./map/types";
export {
  HEALTH_UNSUPPORTED_LABEL,
  HEALTH_UNSUPPORTED_NOTICE,
  NEUTRAL_FILL,
  OVERLAY_ORDER,
  OVERLAY_SPECS,
  PERFORMANCE_STATE_LABEL,
  QUIET_FILL,
  burdenBand,
  lensAvailable,
  noFileScored,
  performanceBurden,
  performanceFill,
  performanceSentence,
  scoreText,
} from "./map/lens";
export type {
  LegendRow,
  OverlaySpec,
  PerformanceBurden,
  PerformanceBurdenUnit,
  PerformanceNodeState,
} from "./map/lens";
export { WORLD, fitCamera, groupByModule, layoutField, moduleColorId, packGalaxies } from "./map/layout";
export { buildHitGrid, hitTest } from "./map/hit-test";
export { MapLegend, MapLensSwitcher } from "./map/legend";
export { SEARCH_MARK_CAP } from "./map/overlay";
export { FIELD_LIST_CAP, MapFieldList } from "./map/inspector";

export interface CodeHealthMapProps {
  files: CodeHealthMapFile[];
  /** Selection ring on this file. Owned by the host so a link can set it. */
  selectedPath?: string | null;
  /** Filename substring. Matching nodes take a ring; the field is untouched. */
  search?: string;
  /** Extra paths to mark, for a deep link into an opportunity's files. */
  highlightPaths?: string[];
  /** File click or keyboard selection. */
  onSelectFile?: (path: string) => void;
  /** Hover feeds the host's inspector. `null` when the pointer leaves a node. */
  onHoverFile?: (file: CodeHealthMapFile | null) => void;
  /** Canvas min height in px. */
  minHeight?: number;
  /** Which lens recolors the field. Defaults to `health`. */
  overlay?: CodeHealthOverlay;
  /** When provided, the lens is switchable from the on-canvas chrome. */
  onOverlayChange?: (overlay: CodeHealthOverlay) => void;
  /** Lenses offered in the switcher. Defaults to {@link OVERLAY_ORDER}. */
  lenses?: CodeHealthOverlay[];
  /**
   * The active lens's per-file signal is still loading. The key says so, so an
   * all-neutral field reads as "fetching" rather than as "no data".
   */
  overlayLoading?: boolean;
  /**
   * What the server drew and what it left out. Rendered as one sentence over
   * the canvas, from the same response the field came from.
   */
  scope?: MapScope;
  /**
   * Where the lens switcher and key live.
   *
   * `"canvas"` (default) floats them over the map, for a host with nowhere
   * else to put them. `"none"` renders neither, for a host that lays them out
   * around the canvas with {@link MapLensSwitcher} / {@link MapLegend}.
   */
  chrome?: "canvas" | "none";
  /**
   * Ask the host for a bigger field. Offered in the scope note while the
   * field is capped; the host raises its cap by `drawMoreStep` and refetches.
   */
  onDrawMore?: () => void;
  /** Files one "draw more" adds, for the button's label. Defaults to 2,000. */
  drawMoreStep?: number;
  /** Ask the host to draw (or stop drawing) vendored and generated files. */
  onIncludeHeldChange?: (include: boolean) => void;
  /** Ask the host to pin the files matching `search` that are not drawn. */
  onFindUndrawn?: (query: string) => void;
  /** A host request for a new field is in flight; its buttons wait for it. */
  pending?: boolean;
}

/** Label a galaxy once its on-screen radius clears this (px). */
const LABEL_MIN_SCREEN_R = 28;
/** Smallest radius a node is drawn at, so a zoomed-out file is still a dot. */
const MIN_NODE_PX = 0.9;
/** Pointer slop for picking a node, in px. */
const HIT_SLOP_PX = 3;
/** Movement before a press counts as a drag rather than a click. */
const DRAG_PX = 4;
/** How far out and in the camera may go, relative to the fitted view. */
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 80;
/** One step of the zoom buttons and keys. */
const ZOOM_STEP = 1.6;
/** Lens recolor, in ms. */
const COLOUR_TWEEN_MS = 320;

type KeyboardLevel = "module" | "file";

interface Tween {
  from: Cam;
  to: Cam;
  start: number;
  dur: number;
}

function now(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

export function CodeHealthMap({
  files,
  selectedPath,
  search,
  highlightPaths,
  onSelectFile,
  onHoverFile,
  minHeight = 640,
  overlay = "health",
  onOverlayChange,
  lenses = OVERLAY_ORDER,
  overlayLoading = false,
  scope,
  chrome = "canvas",
  onDrawMore,
  drawMoreStep = 2000,
  onIncludeHeldChange,
  onFindUndrawn,
  pending = false,
}: CodeHealthMapProps) {
  const overlaySpec = OVERLAY_SPECS[overlay] ?? OVERLAY_SPECS.health;
  const containerRef = useRef<HTMLDivElement>(null);
  const baseRef = useRef<HTMLCanvasElement>(null);
  const topRef = useRef<HTMLCanvasElement>(null);
  const keyboardRef = useRef<HTMLDivElement>(null);
  const labelRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const reducedMotion = usePrefersReducedMotion();
  const themeVersion = useThemeVersion();

  const [ready, setReady] = useState(false);
  const [focusModule, setFocusModule] = useState<string | null>(null);
  const [hover, setHover] = useState<{ file: CodeHealthMapFile; x: number; y: number } | null>(
    null,
  );
  // Keyboard cursor. One tab stop reaches the whole field: arrows move over
  // modules, Enter descends into one, arrows then move over its files. The
  // alternative is a tab stop per node, which is thousands of them.
  const [kbLevel, setKbLevel] = useState<KeyboardLevel>("module");
  const [kbModule, setKbModule] = useState(0);
  const [kbFile, setKbFile] = useState(0);
  // The cursor is drawn only while the field has keyboard focus, so a reader
  // using a pointer never sees a ring that answers to keys they are not using.
  const [kbFocused, setKbFocused] = useState(false);

  // ---- The field: laid out once per feed, never per resize or lens. ----
  const galaxies = useMemo(() => layoutField(files), [files]);
  const scene = useMemo(() => buildScene(galaxies), [galaxies]);
  const grid = useMemo(() => buildHitGrid(scene.nodes), [scene]);
  const minNodeR = useMemo(
    () => scene.nodes.reduce((m, n) => Math.min(m, n.r), Infinity),
    [scene],
  );
  // Galaxies biggest first, the order labels claim space in.
  const labelOrder = useMemo(
    () => galaxies.map((_, i) => i).sort((a, b) => galaxies[b]!.R - galaxies[a]!.R),
    [galaxies],
  );
  const fills = useMemo(
    () => scene.nodes.map((n) => overlaySpec.fill(n.file)),
    [scene, overlaySpec],
  );
  const focusIdx = focusModule ? galaxies.findIndex((g) => g.module === focusModule) : -1;
  const focusGalaxy = focusIdx >= 0 ? galaxies[focusIdx]! : null;

  const q = (search ?? "").trim().toLowerCase();
  // Matching runs over the drawn field only and produces a bounded list for
  // the marks layer. Neither the layout nor the field sees the query, so
  // typing repaints the rings and nothing else.
  const matches = useMemo(() => {
    if (q.length === 0) return [] as number[];
    const out: number[] = [];
    scene.nodes.forEach((n, i) => {
      if (n.file.file_path.toLowerCase().includes(q)) out.push(i);
    });
    return out;
  }, [q, scene]);
  const marked = useMemo(() => {
    const extra = (highlightPaths ?? [])
      .map((p) => scene.index.get(p))
      .filter((i): i is number => i !== undefined);
    const all = extra.length ? [...new Set([...extra, ...matches])] : matches;
    return all.slice(0, SEARCH_MARK_CAP);
  }, [highlightPaths, matches, scene]);

  const kbGalaxy = galaxies[Math.min(kbModule, Math.max(0, galaxies.length - 1))] ?? null;
  const kbNode =
    kbLevel === "file" && kbGalaxy
      ? kbGalaxy.nodes[Math.min(kbFile, Math.max(0, kbGalaxy.nodes.length - 1))] ?? null
      : null;
  const selectedIdx = selectedPath ? scene.index.get(selectedPath) ?? -1 : -1;
  const cursorIdx = kbNode && kbFocused ? scene.index.get(kbNode.file.file_path) ?? -1 : -1;
  const hoverIdx = hover ? scene.index.get(hover.file.file_path) ?? -1 : -1;

  // ---- Everything the frame loop reads, held in refs so a frame never
  // waits on React and React never re-renders for a frame. ----
  const view = useRef({ w: 0, h: 0, dpr: 1 });
  const cam = useRef<Cam>({ cx: WORLD / 2, cy: WORLD / 2, scale: 1 });
  const userMoved = useRef(false);
  const fly = useRef<Tween | null>(null);
  const colourTween = useRef<{ from: string[]; start: number } | null>(null);
  const shownFills = useRef<string[] | null>(null);
  const batches = useRef<Batch[]>([]);
  const batchesStale = useRef(true);
  const batchFloor = useRef(0);
  const dirty = useRef({ base: true, top: true, labels: true });
  const raf = useRef<number | null>(null);
  const resolver = useRef<(css: string) => Rgb>(() => [128, 128, 128]);
  const palette = useRef<Palette | null>(null);
  const live = useRef({ scene, fills, focusIdx, marked, selectedIdx, cursorIdx, hoverIdx, minNodeR, labelOrder });
  live.current = { scene, fills, focusIdx, marked, selectedIdx, cursorIdx, hoverIdx, minNodeR, labelOrder };

  const frame = useCallback(() => {
    raf.current = null;
    const v = view.current;
    if (v.w === 0) return;
    const L = live.current;
    const t0 = now();
    let animating = false;
    let moved = false;

    const f = fly.current;
    if (f) {
      const p = f.dur <= 0 ? 1 : (t0 - f.start) / f.dur;
      cam.current = p >= 1 ? f.to : interpolateCamera(f.from, f.to, p);
      if (p >= 1) fly.current = null;
      else animating = true;
      moved = true;
    }

    let t = 1;
    const ct = colourTween.current;
    if (ct) {
      const p = (t0 - ct.start) / COLOUR_TWEEN_MS;
      if (p >= 1) {
        colourTween.current = null;
        batchesStale.current = true;
      } else {
        t = easeInOutCubic(p);
        animating = true;
      }
      dirty.current.base = true;
    }

    // A zoom step that changes how far the dot floor reaches rebuilds the
    // paths; otherwise they are reused across every frame of a pan or zoom.
    const s = cam.current.scale;
    const floor = MIN_NODE_PX / s;
    const floorMatters = L.minNodeR * s < MIN_NODE_PX * 1.5;
    if (floorMatters) {
      const built = batchFloor.current;
      if (built === 0 || floor / built > 1.25 || floor / built < 0.8) batchesStale.current = true;
    } else if (batchFloor.current > 0) {
      batchesStale.current = true;
    }

    const base = baseRef.current?.getContext("2d") ?? null;
    const top = topRef.current?.getContext("2d") ?? null;
    if (!palette.current) palette.current = resolvePalette(resolver.current);
    const pal = palette.current;

    if (batchesStale.current && base) {
      const loud = isLoudFill;
      batches.current = groupBatches(
        L.scene,
        L.fills,
        colourTween.current?.from ?? null,
        L.focusIdx,
        loud,
        resolver.current,
      );
      attachPaths(batches.current, L.scene, floorMatters ? floor : 0);
      batchFloor.current = floorMatters ? floor : 0;
      batchesStale.current = false;
      dirty.current.base = true;
    }

    const camera = cam.current;
    if (base && (dirty.current.base || moved)) {
      drawField(base, L.scene, batches.current, camera, v, t, L.focusIdx, pal);
    }
    if (top && (dirty.current.top || moved)) {
      drawMarks(
        top,
        L.scene,
        camera,
        v,
        { selected: L.selectedIdx, hovered: L.hoverIdx, cursor: L.cursorIdx, matches: L.marked },
        (i) => resolver.current(L.fills[i] ?? NEUTRAL_FILL),
        MIN_NODE_PX,
        pal,
      );
    }
    if (dirty.current.labels || moved) placeLabels(L.scene.galaxies, L.labelOrder, L.focusIdx, camera, v, labelRefs.current);
    dirty.current = { base: false, top: false, labels: false };
    if (animating) raf.current = requestAnimationFrame(frame);
  }, []);

  const invalidate = useCallback(
    (what: Partial<{ base: boolean; top: boolean; labels: boolean }> = { base: true, top: true, labels: true }) => {
      if (what.base) dirty.current.base = true;
      if (what.top) dirty.current.top = true;
      if (what.labels) dirty.current.labels = true;
      if (raf.current == null && typeof requestAnimationFrame !== "undefined") {
        raf.current = requestAnimationFrame(frame);
      }
    },
    [frame],
  );

  useEffect(
    () => () => {
      // Cleared as well as cancelled: a remount (StrictMode runs effects
      // twice) must be able to schedule again.
      if (raf.current != null) cancelAnimationFrame(raf.current);
      raf.current = null;
    },
    [],
  );

  const clampCam = useCallback((c: Cam): Cam => {
    const fit = fitCamera(view.current.w, view.current.h);
    const scale = Math.min(fit.scale * MAX_ZOOM, Math.max(fit.scale * MIN_ZOOM, c.scale));
    const lo = -WORLD * 0.25;
    const hi = WORLD * 1.25;
    return { cx: Math.min(hi, Math.max(lo, c.cx)), cy: Math.min(hi, Math.max(lo, c.cy)), scale };
  }, []);

  /** Move the camera, flown unless the reader asked for no motion. */
  const flyTo = useCallback(
    (to: Cam, animate = true) => {
      const target = clampCam(to);
      const from = cam.current;
      fly.current = {
        from,
        to: target,
        start: now(),
        dur: animate && !reducedMotion ? flyDuration(from, target) : 0,
      };
      invalidate();
    },
    [clampCam, invalidate, reducedMotion],
  );

  const fitView = useCallback(
    (animate = true) => {
      userMoved.current = false;
      flyTo(fitCamera(view.current.w, view.current.h), animate);
    },
    [flyTo],
  );

  // ---- Sizing: the canvas tracks its box, coalesced to one frame. ----
  useEffect(() => {
    const node = containerRef.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    let pendingSize: { width: number; height: number } | null = null;
    let sizeRaf: number | null = null;
    let last = { width: 0, height: 0 };
    const apply = () => {
      sizeRaf = null;
      if (!pendingSize) return;
      const { width, height } = pendingSize;
      last = pendingSize;
      pendingSize = null;
      const w = Math.round(width);
      const h = Math.round(Math.max(height, minHeight));
      const dpr = typeof window !== "undefined" ? Math.min(window.devicePixelRatio || 1, 3) : 1;
      view.current = { w, h, dpr };
      for (const c of [baseRef.current, topRef.current]) {
        if (!c) continue;
        c.width = Math.max(1, Math.round(w * dpr));
        c.height = Math.max(1, Math.round(h * dpr));
        c.style.width = `${w}px`;
        c.style.height = `${h}px`;
      }
      if (!userMoved.current) {
        fly.current = null;
        cam.current = fitCamera(w, h);
      }
      setReady(w > 0);
      invalidate();
    };
    const observer = new ResizeObserver((entries) => {
      const first = entries[0];
      if (!first) return;
      pendingSize = { width: first.contentRect.width, height: first.contentRect.height };
      // The first size lands synchronously so the field never paints a blank
      // frame at mount; later ones coalesce into one per animation frame.
      if (view.current.w === 0) apply();
      else if (sizeRaf == null) sizeRaf = requestAnimationFrame(apply);
    });
    observer.observe(node);
    // A ResizeObserver does not fire when the window moves to a screen with
    // another pixel ratio, so the backing store would stay at the old one.
    // The query names the current ratio, so it is re-armed on every change.
    let media: MediaQueryList | null = null;
    const onRatio = () => {
      pendingSize = last;
      apply();
      watchRatio();
    };
    const watchRatio = () => {
      media?.removeEventListener("change", onRatio);
      if (typeof window === "undefined" || !window.matchMedia) return;
      media = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
      media.addEventListener("change", onRatio);
    };
    watchRatio();
    return () => {
      observer.disconnect();
      media?.removeEventListener("change", onRatio);
      if (sizeRaf != null) cancelAnimationFrame(sizeRaf);
    };
  }, [minHeight, invalidate]);

  // ---- Repaint triggers. Each names the one layer it changes. ----
  useEffect(() => {
    resolver.current = createColourResolver(containerRef.current);
    palette.current = null;
    batchesStale.current = true;
    invalidate();
  }, [themeVersion, invalidate]);

  useEffect(() => {
    // A new feed is a new field: no tween from the old one's colours.
    shownFills.current = null;
    colourTween.current = null;
    batchesStale.current = true;
    if (!userMoved.current && view.current.w > 0) cam.current = fitCamera(view.current.w, view.current.h);
    invalidate();
  }, [scene, invalidate]);

  useEffect(() => {
    const previous = shownFills.current;
    shownFills.current = fills;
    if (previous && previous !== fills && previous.length === fills.length && !reducedMotion) {
      colourTween.current = { from: previous, start: now() };
    }
    batchesStale.current = true;
    invalidate();
  }, [fills, reducedMotion, invalidate]);

  useEffect(() => {
    batchesStale.current = true;
    invalidate();
  }, [focusIdx, invalidate]);

  useEffect(() => {
    invalidate({ top: true });
  }, [marked, selectedIdx, cursorIdx, hoverIdx, invalidate]);

  useEffect(() => {
    invalidate({ labels: true });
  }, [kbModule, kbLevel, ready, invalidate]);

  // Focusing a galaxy frames it. Leaving one is a state change only: the
  // effect below returns the camera, so every way out (a click, Escape, the
  // breadcrumb) takes the same path.
  const focusOn = useCallback(
    (module: string | null) => {
      setFocusModule(module);
      const g = module ? galaxies.find((x) => x.module === module) : null;
      if (g) {
        userMoved.current = true;
        flyTo(frameRect(view.current, { x: g.cx - g.R, y: g.cy - g.R, w: 2 * g.R, h: 2 * g.R }, 0.9));
      }
    },
    [galaxies, flyTo],
  );
  const fitAll = useCallback(() => {
    if (focusModule) setFocusModule(null);
    else fitView();
  }, [focusModule, fitView]);

  // A galaxy that left the field takes its focus with it.
  useEffect(() => {
    if (focusModule && focusIdx < 0) setFocusModule(null);
  }, [focusModule, focusIdx]);

  // ---- Pointer: picking is a grid lookup, so it runs on every move. ----
  const onSelectRef = useRef(onSelectFile);
  onSelectRef.current = onSelectFile;
  const onHoverRef = useRef(onHoverFile);
  onHoverRef.current = onHoverFile;

  const localPoint = useCallback((clientX: number, clientY: number) => {
    const rect = containerRef.current?.getBoundingClientRect();
    return { x: clientX - (rect?.left ?? 0), y: clientY - (rect?.top ?? 0) };
  }, []);

  const pick = useCallback(
    (x: number, y: number) => {
      const c = cam.current;
      const w = screenToWorld(c, view.current, x, y);
      const slop = (HIT_SLOP_PX + Math.max(0, MIN_NODE_PX - minNodeR * c.scale)) / c.scale;
      return { node: hitTest(grid, w.wx, w.wy, slop), world: w };
    },
    [grid, minNodeR],
  );

  const handleSelect = useCallback((path: string) => {
    // Clicking a node hands focus to the field's keyboard region, so a reader
    // who reached the map with a pointer can carry on with arrow keys.
    keyboardRef.current?.focus();
    onSelectRef.current?.(path);
  }, []);

  // The hovered file, mirrored in a ref so a move over the same node is a
  // grid lookup and nothing else: React hears about hover only when it changes.
  const hovered = useRef<CodeHealthMapFile | null>(null);
  const setHovered = useCallback((file: CodeHealthMapFile | null, x = 0, y = 0) => {
    if (hovered.current === file) return;
    hovered.current = file;
    setHover(file ? { file, x, y } : null);
    onHoverRef.current?.(file);
  }, []);

  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const drag = useRef<{ x: number; y: number; moved: boolean; pinch: number } | null>(null);
  const engaged = useRef(false);
  const suppressClick = useRef(false);

  const onPointerDown = useCallback(
    (e: ReactPointerEvent) => {
      engaged.current = true;
      const p = localPoint(e.clientX, e.clientY);
      pointers.current.set(e.pointerId, p);
      (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
      const pts = [...pointers.current.values()];
      const pinch =
        pts.length === 2 ? Math.hypot(pts[0]!.x - pts[1]!.x, pts[0]!.y - pts[1]!.y) : 0;
      drag.current = { x: p.x, y: p.y, moved: drag.current?.moved ?? false, pinch };
    },
    [localPoint],
  );

  const onPointerMove = useCallback(
    (e: ReactPointerEvent) => {
      const d = drag.current;
      if (!d || !pointers.current.has(e.pointerId)) return;
      const p = localPoint(e.clientX, e.clientY);
      pointers.current.set(e.pointerId, p);
      const pts = [...pointers.current.values()];
      if (pts.length === 2 && d.pinch > 0) {
        const dist = Math.hypot(pts[0]!.x - pts[1]!.x, pts[0]!.y - pts[1]!.y);
        const mid = { x: (pts[0]!.x + pts[1]!.x) / 2, y: (pts[0]!.y + pts[1]!.y) / 2 };
        cam.current = clampCam(zoomAbout(cam.current, view.current, mid.x, mid.y, dist / d.pinch));
        d.pinch = dist;
        d.moved = true;
      } else {
        const dx = p.x - d.x;
        const dy = p.y - d.y;
        if (!d.moved && Math.hypot(dx, dy) < DRAG_PX) return;
        d.moved = true;
        cam.current = clampCam(panByScreen(cam.current, dx, dy));
        d.x = p.x;
        d.y = p.y;
      }
      fly.current = null;
      userMoved.current = true;
      setHovered(null);
      invalidate({ base: true, top: true, labels: true });
    },
    [localPoint, clampCam, invalidate, setHovered],
  );

  const onPointerUp = useCallback((e: ReactPointerEvent) => {
    pointers.current.delete(e.pointerId);
    const d = drag.current;
    if (pointers.current.size === 0) {
      suppressClick.current = d?.moved ?? false;
      drag.current = null;
    } else if (d) {
      const rest = [...pointers.current.values()][0]!;
      drag.current = { x: rest.x, y: rest.y, moved: d.moved, pinch: 0 };
    }
  }, []);

  const onMouseMove = useCallback(
    (e: ReactMouseEvent) => {
      if (drag.current?.moved) return;
      const p = localPoint(e.clientX, e.clientY);
      const { node } = pick(p.x, p.y);
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
      const p = localPoint(e.clientX, e.clientY);
      const { node, world } = pick(p.x, p.y);
      if (node >= 0) {
        handleSelect(scene.nodes[node]!.file.file_path);
        return;
      }
      // Inside a galaxy's ring zooms to it; empty space returns to the whole.
      let inside: string | null = null;
      let best = Infinity;
      for (const g of galaxies) {
        const d = Math.hypot(world.wx - g.cx, world.wy - g.cy);
        if (d <= g.R && g.R < best) {
          inside = g.module;
          best = g.R;
        }
      }
      if (inside && inside !== focusModule) focusOn(inside);
      else if (focusModule) focusOn(null);
    },
    [localPoint, pick, handleSelect, scene, galaxies, focusModule, focusOn],
  );

  // Wheel zooms once the reader is working in the map (or pinches a
  // trackpad, which arrives as ctrl+wheel), so scrolling the page past the
  // map never gets trapped by it. Bound natively: React's wheel listener is
  // passive and cannot stop the page from scrolling.
  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const onWheel = (e: WheelEvent) => {
      if (!engaged.current && !e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? view.current.h : 1;
      const rate = e.ctrlKey ? 0.01 : 0.0015;
      const factor = Math.exp(-e.deltaY * unit * rate);
      const p = localPoint(e.clientX, e.clientY);
      fly.current = null;
      userMoved.current = true;
      cam.current = clampCam(zoomAbout(cam.current, view.current, p.x, p.y, factor));
      invalidate();
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [localPoint, clampCam, invalidate]);

  const zoomBy = useCallback(
    (factor: number) => {
      userMoved.current = true;
      const v = view.current;
      flyTo(zoomAbout(cam.current, v, v.w / 2, v.h / 2, factor));
    },
    [flyTo],
  );

  // Escape climbs out of a focused galaxy (and the file level) while the
  // focus is inside the field, and only then. When it does that it is
  // consumed: the open file drawer listens on the document in the capture
  // phase, so the map listens on the window in the capture phase and stops
  // the key there, or one press would both zoom out and close the drawer.
  // An Escape the map has no use for passes through untouched.
  const escapeState = useRef({ focused: false, fileLevel: false });
  escapeState.current = { focused: focusModule != null, fileLevel: kbLevel === "file" };
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const node = containerRef.current;
      if (!node || !(e.target instanceof Node) || !node.contains(e.target)) return;
      const { focused, fileLevel } = escapeState.current;
      if (!focused && !fileLevel) return;
      e.stopPropagation();
      e.preventDefault();
      setKbLevel("module");
      setFocusModule(null);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);
  // Every way out of a galaxy clears the state; the camera follows here.
  const lastFocus = useRef<string | null>(null);
  useEffect(() => {
    if (lastFocus.current && !focusModule) fitView();
    lastFocus.current = focusModule;
  }, [focusModule, fitView]);

  const onCanvasKeyDown = useCallback(
    (e: ReactKeyboardEvent) => {
      if (e.key === "+" || e.key === "=") {
        e.preventDefault();
        zoomBy(ZOOM_STEP);
        return;
      }
      if (e.key === "-" || e.key === "_") {
        e.preventDefault();
        zoomBy(1 / ZOOM_STEP);
        return;
      }
      if (e.key === "0") {
        e.preventDefault();
        setKbLevel("module");
        fitAll();
        return;
      }
      const forward = e.key === "ArrowRight" || e.key === "ArrowDown";
      const back = e.key === "ArrowLeft" || e.key === "ArrowUp";
      if (kbLevel === "module") {
        if (forward || back) {
          e.preventDefault();
          setKbModule((i) => {
            const n = galaxies.length;
            return n === 0 ? 0 : (i + (forward ? 1 : n - 1)) % n;
          });
        } else if (e.key === "Enter" && kbGalaxy) {
          e.preventDefault();
          setKbLevel("file");
          setKbFile(0);
          focusOn(kbGalaxy.module);
        }
        return;
      }
      if (forward || back) {
        e.preventDefault();
        const n = kbGalaxy?.nodes.length ?? 0;
        setKbFile((i) => (n === 0 ? 0 : (i + (forward ? 1 : n - 1)) % n));
      } else if (e.key === "Enter" && kbNode) {
        e.preventDefault();
        handleSelect(kbNode.file.file_path);
      } else if (e.key === "Backspace" || e.key === "Escape") {
        e.preventDefault();
        setKbLevel("module");
        setFocusModule(null);
      }
    },
    [kbLevel, kbGalaxy, kbNode, galaxies.length, handleSelect, focusOn, zoomBy, fitAll],
  );

  const offered = useMemo(() => lenses.filter((l) => lensAvailable(l, files)), [lenses, files]);
  const attention = useMemo(
    () => fills.reduce((n, f) => (isLoudFill(f) ? n + 1 : n), 0),
    [fills],
  );

  if (files.length === 0) {
    return (
      <div ref={containerRef} className="flex w-full items-center justify-center" style={{ minHeight }}>
        <EmptyState
          size="compact"
          title="No files to map yet"
          description="Index this repo to populate health."
        />
      </div>
    );
  }

  const cursorLabel =
    kbLevel === "file" && kbNode
      ? `${kbNode.file.file_path}, ${overlaySpec.label} lens`
      : kbGalaxy
        ? `${kbGalaxy.label}, ${kbGalaxy.fileCount} files`
        : "";

  return (
    <div
      ref={containerRef}
      data-testid="map-field"
      data-matches={matches.length}
      data-selected={selectedIdx >= 0 ? selectedPath ?? undefined : undefined}
      data-keyboard-cursor={kbNode?.file.file_path}
      className="relative w-full overflow-hidden rounded-xl border border-[var(--color-border-default)] focus-within:ring-2 focus-within:ring-[var(--color-accent-primary)]"
      style={{
        minHeight,
        background:
          "radial-gradient(ellipse at 50% 42%, var(--color-bg-surface), var(--color-bg-root) 75%)",
      }}
    >
      {/* One tab stop for the whole field. Arrows move over modules, Enter
          descends into one and then moves over its files, Enter again selects
          it and opens the same inspector a click opens. Pointer events stay
          off so it never intercepts a click meant for the canvas. */}
      <div
        ref={keyboardRef}
        tabIndex={0}
        role="group"
        aria-label={`Code health map, ${overlaySpec.label} lens. Arrow keys move between modules, Enter opens one, plus and minus zoom, 0 fits the field.`}
        onKeyDown={onCanvasKeyDown}
        onFocus={() => setKbFocused(true)}
        onBlur={() => setKbFocused(false)}
        className="pointer-events-none absolute inset-0 z-10 outline-none"
      />
      <span aria-live="polite" className="sr-only">
        {cursorLabel}
      </span>
      <p className="sr-only">
        {`${scene.nodes.length.toLocaleString()} files drawn in ${galaxies.length} groups, sized by lines of code. ${attention.toLocaleString()} need attention under the ${overlaySpec.label.toLowerCase()} lens. The list beside the map reaches every one.`}
      </p>

      <canvas ref={baseRef} aria-hidden className="pointer-events-none absolute inset-0" />
      <canvas
        ref={topRef}
        aria-hidden
        data-testid="map-canvas"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onMouseMove={onMouseMove}
        onMouseLeave={onMouseLeave}
        onClick={onClick}
        className={`absolute inset-0 ${hover && onSelectFile ? "cursor-pointer" : "cursor-grab active:cursor-grabbing"}`}
        style={{ touchAction: "pan-y" }}
      />

      {/* Galaxy names: real-px HTML, crisp at any zoom, placed by the frame
          loop with the biggest galaxies claiming space first and any label
          that would collide hidden. A label is also the way into a galaxy. */}
      <div className="pointer-events-none absolute inset-0">
        {galaxies.map((g, i) => {
          const cursor = kbFocused && kbLevel === "module" && kbGalaxy === g;
          return (
            <button
              key={g.module}
              ref={(el) => {
                labelRefs.current[i] = el;
              }}
              type="button"
              tabIndex={-1}
              title={`${g.label} · ${g.fileCount.toLocaleString()} files`}
              onClick={() => focusOn(focusModule === g.module ? null : g.module)}
              className={`pointer-events-auto absolute left-0 top-0 max-w-[240px] truncate rounded bg-[var(--color-bg-glass)] px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] ${
                cursor ? "ring-2 ring-[var(--color-accent-primary)]" : ""
              } ${focusIdx === i ? "font-medium text-[var(--color-text-primary)]" : ""}`}
              style={{ visibility: "hidden" }}
            >
              {g.label}
            </button>
          );
        })}
      </div>

      {chrome === "canvas" ? (
        <>
          {onOverlayChange ? (
            <div className="absolute left-3 top-3 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] p-1 shadow-sm backdrop-blur-sm">
              <MapLensSwitcher
                overlay={overlay}
                onOverlayChange={onOverlayChange}
                lenses={offered}
                className="border-0"
              />
            </div>
          ) : null}

          <div
            className={`absolute left-3 max-w-[220px] rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] px-3 py-2 text-xs shadow-sm backdrop-blur-sm ${
              onOverlayChange ? "top-14" : "top-3"
            }`}
          >
            <div className="mb-1.5 font-medium text-[var(--color-text-secondary)]">
              {overlaySpec.label}
            </div>
            <MapLegendRows spec={overlaySpec} loading={overlayLoading} />
            <div className="mt-2 border-t border-[var(--color-border-default)] pt-1.5 text-[var(--color-text-tertiary)]">
              {overlaySpec.caption}
              <br />
              click a galaxy to zoom
            </div>
          </div>
        </>
      ) : null}

      <div className="absolute right-3 top-3 z-20 flex flex-col items-end gap-1.5">
        {focusGalaxy ? (
          <div className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] px-2.5 py-1.5 text-xs shadow-sm backdrop-blur-sm">
            <span className="max-w-[180px] truncate font-medium text-[var(--color-text-primary)]">
              {focusGalaxy.label}
            </span>
            <button
              type="button"
              onClick={() => {
                setKbLevel("module");
                focusOn(null);
              }}
              className="ml-1 rounded border border-[var(--color-border-default)] px-1.5 py-0.5 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
            >
              ← Overview
            </button>
          </div>
        ) : null}
        <div
          role="group"
          aria-label="Zoom"
          className="flex overflow-hidden rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] text-xs shadow-sm backdrop-blur-sm"
        >
          {[
            { label: "Zoom in", text: "+", act: () => zoomBy(ZOOM_STEP) },
            { label: "Zoom out", text: "−", act: () => zoomBy(1 / ZOOM_STEP) },
            {
              label: "Fit the whole field",
              text: "Fit",
              act: () => {
                setKbLevel("module");
                fitAll();
              },
            },
          ].map((b, i) => (
            <button
              key={b.label}
              type="button"
              aria-label={b.label}
              title={b.label}
              onClick={b.act}
              className={`min-w-[28px] px-2 py-1 font-mono tabular-nums text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-elevated)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-accent-primary)] ${
                i > 0 ? "border-l border-[var(--color-border-default)]" : ""
              }`}
            >
              {b.text}
            </button>
          ))}
        </div>
      </div>

      {scope ? (
        <MapScopeNote
          scope={scope}
          matches={matches.length}
          query={q}
          drawMoreStep={drawMoreStep}
          pending={pending}
          {...(onDrawMore ? { onDrawMore } : {})}
          {...(onIncludeHeldChange ? { onIncludeHeldChange } : {})}
          {...(onFindUndrawn ? { onFindUndrawn } : {})}
        />
      ) : null}

      {noFileScored(files) ? (
        <div
          role="status"
          data-testid="map-unanalysed"
          className="pointer-events-none absolute bottom-3 left-3 max-w-[64%] rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] px-2.5 py-1.5 text-xs text-[var(--color-text-secondary)] shadow-sm backdrop-blur-sm sm:max-w-[44%]"
        >
          {HEALTH_UNSUPPORTED_NOTICE}
        </div>
      ) : null}

      {hover ? (
        <HoverCard
          file={hover.file}
          overlay={overlay}
          x={hover.x}
          y={hover.y}
          width={view.current.w}
          height={view.current.h}
        />
      ) : null}
    </div>
  );
}

/** Rough label box, for collision culling before a label has been measured. */
const LABEL_H = 18;
const labelWidth = (el: HTMLElement | null, text: string) =>
  el?.offsetWidth || Math.min(240, text.length * 6.6 + 14);

/**
 * Put each galaxy's name just above its ring, biggest galaxy first, and hide
 * any name that would land on one already placed or whose galaxy is too small
 * on screen to need one. Runs in the frame loop, writing styles directly, so
 * a pan moves the names without a React render.
 */
function placeLabels(
  galaxies: { cx: number; cy: number; R: number; label: string }[],
  order: number[],
  focus: number,
  camera: Cam,
  v: { w: number; h: number },
  els: (HTMLElement | null)[],
): void {
  const placed: { x: number; y: number; w: number }[] = [];
  // A field of a few galaxies names every one, however small; a crowded one
  // names those big enough on screen to be worth a name.
  const minR = galaxies.length <= 12 ? 4 : LABEL_MIN_SCREEN_R;
  for (const i of order) {
    const g = galaxies[i];
    const el = els[i];
    if (!g || !el) continue;
    const R = g.R * camera.scale;
    const x = v.w / 2 + (g.cx - camera.cx) * camera.scale;
    let y = v.h / 2 + (g.cy - g.R - camera.cy) * camera.scale - 4 - LABEL_H;
    // A ring whose top is off the canvas carries its name just inside it.
    if (y < 4) y = Math.max(4, y + LABEL_H + 10);
    const w = labelWidth(el, g.label);
    const left = x - w / 2;
    const visible =
      (i === focus || (R >= minR && (focus < 0 || R >= minR * 2))) &&
      left + w > 0 &&
      left < v.w &&
      y + LABEL_H > 0 &&
      y < v.h &&
      !placed.some((p) => left < p.x + p.w + 6 && p.x < left + w + 6 && Math.abs(p.y - y) < LABEL_H + 2);
    if (visible) placed.push({ x: left, y, w });
    el.style.visibility = visible ? "visible" : "hidden";
    el.style.transform = `translate(${Math.round(left)}px, ${Math.round(y)}px)`;
  }
}

/**
 * What this field is, in one sentence, from the response that drew it, plus
 * the few actions that change it.
 *
 * A count and the visual it describes have to come from one source, so this
 * reads the server's own selection totals rather than the array length: a
 * caption saying "3,808 files" over a two-thousand node field is a claim the
 * reader has no way to check. The actions sit here because this is where the
 * reader learns the field is partial.
 */
function MapScopeNote({
  scope,
  matches,
  query,
  drawMoreStep,
  pending,
  onDrawMore,
  onIncludeHeldChange,
  onFindUndrawn,
}: {
  scope: MapScope;
  matches: number;
  query: string;
  drawMoreStep: number;
  pending: boolean;
  onDrawMore?: () => void;
  onIncludeHeldChange?: (include: boolean) => void;
  onFindUndrawn?: (query: string) => void;
}) {
  const capped = scope.shown < scope.eligible;
  const hiddenCauses = scope.omitted.performanceFiles;
  const more = Math.min(drawMoreStep, scope.eligible - scope.shown);
  const held = scope.held ?? 0;
  const pinned = scope.query && scope.query.text === query ? scope.query : null;
  const action =
    "pointer-events-auto rounded px-1 text-[var(--color-accent-primary)] underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] disabled:cursor-wait disabled:opacity-60";
  return (
    <div
      data-testid="map-scope"
      className="pointer-events-none absolute bottom-3 right-3 z-20 max-w-[64%] rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] px-2.5 py-1.5 text-right text-xs text-[var(--color-text-tertiary)] shadow-sm backdrop-blur-sm sm:max-w-[52%]"
    >
      <span className="font-mono tabular-nums">
        {scope.shown.toLocaleString()} of {scope.eligible.toLocaleString()}
      </span>{" "}
      files drawn
      {capped ? (
        <>
          {" · "}
          {hiddenCauses == null
            ? `${scope.omitted.files.toLocaleString()} not drawn`
            : hiddenCauses > 0
              ? `${hiddenCauses.toLocaleString()} file${hiddenCauses === 1 ? "" : "s"} with open causes not drawn`
              : "every file with an open cause is drawn"}
          {onDrawMore && more > 0 ? (
            <>
              {" · "}
              <button type="button" className={action} disabled={pending} onClick={onDrawMore}>
                Draw {more.toLocaleString()} more
              </button>
            </>
          ) : null}
        </>
      ) : null}
      {onIncludeHeldChange && (held > 0 || scope.includeHeld) ? (
        <>
          {" · "}
          <button
            type="button"
            className={action}
            disabled={pending}
            onClick={() => onIncludeHeldChange(!scope.includeHeld)}
          >
            {scope.includeHeld
              ? "Hide vendored and generated files"
              : `Include ${held.toLocaleString()} vendored and generated file${held === 1 ? "" : "s"}`}
          </button>
        </>
      ) : null}
      {query ? (
        <>
          {" · "}
          <span className="tabular-nums">{matches.toLocaleString()}</span>
          {pinned
            ? ` of ${pinned.matches.toLocaleString()} match${pinned.matches === 1 ? "" : "es"} drawn`
            : ` match${matches === 1 ? "" : "es"} here`}
          {onFindUndrawn && capped && !pinned ? (
            <>
              {" · "}
              <button
                type="button"
                className={action}
                disabled={pending}
                onClick={() => onFindUndrawn(query)}
              >
                Find matches outside the field
              </button>
            </>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
