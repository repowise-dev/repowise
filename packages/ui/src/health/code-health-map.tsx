"use client";

/**
 * The Code Health area map: every directory a disc inside its parent, one
 * geometry, a switchable lens, and semantic zoom from the repository down to
 * single files.
 *
 * This module is the facade. The hierarchy, its layout, the lens encodings,
 * the per-frame zoom decisions, the painter and its frame loop, the camera,
 * the pointer and keyboard models, and the pieces of chrome are separate
 * modules under `map/`; what is left here is composition.
 *
 * The geometry is deterministic and lens-independent on purpose. A reader
 * builds spatial memory of this field, and switching a lens must recolor it
 * rather than redraw it, or that memory is worth nothing.
 *
 * Two ways to feed it. A host with the areas API passes `source` (see
 * `useAreaSource`): the hierarchy and its files load as the reader zooms, and
 * the rollups are the server's. A host with a flat file feed passes `files`
 * and the hierarchy is derived from them. Both draw the same map.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EmptyState } from "../shared/empty-state";
import { Breadcrumb } from "../shared/breadcrumb";
import { usePrefersReducedMotion } from "../hooks/use-prefers-reduced-motion";
import { ROOT_AREA } from "../lib/areas";
import type { MapLens } from "./map/lens";
import { OVERLAY_ORDER, lensAvailable, resolveLens } from "./map/lens-registry";
import { deriveAreaIndex } from "./map/areas";
import { chainEnd, displayLabel } from "./map/area-paths";
import { AreaLabels } from "./map/labels";
import { FieldHover, FieldNotes, useAreaChrome } from "./map/field-notes";
import { CanvasChrome, ZoomControls } from "./map/controls";
import { useFieldModel } from "./map/use-field-model";
import { useFieldCanvas } from "./map/use-field-canvas";
import { ZOOM_STEP, useMapCamera } from "./map/use-map-camera";
import { useHover, useMapPointer } from "./map/use-map-pointer";
import { useKeyboardCursor, useMapKeyboard, type KeyboardCursor } from "./map/use-map-keyboard";
import type { AreaSource } from "./map/use-area-source";
import { DEFAULT_MAP_CANVAS_LABELS, type MapCanvasLabels } from "./map/canvas-labels";
import type { HeldGroups } from "./map/scope-note";
import type { CodeHealthMapFile, CodeHealthOverlay, MapScope } from "./map/types";

export type { CodeHealthMapFile, CodeHealthOverlay, FileNode, MapScope, PerformanceActionability } from "./map/types";
export {
  BAND_LEGEND,
  HEALTH_UNSUPPORTED_LABEL,
  HEALTH_UNSUPPORTED_NOTICE,
  NEUTRAL_FILL,
  PERFORMANCE_STATE_LABEL,
  QUIET_FILL,
  burdenBand,
  noFileScored,
  performanceBurden,
  performanceFill,
  performanceSentence,
  scoreText,
} from "./map/lens";
export type {
  LegendRow,
  LensContext,
  MapLens,
  OverlaySpec,
  PerformanceBurden,
  PerformanceBurdenUnit,
  PerformanceNodeState,
} from "./map/lens";
export { OVERLAY_ORDER, OVERLAY_SPECS, lensAvailable, resolveLens } from "./map/lens-registry";
export { KNOWLEDGE_LENS, ownerLens, rankOwners, shareFill } from "./map/area-lenses";
export { EMPTY_AREA_INDEX, deriveAreaIndex, mergeAreaTree, type AreaIndex, type MapArea } from "./map/areas";
export { areaTrail, chainEnd, displayChildren, displayLabel, displayParent } from "./map/area-paths";
export { layoutAreas, layoutFiles } from "./map/area-layout";
export { WORLD, fitCamera } from "./map/layout";
export { buildHitGrid, hitTest } from "./map/hit-test";
export { MapLegend, MapLensSwitcher } from "./map/legend";
export { SEARCH_MARK_CAP } from "./map/overlay";
export { useAreaSource, type AreaFetchers, type AreaSource } from "./map/use-area-source";
export { DEFAULT_MAP_CANVAS_LABELS, type MapCanvasLabels } from "./map/canvas-labels";
export type { HeldGroups } from "./map/scope-note";

/** What the root disc is called in the breadcrumb and the key. */
const ROOT_LABEL = "Repository";
const DEFAULT_ARIA_LABEL = (lens: string) =>
  `Code health map, ${lens} lens. Arrow keys move between the areas and files inside the selected area, Enter opens one, Backspace goes up a level, Escape returns to the repository, plus and minus zoom.`;

