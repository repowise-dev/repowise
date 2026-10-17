"use client";

/**
 * The Code Health galaxy: one field, one geometry, a switchable lens.
 *
 * This module is the facade. The layout, the lens encodings, the hit grid, the
 * canvas painter and its frame loop, the camera, the pointer and keyboard
 * models, and the pieces of chrome are separate modules under `map/`, and
 * everything a host used to import is still re-exported here. What is left in
 * this file is composition: which hook owns what, and what each layer shows.
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

import { EmptyState } from "../shared/empty-state";
import { usePrefersReducedMotion } from "../hooks/use-prefers-reduced-motion";
import {
  HEALTH_UNSUPPORTED_NOTICE,
  OVERLAY_ORDER,
  OVERLAY_SPECS,
  lensAvailable,
  noFileScored,
} from "./map/lens";
import { HoverCard } from "./map/overlay";
import { GalaxyLabels } from "./map/labels";
import { CanvasChrome, FocusCrumb, ZoomControls } from "./map/controls";
import { MapScopeNote } from "./map/scope-note";
import { useFieldModel } from "./map/use-field-model";
import { useFieldCanvas } from "./map/use-field-canvas";
import { ZOOM_STEP, useMapCamera } from "./map/use-map-camera";
import { useHover, useMapPointer } from "./map/use-map-pointer";
import { useKeyboardCursor, useMapKeyboard, type KeyboardCursor } from "./map/use-map-keyboard";
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
  const spec = OVERLAY_SPECS[overlay] ?? OVERLAY_SPECS.health;
  const containerRef = useRef<HTMLDivElement>(null);
  const baseRef = useRef<HTMLCanvasElement>(null);
  const topRef = useRef<HTMLCanvasElement>(null);
  const keyboardRef = useRef<HTMLDivElement>(null);
  const labelRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const reducedMotion = usePrefersReducedMotion();

  const model = useFieldModel(files, spec, search, highlightPaths);
  const { galaxies, scene } = model;
  const [focusModule, setFocusModule] = useState<string | null>(null);
  const focusIdx = focusModule ? galaxies.findIndex((g) => g.module === focusModule) : -1;
  const kb = useKeyboardCursor(galaxies);

  const { hover, setHovered } = useHover(onHoverFile);
  const hoverIdx = hover ? scene.index.get(hover.file.file_path) ?? -1 : -1;
  const selectedIdx = selectedPath ? scene.index.get(selectedPath) ?? -1 : -1;
  const cursorIdx = kb.node && kb.focused ? scene.index.get(kb.node.file.file_path) ?? -1 : -1;

  const { loop, invalidate } = useFieldCanvas({
    container: containerRef,
    base: baseRef,
    top: topRef,
    labels: labelRefs,
    minHeight,
    reducedMotion,
    labelKey: `${kb.moduleIdx}:${kb.level}`,
    live: {
      scene,
      fills: model.fills,
      focusIdx,
      marked: model.marked,
      selectedIdx,
      cursorIdx,
      hoverIdx,
      minNodeR: model.minNodeR,
      labelOrder: model.labelOrder,
    },
  });
  const camera = useMapCamera({ loop, invalidate, reducedMotion, galaxies, focusModule, setFocusModule });

  // A galaxy that left the field takes its focus with it.
  useEffect(() => {
    if (focusModule && focusIdx < 0) setFocusModule(null);
  }, [focusModule, focusIdx]);

  const onSelectRef = useRef(onSelectFile);
  onSelectRef.current = onSelectFile;
  const handleSelect = useCallback((path: string) => {
    // Clicking a node hands focus to the field's keyboard region, so a reader
    // who reached the map with a pointer can carry on with arrow keys.
    keyboardRef.current?.focus();
    onSelectRef.current?.(path);
  }, []);

  const handlers = useMapPointer({
    container: containerRef,
    loop,
    invalidate,
    scene,
    grid: model.grid,
    minNodeR: model.minNodeR,
    galaxies,
    focusModule,
    focusOn: camera.focusOn,
    onSelect: handleSelect,
    setHovered,
  });

  const onKeyDown = useMapKeyboard({
    container: containerRef,
    kb,
    galaxies,
    camera,
    focusModule,
    setFocusModule,
    onSelect: handleSelect,
  });

  const offered = useMemo(() => lenses.filter((l) => lensAvailable(l, files)), [lenses, files]);

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

  const focusGalaxy = focusIdx >= 0 ? galaxies[focusIdx]! : null;
  const leave = () => {
    kb.setLevel("module");
    camera.fitAll();
  };

  return (
    <div
      ref={containerRef}
      data-testid="map-field"
      data-matches={model.matches.length}
      data-selected={selectedIdx >= 0 ? selectedPath ?? undefined : undefined}
      data-keyboard-cursor={kb.node?.file.file_path}
      className="relative w-full overflow-hidden rounded-xl border border-[var(--color-border-default)] focus-within:ring-2 focus-within:ring-[var(--color-accent-primary)]"
      style={{
        minHeight,
        background:
          "radial-gradient(ellipse at 50% 42%, var(--color-bg-surface), var(--color-bg-root) 75%)",
      }}
    >
      {/* One tab stop for the whole field. Pointer events stay off so it never
          intercepts a click meant for the canvas. */}
      <div
        ref={keyboardRef}
        tabIndex={0}
        role="group"
        aria-label={`Code health map, ${spec.label} lens. Arrow keys move between modules, Enter opens one, plus and minus zoom, 0 fits the field.`}
        onKeyDown={onKeyDown}
        onFocus={() => kb.setFocused(true)}
        onBlur={() => kb.setFocused(false)}
        className="pointer-events-none absolute inset-0 z-10 outline-none"
      />
      <span aria-live="polite" className="sr-only">
        {cursorLabel(kb, spec.label)}
      </span>
      <p className="sr-only">
        {`${scene.nodes.length.toLocaleString()} files drawn in ${galaxies.length} groups, sized by lines of code. ${model.attention.toLocaleString()} need attention under the ${spec.label.toLowerCase()} lens. The list beside the map reaches every one.`}
      </p>

      <canvas ref={baseRef} aria-hidden className="pointer-events-none absolute inset-0" />
      <canvas
        ref={topRef}
        aria-hidden
        data-testid="map-canvas"
        {...handlers}
        onPointerCancel={handlers.onPointerUp}
        className={`absolute inset-0 ${hover && onSelectFile ? "cursor-pointer" : "cursor-grab active:cursor-grabbing"}`}
        style={{ touchAction: "pan-y" }}
      />

      <GalaxyLabels
        galaxies={galaxies}
        refs={labelRefs}
        focusIdx={focusIdx}
        cursorIdx={kb.focused && kb.level === "module" && kb.galaxy ? galaxies.indexOf(kb.galaxy) : -1}
        onToggle={(m) => camera.focusOn(focusModule === m ? null : m)}
      />

      {chrome === "canvas" ? (
        <CanvasChrome
          overlay={overlay}
          spec={spec}
          lenses={offered}
          loading={overlayLoading}
          onOverlayChange={onOverlayChange}
        />
      ) : null}

      <div className="absolute right-3 top-3 z-20 flex flex-col items-end gap-1.5">
        {focusGalaxy ? <FocusCrumb label={focusGalaxy.label} onLeave={leave} /> : null}
        <ZoomControls
          onZoomIn={() => camera.zoomBy(ZOOM_STEP)}
          onZoomOut={() => camera.zoomBy(1 / ZOOM_STEP)}
          onFit={leave}
        />
      </div>

      {scope ? (
        <MapScopeNote
          scope={scope}
          matches={model.matches.length}
          query={model.query}
          drawMoreStep={drawMoreStep}
          pending={pending}
          onDrawMore={onDrawMore}
          onIncludeHeldChange={onIncludeHeldChange}
          onFindUndrawn={onFindUndrawn}
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
          width={loop.current.view.w}
          height={loop.current.view.h}
        />
      ) : null}
    </div>
  );
}

/** What the live region says about the keyboard cursor. */
function cursorLabel(kb: KeyboardCursor, lens: string): string {
  if (kb.level === "file" && kb.node) return `${kb.node.file.file_path}, ${lens} lens`;
  return kb.galaxy ? `${kb.galaxy.label}, ${kb.galaxy.fileCount} files` : "";
}
