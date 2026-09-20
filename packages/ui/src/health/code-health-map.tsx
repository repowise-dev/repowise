"use client";

/**
 * The Code Health galaxy: one field, one geometry, a switchable lens.
 *
 * This module is the facade. The layout, the lens encodings, the base node
 * layer, the pointer overlay, and the key are separate modules under `map/`,
 * and everything they export is re-exported here so a host's import path never
 * had to change. What is left in this file is composition: sizing, camera,
 * selection, keyboard navigation, and which layer sees which prop.
 *
 * The geometry is deterministic and lens-independent on purpose. A reader
 * builds spatial memory of this field, and switching a lens must recolor it
 * rather than redraw it, or that memory is worth nothing.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
} from "react";

import { useCommunityFamilies } from "../shared/use-theme-tokens";
import { OVERLAY_ORDER, OVERLAY_SPECS } from "./map/lens";
import { packGalaxies, rand } from "./map/layout";
import { FileNodes } from "./map/node-layer";
import { HoverCard, NodeHighlight, SearchMatches } from "./map/overlay";
import { MapLegendRows, MapLensSwitcher } from "./map/legend";
import type { CodeHealthMapFile, CodeHealthOverlay, FileNode, MapScope } from "./map/types";

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
  NEUTRAL_FILL,
  OVERLAY_ORDER,
  OVERLAY_SPECS,
  PERFORMANCE_STATE_LABEL,
  burdenBand,
  performanceBurden,
  performanceFill,
  performanceSentence,
} from "./map/lens";
export type {
  LegendRow,
  OverlaySpec,
  PerformanceBurden,
  PerformanceBurdenUnit,
  PerformanceNodeState,
} from "./map/lens";
export { groupByModule, moduleColorId, packGalaxies } from "./map/layout";
export { MapLegend, MapLensSwitcher } from "./map/legend";
export { SEARCH_MARK_CAP } from "./map/overlay";
export { FIELD_LIST_CAP, MapFieldList, MapInspector } from "./map/inspector";

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
  /** Hover feeds the host's inspector. */
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
}

/** Label a galaxy once its on-screen footprint clears this radius (px). */
const LABEL_MIN_SCREEN_R = 40;
/** Spokes drawn from each hub to its N biggest files (overview / focused). */
const SPOKES_OVERVIEW = 8;
const SPOKES_FOCUSED = 36;