export interface CodeHealthMapProps {
  /** The flat file feed. Ignored when `source` is given. */
  files: CodeHealthMapFile[];
  /** The lazily loaded hierarchy, for a host with the areas API. */
  source?: AreaSource;
  /** Selected area; `""` is the repository. The camera frames it. */
  area?: string;
  /** Area click, breadcrumb, Backspace (up) and Escape (root). */
  onAreaChange?: (area: string) => void;
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
  /** Which built-in lens recolors the field. Defaults to `health`. */
  overlay?: CodeHealthOverlay;
  /**
   * A lens of the host's own, which wins over `overlay`. This is how another
   * surface (off-boarding, coupling) colours the same map: build a
   * {@link MapLens} over its data and pass it here and to `MapLegend`.
   */
  lens?: MapLens;
  /** When provided, the lens is switchable from the on-canvas chrome. */
  onOverlayChange?: (overlay: CodeHealthOverlay) => void;
  /** Lenses offered in the switcher. Defaults to {@link OVERLAY_ORDER}. */
  lenses?: CodeHealthOverlay[];
  /** The active lens's per-file signal is still loading. */
  overlayLoading?: boolean;
  /** What the server drew and what it left out, for a flat, capped feed. */
  scope?: MapScope;
  /**
   * Where the lens switcher and key live: `"canvas"` (default) floats them
   * over the map, `"none"` leaves them to the host.
   */
  chrome?: "canvas" | "none";
  /** Ask the host for a bigger flat field. */
  onDrawMore?: () => void;
  drawMoreStep?: number;
  onIncludeHeldChange?: (include: boolean) => void;
  onFindUndrawn?: (query: string) => void;
  /** A host request for a new field is in flight. */
  pending?: boolean;
  /** The root disc's name. */
  rootLabel?: string;
  /** The canvas's accessible name and key help, for the active lens's label. */
  ariaLabel?: (lens: string) => string;
  /** The words the canvas prints (folder chips, hover card), for translation. */
  labels?: Partial<MapCanvasLabels>;
  /** Groups held out of the map, toggled back from the canvas footer. */
  heldGroups?: HeldGroups;
}

/** The selected area, owned by the host when it passes `onAreaChange`, local otherwise. */
function useSelectedArea(area: string | undefined, onAreaChange: ((a: string) => void) | undefined) {
  const [local, setLocal] = useState(ROOT_AREA);
  const selected = area ?? local;
  const ref = useRef(onAreaChange);
  ref.current = onAreaChange;
  const select = useCallback((next: string) => {
    if (ref.current) ref.current(next);
    else setLocal(next);
  }, []);
  return [selected, select] as const;
}

