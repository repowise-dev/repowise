"use client";

/**
 * React host for the continuous-zoom canvas. Owns the `<canvas>`, the
 * `ZoomRenderer` instance, the minimap and the interaction layer: wheel-zoom,
 * drag-pan, keyboard, hover popover and click-to-select. It is a reusable
 * primitive: navigation chrome (breadcrumb, search box, controls, detail panel,
 * URL sync) lives in the host page and drives the canvas through the imperative
 * handle and the `onFocusChange` / `onSelect` callbacks, so web and hosted
 * share the exact same canvas. Every command (key, pointer, handle) goes
 * through `ZoomActions`, so a button and its shortcut cannot drift.
 */

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { type Camera, type Viewport, clampCamera, fitScale } from "./camera";
import { type FlyOptions, ZoomRenderer, type FrameStats } from "./renderer";
import { buildScene, type ZoomScene } from "./scene";
import { sceneRootRect, settleRootAspect } from "./relayout";
import { resolveZoomPalette } from "./theme";
import type { ZoomMap, ZoomNode, ZoomRelation } from "./types";
import { HoverCard } from "./hover-card";
import { ZoomActions } from "./zoom-actions";
import { DEFAULT_ZOOM_CANVAS_LABELS, type ZoomCanvasLabels } from "./zoom-canvas-labels";
import { useZoomHover } from "./use-zoom-hover";
import { useZoomInput } from "./use-zoom-input";
import { ZoomMinimap, type ZoomMinimapHandle } from "./ZoomMinimap";
import { useThemeVersion } from "../shared/use-theme-tokens";
import { usePrefersReducedMotion } from "../hooks/use-prefers-reduced-motion";

export interface ZoomCanvasHandle {
  /** Fly the camera to frame a node by id. Returns false if it is not laid out. */
  flyTo: (id: string, opts?: FlyOptions) => boolean;
  /** Fly back to the whole-system overview. */
  reset: (opts?: FlyOptions) => void;
  /** Zoom about the viewport centre; a factor above 1 zooms in. */
  zoomBy: (factor: number) => void;
  /** Fly out to the parent of the focused node (the overview from one level down). */
  up: () => void;
  /**
   * Select a node by id, or clear with null. Fires `onSelect` and pans the node
   * clear of `selectionInsetRight`, so the host never pokes the renderer.
   */
  select: (id: string | null) => void;
  /** Move keyboard focus to the canvas (e.g. after the host closes a sheet). */
  focus: () => void;
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
  /**
   * Draw only relations whose verb is in this set, or every relation when
   * unset. See `DrawOptions.relationVerbs` for why this is a set.
   */
  relationVerbs?: ReadonlySet<string> | null;
  /**
   * Dim every card outside this set of node ids (a container picked in the
   * map key), or none when unset. Build it once per pick, not per render.
   */
  highlightIds?: ReadonlySet<string> | null;
  /**
   * Relations incident to a node, for the hover card's one-line summary. The
   * host already indexes these for its detail panel, so it passes the lookup in
   * rather than the canvas walking the relation list a second time.
   */
  relationsByNode?: Map<string, ZoomRelation[]>;
  /**
   * Screen px the host covers on the canvas's right edge while a node is
   * selected (a detail sheet). Selecting a node under that strip pans it clear;
   * the canvas itself never resizes for it.
   */
  selectionInsetRight?: number;
  /** Placement classes for the minimap, which shows once you zoom in. */
  minimapClassName?: string;
  /** Accessible names and hover-card copy, so a host can pass translated ones. */
  labels?: Partial<ZoomCanvasLabels>;
}

/** The minimap appears once the camera is this far past the overview fit. */
const MINIMAP_ZOOM_RATIO = 1.15;
/** Scratch viewport for the minimap threshold, so a camera frame allocates nothing. Written and read synchronously in `onView`. */
const WHOLE_VP: Viewport = { w: 1, h: 1 };
/** A resize settles this long before the overview is laid out to the new shape. */
const RELAYOUT_DEBOUNCE_MS = 200;
/** What the commands see in the one commit before the canvas is measured. */
const UNMEASURED_SCENE: ZoomScene = {
  rootId: "",
  nodes: new Map(),
  worldRects: new Map(),
  relationsByParent: new Map(),
  drawChildren: new Map(),
  externalLanes: new Map(),
  laidOutCount: 0,
};