type KeyboardLevel = "module" | "file";

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
}: CodeHealthMapProps) {
  const overlaySpec = OVERLAY_SPECS[overlay] ?? OVERLAY_SPECS.health;
  const containerRef = useRef<HTMLDivElement>(null);
  const keyboardRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ width: 0, height: 0 });
  const [focusModule, setFocusModule] = useState<string | null>(null);
  const [hovered, setHovered] = useState<CodeHealthMapFile | null>(null);
  // Where the hover card sits, in container-relative px. Tracked only while a
  // node is hovered: a card that follows the pointer needs the position, and
  // paying for it across the whole canvas would re-render the facade on every
  // frame of every sweep that identifies nothing.
  const [pointer, setPointer] = useState<{ x: number; y: number } | null>(null);
  const hoveredRef = useRef<CodeHealthMapFile | null>(null);
  hoveredRef.current = hovered;
  const moveRaf = useRef<number | null>(null);
  // Keyboard cursor. One tab stop reaches the whole field: arrows move over
  // modules, Enter descends into one, arrows then move over its files. The
  // alternative is a tab stop per node, which is thousands of them.
  const [kbLevel, setKbLevel] = useState<KeyboardLevel>("module");
  const [kbModule, setKbModule] = useState(0);
  const [kbFile, setKbFile] = useState(0);
  const famFor = useCommunityFamilies();

  // Latest callbacks held in refs so the node-layer handlers stay referentially
  // stable across renders (the host re-renders on every hover to drive its
  // inspector). Stable handlers let the memoized layers skip reconciling their
  // thousands of circles when only a highlight changed.
  const onSelectRef = useRef(onSelectFile);
  onSelectRef.current = onSelectFile;
  const onHoverRef = useRef(onHoverFile);
  onHoverRef.current = onHoverFile;
  const handleSelect = useCallback((path: string) => {
    // Clicking a node hands focus to the field's keyboard region, so a reader
    // who reached the map with a pointer can carry on with arrow keys instead
    // of having to tab back into it.
    keyboardRef.current?.focus();
    onSelectRef.current?.(path);
  }, []);
  /** Pointer position relative to the container, or null if it has no box. */
  const localPoint = useCallback((clientX: number, clientY: number) => {
    const rect = containerRef.current?.getBoundingClientRect();
    return rect ? { x: clientX - rect.left, y: clientY - rect.top } : null;
  }, []);
  const handleHoverEnter = useCallback(
    (f: CodeHealthMapFile, e: ReactMouseEvent) => {
      // Seed the position from the entering event rather than waiting for the
      // next move, so the card opens where the pointer is instead of wherever
      // it last was.
      setPointer(localPoint(e.clientX, e.clientY));
      setHovered(f);
      onHoverRef.current?.(f);
    },
    [localPoint],
  );
  const handleHoverLeave = useCallback((f: CodeHealthMapFile) => {
    setHovered((h) => (h === f ? null : h));
  }, []);
  const handleCanvasMove = useCallback(
    (e: ReactMouseEvent) => {
      if (!hoveredRef.current || moveRaf.current != null) return;
      const { clientX, clientY } = e;
      // One position per frame. A raw mousemove fires far more often than the
      // card can be repainted, and each one would be a facade render.
      moveRaf.current = requestAnimationFrame(() => {
        moveRaf.current = null;
        setPointer(localPoint(clientX, clientY));
      });
    },
    [localPoint],
  );
  useEffect(
    () => () => {
      if (moveRaf.current != null) cancelAnimationFrame(moveRaf.current);
    },
    [],
  );

  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((es) => {
      const first = es[0];
      if (!first) return;
      const { width, height } = first.contentRect;
      setDims({ width, height: Math.max(height, minHeight) });
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [minHeight]);

  // Escape exits a focused galaxy, while the focus is inside the field. It
  // used to be bound to the window, which meant dismissing anything else on
  // the page - the file drawer this map now opens, most of all - also reset
  // the zoom underneath it.
  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFocusModule(null);
    };
    node.addEventListener("keydown", onKey);
    return () => node.removeEventListener("keydown", onKey);
  }, []);

  const S = Math.max(0, Math.min(dims.width, dims.height));

  const galaxies = useMemo(() => packGalaxies(files, S), [files, S]);

  const byModule = useMemo(() => new Map(galaxies.map((g) => [g.module, g])), [galaxies]);

  // path -> laid-out node, so the overlays can position themselves without
  // re-rendering the base layer on hover, selection, or a keystroke.
  const nodeIndex = useMemo(() => {
    const m = new Map<string, FileNode>();
    for (const g of galaxies) for (const nd of g.nodes) m.set(nd.file.file_path, nd);
    return m;
  }, [galaxies]);

  // Starfield: static, regenerated only on resize.
  const stars = useMemo(() => {
    const { width, height } = dims;
    if (width === 0) return [] as { x: number; y: number; r: number }[];
    const count = Math.min(160, Math.floor((width * height) / 9000));
    return Array.from({ length: count }, (_, i) => ({
      x: rand(i) * width,
      y: rand(i + 1000) * height,
      r: 0.4 + rand(i + 2000) * 1.3,
    }));
  }, [dims]);

  const q = (search ?? "").trim().toLowerCase();
  // Matching runs over the loaded field only and produces a bounded list of
  // paths for the overlay. Neither the layout nor the base layer sees the
  // query, so typing repaints the marks and nothing else.
  const matches = useMemo(() => {
    if (q.length === 0) return [] as string[];
    const out: string[] = [];
    for (const path of nodeIndex.keys()) {
      if (path.toLowerCase().includes(q)) out.push(path);
    }
    return out;
  }, [q, nodeIndex]);

  const marked = useMemo(() => {
    if (!highlightPaths?.length) return matches;
    return [...new Set([...highlightPaths, ...matches])];
  }, [highlightPaths, matches]);

  const focusGalaxy = focusModule ? byModule.get(focusModule) ?? null : null;

  const kbGalaxy = galaxies[Math.min(kbModule, Math.max(0, galaxies.length - 1))] ?? null;
  const kbNode =
    kbLevel === "file" && kbGalaxy
      ? kbGalaxy.nodes[Math.min(kbFile, Math.max(0, kbGalaxy.nodes.length - 1))] ?? null
      : null;

  const onCanvasKeyDown = useCallback(
    (e: ReactKeyboardEvent) => {
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
          setFocusModule(kbGalaxy.module);
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
    [kbLevel, kbGalaxy, kbNode, galaxies.length, handleSelect],
  );

  const offX = (dims.width - S) / 2;
  const offY = (dims.height - S) / 2;
  const k = focusGalaxy ? (S * 0.9) / (2 * focusGalaxy.R) : 1;
  const camX = focusGalaxy ? focusGalaxy.cx : S / 2;
  const camY = focusGalaxy ? focusGalaxy.cy : S / 2;
  const tx = dims.width / 2 - k * (camX + offX);
  const ty = dims.height / 2 - k * (camY + offY);
  const toScreen = (x: number, y: number) => ({ x: k * (x + offX) + tx, y: k * (y + offY) + ty });

  if (S === 0) {
    return (
      <div
        ref={containerRef}
        className="w-full rounded-xl bg-[var(--color-bg-root)]"
        style={{ minHeight }}
      />
    );
  }

  if (files.length === 0) {
    return (
      <div
        ref={containerRef}
        className="flex w-full items-center justify-center rounded-xl border border-dashed border-[var(--color-border-default)] bg-[var(--color-bg-root)] text-sm text-[var(--color-text-tertiary)]"
        style={{ minHeight }}
      >
        No files to map yet. Index this repo to populate health.
      </div>
    );
  }

  const labelled = galaxies.filter((g) => focusGalaxy === g || g.R * k >= LABEL_MIN_SCREEN_R);
  const spokeCap = focusGalaxy ? SPOKES_FOCUSED : SPOKES_OVERVIEW;
  const cursorLabel =
    kbLevel === "file" && kbNode
      ? `${kbNode.file.file_path}, ${overlaySpec.label} lens`
      : kbGalaxy
        ? `${kbGalaxy.module}, ${kbGalaxy.fileCount} files`
        : "";

  return (
    <div
      ref={containerRef}
      onMouseMove={handleCanvasMove}
      className="relative w-full overflow-hidden rounded-xl border border-[var(--color-border-default)] focus-within:ring-2 focus-within:ring-[var(--color-accent-primary)]"
      style={{ minHeight }}
    >
      {/* One tab stop for the whole field. Arrows move over modules, Enter
          descends into one and then moves over its files, Enter again selects
          it and opens the same inspector a click opens. A tab stop per node
          would be thousands of them, which is not navigation. Pointer events
          stay off so it never intercepts a click meant for the canvas. */}
      <div
        ref={keyboardRef}
        tabIndex={0}
        role="group"
        aria-label={`Code health map, ${overlaySpec.label} lens. Arrow keys move between modules, Enter opens one.`}
        onKeyDown={onCanvasKeyDown}
        className="pointer-events-none absolute inset-0 z-10 outline-none"
      />
      <span aria-live="polite" className="sr-only">
        {cursorLabel}
      </span>

      <svg
        width={dims.width}
        height={dims.height}
        role="img"
        aria-label={`Code universe: modules as galaxies, files radiating from each hub, sized by lines of code and marked by ${overlaySpec.label.toLowerCase()}`}
        className="block"
      >
        <defs>
          <radialGradient id="ch-mist" cx="50%" cy="42%" r="75%">
            <stop offset="0%" stopColor="var(--color-bg-surface)" />
            <stop offset="100%" stopColor="var(--color-bg-root)" />
          </radialGradient>
          {/* Nebula falloff. This was an feGaussianBlur, which is an offscreen
              raster pass per blob, and the blobs live inside the group whose
              transform animates on every galaxy zoom, so all of them
              re-rastered every frame of the transition. A gradient is painted
              directly and costs nothing to animate. `currentColor` resolves per
              blob, so one def serves every galaxy family. */}
          {/* The falloff starts early and runs the whole way out. Holding full
              opacity to seven tenths of the radius and dropping over the last
              three gave every galaxy a visible disc edge, so the field read as
              a page of hard circles with files inside them rather than as
              clouds the files sit in. */}
          <radialGradient id="ch-nebula">
            <stop offset="0%" stopColor="currentColor" stopOpacity="1" />
            <stop offset="40%" stopColor="currentColor" stopOpacity="0.72" />
            <stop offset="72%" stopColor="currentColor" stopOpacity="0.3" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* Misty backdrop + starfield (static; clicking it exits a galaxy). */}
        <rect
          x={0}
          y={0}
          width={dims.width}
          height={dims.height}
          fill="url(#ch-mist)"
          onClick={() => setFocusModule(null)}
        />
        <g className="pointer-events-none">
          {stars.map((s, i) => (
            <circle key={`star-${i}`} cx={s.x} cy={s.y} r={s.r} fill="var(--color-canvas-dot)" />
          ))}
        </g>

        <g
          style={{ transition: "transform 460ms cubic-bezier(0.4,0,0.2,1)" }}
          transform={`translate(${tx},${ty}) scale(${k})`}
        >
          {/* Nebula blobs (back layer) */}
          {galaxies.map((g) => {
            const fam = famFor(g.colorId);
            const faded = focusGalaxy != null && focusGalaxy !== g;
            return (
              <circle
                key={`blob-${g.module}`}
                cx={g.cx + offX}
                cy={g.cy + offY}
                // The gradient stops inside its own radius, so the blob grows
                // to compensate; the softer the falloff, the more reach it
                // needs to end in the page rather than at a rim.
                r={g.R * 1.1}
                style={{ color: fam.hub }}
                fill="url(#ch-nebula)"
                fillOpacity={faded ? 0.04 : 0.13}
                data-galaxy={g.module}
                className="cursor-zoom-in"
                onClick={(e) => {
                  e.stopPropagation();
                  setFocusModule((m) => (m === g.module ? null : g.module));
                }}
              />
            );
          })}

          {/* Spokes: hub to its biggest files */}
          {galaxies.map((g) => {
            const fam = famFor(g.colorId);
            if (focusGalaxy != null && focusGalaxy !== g) return null;
            return (
              <g key={`spokes-${g.module}`} stroke={fam.hub} strokeOpacity={0.28}>
                {g.nodes.slice(0, spokeCap).map((nd) => (
                  <line
                    key={nd.file.file_path}
                    x1={g.cx + offX}
                    y1={g.cy + offY}
                    x2={nd.x + offX}
                    y2={nd.y + offY}
                    strokeWidth={0.6 / k}
                  />
                ))}
              </g>
            );
          })}

          <FileNodes
            galaxies={galaxies}
            focusModuleKey={focusGalaxy?.module ?? null}
            fill={overlaySpec.fill}
            offX={offX}
            offY={offY}
            strokeWidth={0.5 / k}
            interactive={!!onSelectFile}
            onSelect={handleSelect}
            onHoverEnter={handleHoverEnter}
            onHoverLeave={handleHoverLeave}
          />

          <SearchMatches paths={marked} nodeIndex={nodeIndex} offX={offX} offY={offY} />

          <NodeHighlight
            hovered={hovered}
            selectedPath={selectedPath ?? null}
            nodeIndex={nodeIndex}
            offX={offX}
            offY={offY}
            fill={overlaySpec.fill}
          />

          {/* Keyboard cursor, drawn like a focus ring so the one tab stop has
              a visible position on the field. */}
          {kbNode ? (
            <circle
              data-keyboard-cursor={kbNode.file.file_path}
              cx={kbNode.x + offX}
              cy={kbNode.y + offY}
              r={kbNode.r + 3.5}
              fill="none"
              stroke="var(--color-accent-primary)"
              strokeWidth={2 / k}
              strokeDasharray={`${3 / k} ${2 / k}`}
              className="pointer-events-none"
            />
          ) : null}

          {/* Hub markers */}
          {galaxies.map((g) => {
            const fam = famFor(g.colorId);
            const faded = focusGalaxy != null && focusGalaxy !== g;
            return (
              <circle
                key={`hub-${g.module}`}
                cx={g.cx + offX}
                cy={g.cy + offY}
                r={Math.max(2.5, g.R * 0.035)}
                fill={fam.hub}
                fillOpacity={faded ? 0.3 : 0.95}
                stroke="var(--color-bg-root)"
                strokeWidth={1 / k}
                className="pointer-events-none"
              />
            );
          })}
        </g>
      </svg>

      {/* Editorial galaxy labels: a real-px HTML overlay, crisp at any zoom. */}
      <div className="pointer-events-none absolute inset-0">
        {labelled.map((g) => {
          const fam = famFor(g.colorId);
          // toScreen already folds in offX/offY, so pass the raw pack coords.
          const p = toScreen(g.cx, g.cy);
          if (p.x < -40 || p.x > dims.width + 40 || p.y < 0 || p.y > dims.height) return null;
          const big = focusGalaxy === g;
          const cursor = kbLevel === "module" && kbGalaxy === g;
          return (
            <span
              key={`lbl-${g.module}`}
              // Mono, not serif. These are data-viz labels on a canvas overlay,
              // machine-produced names, which the type rules put in mono.
              className={`absolute -translate-x-1/2 -translate-y-1/2 inline-flex items-center gap-1.5 whitespace-nowrap rounded bg-[var(--color-bg-glass)] px-1.5 py-0.5 font-mono uppercase tracking-[0.12em] text-[var(--color-text-primary)] shadow-sm backdrop-blur-sm ${
                cursor
                  ? "ring-2 ring-[var(--color-accent-primary)]"
                  : "ring-1 ring-[var(--color-border-default)]"
              } ${big ? "text-[11px] font-medium" : "text-[10px]"}`}
              style={{ left: p.x, top: p.y }}
            >
              {/* Galaxy color as a dot; the text stays high-contrast so plum
                  module hues remain legible in dark mode. */}
              <span
                className="h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ backgroundColor: fam.hub }}
                aria-hidden
              />
              {g.module}
            </span>
          );
        })}
      </div>

      {/* Lens switcher + key, floated over the canvas. Only for a host with
          nowhere else to put them. A host that lays them out around the map
          passes chrome="none", so the reader is not looking at the field
          through a stack of glass panels. */}
      {chrome === "canvas" ? (
        <>
          {onOverlayChange ? (
            <div className="absolute left-3 top-3 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] p-1 shadow-sm backdrop-blur-sm">
              <MapLensSwitcher
                overlay={overlay}
                onOverlayChange={onOverlayChange}
                lenses={lenses}
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

      {/* Zoom-out breadcrumb, only while a galaxy is focused. */}
      {focusGalaxy ? (
        <div className="absolute right-3 top-3 flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] px-2.5 py-1.5 text-xs shadow-sm backdrop-blur-sm">
          <span className="max-w-[180px] truncate font-medium text-[var(--color-text-primary)]">
            {focusGalaxy.module}
          </span>
          <button
            type="button"
            onClick={() => {
              setFocusModule(null);
              setKbLevel("module");
            }}
            className="ml-1 rounded border border-[var(--color-border-default)] px-1.5 py-0.5 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
          >
            ← Overview
          </button>
        </div>
      ) : null}

      {scope ? <MapScopeNote scope={scope} matches={matches.length} query={q} /> : null}

      {hovered && pointer ? (
        <HoverCard
          file={hovered}
          overlay={overlay}
          x={pointer.x}
          y={pointer.y}
          width={dims.width}
          height={dims.height}
        />
      ) : null}
    </div>
  );
}

/**
 * What this field is, in one sentence, from the response that drew it.
 *
 * A count and the visual it describes have to come from one source, so this
 * reads the server's own selection totals rather than the array length: a
 * caption saying "3,808 files" over a two-thousand node field is a claim the
 * reader has no way to check.
 */
function MapScopeNote({
  scope,
  matches,
  query,
}: {
  scope: MapScope;
  matches: number;
  query: string;
}) {
  const capped = scope.shown < scope.eligible;
  const hiddenCauses = scope.omitted.performanceFiles;
  return (
    <div
      data-testid="map-scope"
      className="pointer-events-none absolute bottom-3 right-3 max-w-[64%] rounded-md sm:max-w-[52%] border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] px-2.5 py-1.5 text-right text-[11px] text-[var(--color-text-tertiary)] shadow-sm backdrop-blur-sm"
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
        </>
      ) : null}
      {query ? (
        <>
          {" · "}
          <span className="tabular-nums">{matches.toLocaleString()}</span>
          {` match${matches === 1 ? "" : "es"} here`}
        </>
      ) : null}
    </div>
  );
}