export function CodeHealthMap({
  files,
  source,
  area,
  onAreaChange,
  selectedPath,
  search,
  highlightPaths,
  onSelectFile,
  onHoverFile,
  minHeight = 640,
  overlay = "health",
  lens: hostLens,
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
  rootLabel = ROOT_LABEL,
  ariaLabel = DEFAULT_ARIA_LABEL,
  labels: labelOverrides,
  heldGroups,
}: CodeHealthMapProps) {
  const words = useMemo(() => ({ ...DEFAULT_MAP_CANVAS_LABELS, ...labelOverrides }), [labelOverrides]);
  const containerRef = useRef<HTMLDivElement>(null);
  const baseRef = useRef<HTMLCanvasElement>(null);
  const topRef = useRef<HTMLCanvasElement>(null);
  const keyboardRef = useRef<HTMLDivElement>(null);
  const labelRefs = useRef(new Map<string, HTMLElement>());
  const reducedMotion = usePrefersReducedMotion();

  const derived = useMemo(() => (source ? null : deriveAreaIndex(files)), [source, files]);
  const index = source?.index ?? derived!;
  const fieldFiles = source?.files ?? files;
  const lens = useMemo(() => hostLens ?? resolveLens(overlay, index, words), [hostLens, overlay, index, words]);
  const model = useFieldModel(index, fieldFiles, lens, search, highlightPaths);
  const { scene, geo } = model;

  const [rawArea, selectArea] = useSelectedArea(area, onAreaChange);
  const selected = chainEnd(index, rawArea);
  const reveal = source?.reveal;
  useEffect(() => {
    if (reveal && rawArea !== ROOT_AREA && !index.areas.has(rawArea)) reveal(rawArea);
  }, [reveal, rawArea, index]);

  const kb = useKeyboardCursor(index, scene, selected);
  const { hover, setHovered } = useHover(onHoverFile);
  const hoverIdx = hover?.kind === "file" ? (scene.index.get(hover.file.file_path) ?? -1) : -1;
  const selectedIdx = selectedPath ? (scene.index.get(selectedPath) ?? -1) : -1;
  const cursorItem = kb.focused ? kb.item : null;
  const cursorIdx = cursorItem?.kind === "file" ? (scene.index.get(cursorItem.path) ?? -1) : -1;

  const allFilesCovered = useCallback(() => true, []);
  const { loop, invalidate, ready } = useFieldCanvas({
    container: containerRef,
    base: baseRef,
    top: topRef,
    labels: labelRefs,
    minHeight,
    reducedMotion,
    labelKey: `${selected}:${cursorItem?.path ?? ""}:${lens.id}`,
    live: {
      index,
      geo,
      scene,
      fills: model.fills,
      areaFill: model.areaFill,
      marked: model.marked,
      selectedIdx,
      cursorIdx,
      hoverIdx,
      selectedArea: selected === ROOT_AREA ? null : selected,
      hoverArea: hover?.kind === "area" && !hover.slot && hover.path !== ROOT_AREA ? hover.path : null,
      cursorArea: cursorItem?.kind === "area" ? cursorItem.path : null,
      minNodeR: model.minNodeR,
      filesCovered: source?.filesCovered ?? allFilesCovered,
      onWants: source?.request,
    },
  });
  const camera = useMapCamera({ loop, invalidate, reducedMotion, geo, area: selected, ready });

  const onSelectRef = useRef(onSelectFile);
  onSelectRef.current = onSelectFile;
  const handleSelectFile = useCallback((path: string) => {
    // Clicking a node hands focus to the field's keyboard region, so a reader
    // who reached the map with a pointer can carry on with arrow keys.
    keyboardRef.current?.focus({ preventScroll: true });
    onSelectRef.current?.(path);
  }, []);
  const handleSelectArea = useCallback(
    (path: string) => {
      keyboardRef.current?.focus({ preventScroll: true });
      selectArea(chainEnd(index, path));
    },
    [index, selectArea],
  );

  const handlers = useMapPointer({
    container: containerRef,
    loop,
    invalidate,
    scene,
    grid: model.grid,
    minNodeR: model.minNodeR,
    onSelectFile: handleSelectFile,
    onSelectArea: handleSelectArea,
    setHovered,
  });

  const onKeyDown = useMapKeyboard({
    container: containerRef,
    kb,
    index,
    area: selected,
    camera,
    onSelectArea: handleSelectArea,
    onSelectFile: handleSelectFile,
  });

  const { labels, trail } = useAreaChrome(index, geo, model.areaText, selected, rootLabel, handleSelectArea);
  const offered = useMemo(
    () => lenses.filter((l) => lensAvailable(l, fieldFiles, index)),
    [lenses, fieldFiles, index],
  );

  if (!source && files.length === 0) {
    return (
      <div ref={containerRef} className="flex w-full items-center justify-center" style={{ minHeight }}>
        <EmptyState size="compact" title="No files to map yet" description="Index this repo to populate health." />
      </div>
    );
  }

  const root = index.areas.get(ROOT_AREA);
  const flatActions = { drawMoreStep, pending, onDrawMore, onIncludeHeldChange, onFindUndrawn };
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div data-testid="map-breadcrumb" className="min-h-5 min-w-0 font-mono text-xs">
        <Breadcrumb segments={trail} />
      </div>
      <div
        ref={containerRef}
        data-testid="map-field"
        data-area={selected}
        data-matches={model.matches.length}
        data-selected={selectedIdx >= 0 ? (selectedPath ?? undefined) : undefined}
        data-keyboard-cursor={cursorItem?.path}
        className="relative w-full overflow-hidden rounded-xl border border-[var(--color-border-default)] has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[var(--color-focus-ring)]"
        style={{
          minHeight,
          background: "radial-gradient(ellipse at 50% 42%, var(--color-bg-surface), var(--color-bg-root) 75%)",
        }}
      >
        {/* One tab stop for the whole field. Pointer events stay off so it never
            intercepts a click meant for the canvas. */}
        <div
          ref={keyboardRef}
          tabIndex={0}
          role="group"
          aria-label={ariaLabel(lens.label)}
          onKeyDown={onKeyDown}
          // The cursor ring answers to keys, so it shows only for keyboard focus.
          onFocus={(e) => kb.setFocused(e.currentTarget.matches(":focus-visible"))}
          onBlur={() => kb.setFocused(false)}
          className="pointer-events-none absolute inset-0 z-10 outline-none"
        />
        <span aria-live="polite" className="sr-only">
          {cursorLabel(kb, index, rootLabel)}
        </span>
        <p className="sr-only">
          {`${root ? root.fileCount.toLocaleString() : fieldFiles.length.toLocaleString()} files in ${Math.max(index.areas.size - 1, 0)} folders, each folder a disc sized by lines of code. The list beside the map reaches every area and file.`}
        </p>

        <canvas ref={baseRef} aria-hidden className="pointer-events-none absolute inset-0" />
        <canvas
          ref={topRef}
          aria-hidden
          data-testid="map-canvas"
          {...handlers}
          onPointerCancel={handlers.onPointerUp}
          className={`absolute inset-0 ${hover ? "cursor-pointer" : "cursor-grab active:cursor-grabbing"}`}
          style={{ touchAction: "pan-y" }}
        />

        <AreaLabels
          labels={labels}
          refs={labelRefs}
          selected={selected}
          cursor={cursorItem?.kind === "area" ? cursorItem.path : null}
          onSelect={handleSelectArea}
        />

        {chrome === "canvas" ? (
          <CanvasChrome
            overlay={overlay}
            spec={lens}
            lenses={offered}
            loading={overlayLoading}
            onOverlayChange={onOverlayChange}
          />
        ) : null}

        <div className="absolute right-3 top-3 z-20">
          <ZoomControls
            onZoomIn={() => camera.zoomBy(ZOOM_STEP)}
            onZoomOut={() => camera.zoomBy(1 / ZOOM_STEP)}
            onFit={camera.reframe}
          />
        </div>

        <FieldNotes
          flat={source ? null : { files, scope, ...flatActions }}
          loaded={source && root ? { loaded: scene.nodes.length, total: root.fileCount } : null}
          matches={model.matches.length}
          query={model.query}
          held={heldGroups}
          labels={words}
        />
        <FieldHover
          hover={hover}
          analysed={model.areaAnalysed}
          areaText={model.areaText}
          labels={words}
          index={index}
          lens={lens}
          overlay={overlay}
          rootLabel={rootLabel}
          width={loop.current.view.w}
          height={loop.current.view.h}
        />
      </div>
    </div>
  );
}

/** What the live region says about the keyboard cursor. */
function cursorLabel(kb: KeyboardCursor, index: Parameters<typeof displayLabel>[0], rootLabel: string): string {
  const item = kb.focused ? kb.item : null;
  if (!item) return "";
  if (item.kind === "file") return item.path;
  const a = index.areas.get(item.path);
  return a ? `${displayLabel(index, item.path, rootLabel)}, ${a.fileCount.toLocaleString()} files` : "";
}