export const ZoomCanvas = forwardRef<ZoomCanvasHandle, ZoomCanvasProps>(function ZoomCanvas(
  {
    data,
    className,
    onSelect,
    onFocusChange,
    initialFocusId,
    showStats,
    relationVerbs = null,
    highlightIds = null,
    relationsByNode,
    selectionInsetRight = 0,
    minimapClassName = "bottom-3 left-3",
    labels,
  },
  ref,
) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rendererRef = useRef<ZoomRenderer | null>(null);
  const minimapRef = useRef<ZoomMinimapHandle | null>(null);
  const themeVersion = useThemeVersion();
  const reducedMotion = usePrefersReducedMotion();
  const reducedRef = useRef(reducedMotion);
  reducedRef.current = reducedMotion;

  // The overview packs to the canvas shape (see `rootRect`), so nothing is laid
  // out until the canvas is measured (before the first paint); after that it
  // is re-laid out only once a resize settles on a new shape.
  const [rootAspect, setRootAspect] = useState<number | null>(null);
  useLayoutEffect(() => {
    const c = canvasRef.current;
    if (c) setRootAspect(settleRootAspect(1, c.clientWidth, c.clientHeight));
  }, []);
  const scene = useMemo(
    () => (rootAspect === null ? null : buildScene(data, { rootAspect })),
    [data, rootAspect],
  );
  const copy = useMemo(() => ({ ...DEFAULT_ZOOM_CANVAS_LABELS, ...labels }), [labels]);
  const sceneRef = useRef(scene ?? UNMEASURED_SCENE);
  sceneRef.current = scene ?? UNMEASURED_SCENE;
  // Node index is derived from the data (stable identity) so the interaction
  // listeners are not torn down when the scene is rebuilt.
  const nodeById = useMemo(
    () => new Map(data.nodes.map((n) => [n.id, n] as const)),
    [data],
  );
  const nodeByIdRef = useRef(nodeById);
  nodeByIdRef.current = nodeById;
  const insetRef = useRef(selectionInsetRight);
  insetRef.current = selectionInsetRight;

  const [stats, setStats] = useState<FrameStats | null>(null);
  // Flips only when crossing the threshold, so a zoom re-renders React at most
  // twice, never per frame.
  const [zoomedIn, setZoomedIn] = useState(false);
  const zoomedRef = useRef(false);

  // Keep the latest callbacks in refs so the renderer and the native event
  // listeners (each created once) always call the current closure without being
  // torn down and recreated on every render.
  const onFocusRef = useRef(onFocusChange);
  onFocusRef.current = onFocusChange;
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const onStatsRef = useRef<((s: FrameStats) => void) | undefined>(undefined);
  onStatsRef.current = showStats ? setStats : undefined;
  const { hover, hideHover, onHoverRef, cardRef: hoverCardRef } = useZoomHover(canvasRef, nodeById);

  const actions = useMemo(
    () =>
      new ZoomActions({
        renderer: () => rendererRef.current,
        scene: () => sceneRef.current,
        node: (id) => nodeByIdRef.current.get(id),
        insetRight: () => insetRef.current,
        reducedMotion: () => reducedRef.current,
        onSelect: (node) => onSelectRef.current?.(node),
      }),
    [],
  );

  useImperativeHandle(
    ref,
    (): ZoomCanvasHandle => ({
      flyTo: actions.flyTo,
      reset: actions.fit,
      zoomBy: actions.zoomBy,
      up: actions.up,
      select: (id) => actions.select(id),
      focus: () => canvasRef.current?.focus(),
    }),
    [actions],
  );

  // Per camera frame: blit the minimap and flip `zoomedIn` on a threshold.
  const onView = useCallback((cam: Camera, vp: Viewport) => {
    // Against the whole canvas: an open sheet makes the overview fit smaller,
    // which must not count as zooming in.
    WHOLE_VP.w = vp.w;
    WHOLE_VP.h = vp.h;
    const zoomed = cam.scale > fitScale(WHOLE_VP, sceneRootRect(sceneRef.current)) * MINIMAP_ZOOM_RATIO;
    if (zoomed !== zoomedRef.current) {
      zoomedRef.current = zoomed;
      setZoomedIn(zoomed);
    }
    minimapRef.current?.drawView(cam, vp);
  }, []);

  // The minimap mounts after the frame that showed it, so paint it on attach.
  const attachMinimap = useCallback((handle: ZoomMinimapHandle | null) => {
    minimapRef.current = handle;
    const r = rendererRef.current;
    if (handle && r) handle.drawView(r.getCamera(), r.getViewport());
  }, []);

  const onMinimapNavigate = useCallback(({ wx, wy }: { wx: number; wy: number }) => {
    const r = rendererRef.current;
    if (r) r.setCamera(clampCamera({ cx: wx, cy: wy, scale: r.getCamera().scale }));
  }, []);

  // Create the renderer once the canvas mounts.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const renderer = new ZoomRenderer({
      canvas,
      palette: resolveZoomPalette(),
      onStats: (s) => onStatsRef.current?.(s),
      onFocus: (chain) => onFocusRef.current?.(chain),
      onView,
      onHover: (hit, sx, sy) => onHoverRef.current(hit, sx, sy),
    });
    rendererRef.current = renderer;

    let relayout: ReturnType<typeof setTimeout> | undefined;
    const resizeObs = new ResizeObserver(() => {
      renderer.resize();
      actions.refitInset();
      clearTimeout(relayout);
      relayout = setTimeout(
        () => setRootAspect((a) => settleRootAspect(a ?? 1, canvas.clientWidth, canvas.clientHeight)),
        RELAYOUT_DEBOUNCE_MS,
      );
    });
    resizeObs.observe(canvas);

    return () => {
      clearTimeout(relayout);
      resizeObs.disconnect();
      actions.dispose();
      renderer.destroy();
      rendererRef.current = null;
    };
  }, [onView, actions]);

  // Feed the scene + repaint. Only new data can drop or change the selected
  // node; a re-layout keeps it, so the host is not told again.
  const resyncedData = useRef<ZoomMap | null>(null);
  useEffect(() => {
    if (!scene) return;
    rendererRef.current?.setScene(scene);
    if (resyncedData.current !== data) {
      resyncedData.current = data;
      actions.resync();
    }
  }, [scene, data, actions]);

  useEffect(() => {
    rendererRef.current?.setRelationVerbs(relationVerbs);
  }, [relationVerbs]);

  useEffect(() => {
    rendererRef.current?.setHighlight(highlightIds);
  }, [highlightIds]);

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

  useZoomInput(canvasRef, rendererRef, actions, hideHover);

  return (
    <div className={`relative h-full w-full overflow-hidden ${className ?? ""}`}>
      <canvas
        ref={canvasRef}
        tabIndex={0}
        role="application"
        aria-label={copy.canvas}
        className="h-full w-full touch-none outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--color-focus-ring)]"
        style={{ cursor: "grab" }}
      />
      {zoomedIn && scene && (
        <div className={`absolute z-10 ${minimapClassName}`}>
          <ZoomMinimap
            ref={attachMinimap}
            scene={scene}
            onNavigate={onMinimapNavigate}
            label={copy.minimap}
          />
        </div>
      )}
      {hover && (
        <HoverCard
          hover={hover}
          canvas={canvasRef.current}
          cardRef={hoverCardRef}
          relationsByNode={relationsByNode}
          relationVerbs={relationVerbs}
          nodeById={nodeById}
          labels={copy}
        />
      )}
      {showStats && stats && <StatsOverlay stats={stats} laidOut={scene?.laidOutCount ?? 0} />}
    </div>
  );
});

function StatsOverlay({ stats, laidOut }: { stats: FrameStats; laidOut: number }) {
  return (
    <div className="pointer-events-none absolute left-2 top-2 z-10 rounded bg-[var(--color-bg-overlay)] px-2 py-1 font-mono text-[10px] text-[var(--color-text-secondary)]">
      {stats.drawn} drawn · {stats.culled} culled · depth {stats.maxDepthDrawn} ·{" "}
      {stats.frameMs.toFixed(2)}ms · {laidOut} nodes
    </div>
  );
}
