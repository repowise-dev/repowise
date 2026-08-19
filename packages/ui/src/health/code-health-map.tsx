"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
// Aliased: the bare name would shadow the DOM `KeyboardEvent` the Esc handler
// below is typed against.
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import * as d3 from "d3-hierarchy";
import { scoreBand, type ScoreBand } from "./tokens";
import { useCommunityFamilies } from "../shared/use-theme-tokens";

export interface CodeHealthMapFile {
  file_path: string;
  score: number;
  nloc: number;
  module: string | null;
  line_coverage_pct: number | null;
  has_test_file: boolean;
  /** Maintainability pillar score (1–10) — drives the maintainability overlay. */
  maintainability_score?: number | null;
  /** Performance-risk pillar score (1–10) — computed but NOT used to color the
   *  performance overlay (it is compressed to [8,10] and reads uniformly green).
   *  The performance lens colors by {@link performance_findings} + coverage. */
  performance_score?: number | null;
  /** Open performance-risk findings on this file — drives the performance heat. */
  performance_findings?: number | null;
  /** Whether a perf detector ran on this file's language (has a dialect). `false`
   *  → grey "not analyzed"; the pillar is high-precision / low-recall, so green
   *  can only mean "a detector ran and surfaced nothing", never "verified fast". */
  performance_analyzed?: boolean | null;
  /** 0–100 churn percentile — drives the churn overlay. */
  churn_percentile?: number | null;
  /** Reclaimable lines on this file — drives the dead-code overlay. */
  dead_code_lines?: number | null;
  /** Open security findings on this file — drives the security overlay. */
  security_findings?: number | null;
}

/**
 * Lens applied to the same field — recolors every file node without re-laying
 * the galaxies. One diagram, switchable overlay (the page spine).
 */
export type CodeHealthOverlay =
  | "health"
  | "maintainability"
  | "performance"
  | "coverage"
  | "churn"
  | "dead-code"
  | "security";

export interface CodeHealthMapProps {
  files: CodeHealthMapFile[];
  /** Highlight ring on this file. */
  selectedPath?: string | null;
  /** Filename substring — non-matching nodes dim out. */
  search?: string;
  /** File click → open the drawer. */
  onSelectFile?: (path: string) => void;
  /** Hover feeds the details rail. */
  onHoverFile?: (file: CodeHealthMapFile | null) => void;
  /** Canvas min height in px. */
  minHeight?: number;
  /** Which lens recolors the field. Defaults to `health`. */
  overlay?: CodeHealthOverlay;
  /** When provided, the lens is switchable. */
  onOverlayChange?: (overlay: CodeHealthOverlay) => void;
  /** Lenses offered in the switcher. Defaults to {@link OVERLAY_ORDER}. */
  lenses?: CodeHealthOverlay[];
  /**
   * The active overlay's per-file signal is still loading. When true the legend
   * shows a "loading…" note instead of its bands, so an all-neutral field reads
   * as "fetching data" rather than "no data".
   */
  overlayLoading?: boolean;
  /**
   * Where the lens switcher and legend live.
   *
   * `"canvas"` (default) floats them over the map. `"none"` renders neither, for
   * hosts that lay them out themselves with {@link MapLensSwitcher} /
   * {@link MapLegend} — the section-style Code Health page does this, because
   * three glass panels stacked on the left of the canvas put chrome on top of
   * the one thing the reader came for.
   *
   * Defaults to `"canvas"` so the VS Code webview, which has no other lens
   * picker, keeps working untouched.
   */
  chrome?: "canvas" | "none";
}

/* Band → SVG fill var(). Same ramp as every score pill on the surface:
 * worst files burn clay/red, healthy ones go green. */
const BAND_FILL: Record<ScoreBand, string> = {
  critical: "var(--color-error)",
  poor: "var(--color-warning)",
  fair: "var(--color-caution)",
  good: "var(--color-success)",
};

const BAND_LABEL: { band: ScoreBand; label: string }[] = [
  { band: "critical", label: "Alert" },
  { band: "poor", label: "Warning" },
  { band: "fair", label: "Fair" },
  { band: "good", label: "Healthy" },
];

/** Neutral fill for nodes a non-health overlay has no signal for. */
const NEUTRAL_FILL = "var(--color-text-tertiary)";

export interface LegendRow {
  fill: string;
  label: string;
}

/** Lens metadata: how to fill a node and what the key reads. */
export interface OverlaySpec {
  label: string;
  /** Caption shown under the legend key. */
  caption: string;
  fill: (f: CodeHealthMapFile) => string;
  legend: LegendRow[];
}

/** Score band: same ramp as the health lens, grey when the pillar is unscored. */
function scoreFill(score: number | null | undefined): string {
  if (score == null) return NEUTRAL_FILL;
  return BAND_FILL[scoreBand(score)];
}

/** Coverage band: green = well covered, red = uncovered, grey = no data. */
function coverageFill(pct: number | null | undefined): string {
  if (pct == null) return NEUTRAL_FILL;
  if (pct >= 80) return "var(--color-success)";
  if (pct >= 50) return "var(--color-caution)";
  if (pct >= 20) return "var(--color-warning)";
  return "var(--color-error)";
}

/**
 * Performance lens fill — by findings + coverage state, NOT by the performance
 * score. The score is capped to [9,10] so a score ramp paints the whole map
 * green and hides where the risk actually is. Instead:
 *   heat (yellow→red) = open perf findings, so 40 N+1s ≠ 1;
 *   green            = a detector ran and surfaced nothing (NOT "verified fast");
 *   grey             = no detector for this language ever ran ("not analyzed").
 * This keeps the pillar's high-precision / low-recall honesty on the map.
 */
function perfFill(f: CodeHealthMapFile): string {
  const n = f.performance_findings ?? 0;
  if (n > 0) {
    if (n >= 5) return "var(--color-error)";
    if (n >= 2) return "var(--color-warning)";
    return "var(--color-caution)";
  }
  // No findings: green only when a detector actually ran on this language;
  // otherwise grey so an un-analyzed file never reads as "clean".
  return f.performance_analyzed ? "var(--color-success)" : NEUTRAL_FILL;
}

/** Churn band: red = top-decile churn, green = quiet. */
function churnFill(pctile: number | null | undefined): string {
  if (pctile == null) return NEUTRAL_FILL;
  if (pctile >= 90) return "var(--color-error)";
  if (pctile >= 70) return "var(--color-warning)";
  if (pctile >= 40) return "var(--color-caution)";
  return "var(--color-success)";
}

export const OVERLAY_SPECS: Record<CodeHealthOverlay, OverlaySpec> = {
  health: {
    label: "Health",
    caption: "galaxy = module · size = lines of code",
    fill: (f) => BAND_FILL[scoreBand(f.score)],
    legend: BAND_LABEL.map((b) => ({ fill: BAND_FILL[b.band], label: b.label })),
  },
  maintainability: {
    label: "Maintainability",
    caption: "color = maintainability score · grey = not measured",
    fill: (f) => scoreFill(f.maintainability_score),
    legend: [
      ...BAND_LABEL.map((b) => ({ fill: BAND_FILL[b.band], label: b.label })),
      { fill: NEUTRAL_FILL, label: "not measured" },
    ],
  },
  performance: {
    label: "Performance",
    caption: "color = findings, not a score · grey = language not analyzed",
    fill: perfFill,
    legend: [
      { fill: "var(--color-error)", label: "5+ findings" },
      { fill: "var(--color-warning)", label: "2–4 findings" },
      { fill: "var(--color-caution)", label: "1 finding" },
      { fill: "var(--color-success)", label: "Analyzed, none found" },
      { fill: NEUTRAL_FILL, label: "Not analyzed" },
    ],
  },
  coverage: {
    label: "Coverage",
    caption: "color = line coverage · grey = no coverage data",
    fill: (f) => coverageFill(f.line_coverage_pct),
    legend: [
      { fill: "var(--color-success)", label: "≥80%" },
      { fill: "var(--color-caution)", label: "50–80%" },
      { fill: "var(--color-warning)", label: "20–50%" },
      { fill: "var(--color-error)", label: "<20%" },
      { fill: NEUTRAL_FILL, label: "no data" },
    ],
  },
  churn: {
    label: "Churn",
    caption: "color = 90-day churn percentile",
    fill: (f) => churnFill(f.churn_percentile),
    legend: [
      { fill: "var(--color-error)", label: "Top 10%" },
      { fill: "var(--color-warning)", label: "Top 30%" },
      { fill: "var(--color-caution)", label: "Top 60%" },
      { fill: "var(--color-success)", label: "Quiet" },
      { fill: NEUTRAL_FILL, label: "no data" },
    ],
  },
  "dead-code": {
    label: "Dead code",
    caption: "red = reclaimable lines · grey = clean",
    fill: (f) => ((f.dead_code_lines ?? 0) > 0 ? "var(--color-error)" : NEUTRAL_FILL),
    legend: [
      { fill: "var(--color-error)", label: "Has dead code" },
      { fill: NEUTRAL_FILL, label: "Clean" },
    ],
  },
  security: {
    label: "Security",
    caption: "red = open findings · grey = clean",
    fill: (f) => ((f.security_findings ?? 0) > 0 ? "var(--color-error)" : NEUTRAL_FILL),
    legend: [
      { fill: "var(--color-error)", label: "Has findings" },
      { fill: NEUTRAL_FILL, label: "Clean" },
    ],
  },
};

/**
 * Default lenses offered in the switcher: the three co-equal health signals,
 * all backed by a per-file score that rides on the map payload itself.
 *
 * Churn is deliberately not in the default. It colors by `churn_percentile`,
 * which arrives on a separate request the host has to join in, so offering it
 * where nobody joined it paints an all-neutral field that reads as "no churn"
 * rather than "no data". Hosts that do the join pass their own `lenses`.
 */
export const OVERLAY_ORDER: CodeHealthOverlay[] = [
  "health",
  "maintainability",
  "performance",
];

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5)); // ~2.39996 rad

/** One module = one galaxy: its files plus size aggregates. */
export interface GalaxyAgg {
  module: string;
  files: CodeHealthMapFile[]; // NLOC-desc
  totalNloc: number;
  maxNloc: number;
}

/**
 * Group flat file rows into per-module galaxies, dropping zero-NLOC files
 * (they can't be sized). Files come back sorted biggest-first. Pure + exported
 * for unit testing.
 */
export function groupByModule(files: CodeHealthMapFile[]): GalaxyAgg[] {
  const byModule = new Map<string, CodeHealthMapFile[]>();
  for (const f of files) {
    if (f.nloc <= 0) continue;
    const key = f.module ?? "(ungrouped)";
    const arr = byModule.get(key);
    if (arr) arr.push(f);
    else byModule.set(key, [f]);
  }
  const out: GalaxyAgg[] = [];
  for (const [module, group] of byModule) {
    group.sort((a, b) => b.nloc - a.nloc);
    out.push({
      module,
      files: group,
      totalNloc: group.reduce((s, f) => s + f.nloc, 0),
      maxNloc: group[0]?.nloc ?? 1,
    });
  }
  // Biggest galaxies first → stable z-order (small ones paint on top).
  out.sort((a, b) => b.totalNloc - a.totalNloc);
  return out;
}

interface FileNode {
  file: CodeHealthMapFile;
  x: number;
  y: number;
  r: number;
}

interface Galaxy {
  module: string;
  cx: number;
  cy: number;
  R: number;
  fileCount: number;
  colorId: number;
  nodes: FileNode[];
}

/** Stable, well-spread hash so a module maps to a consistent palette family. */
function moduleColorId(module: string): number {
  let h = 0;
  for (let i = 0; i < module.length; i++) h = (h * 31 + module.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** Deterministic pseudo-random in [0,1) — for the static starfield. */
function rand(i: number): number {
  const x = Math.sin(i * 127.1) * 43758.5453;
  return x - Math.floor(x);
}

/** Label a galaxy once its on-screen footprint clears this radius (px). */
const LABEL_MIN_SCREEN_R = 40;
/** Spokes drawn from each hub to its N biggest files (overview / focused). */
const SPOKES_OVERVIEW = 8;
const SPOKES_FOCUSED = 36;

export function CodeHealthMap({
  files,
  selectedPath,
  search,
  onSelectFile,
  onHoverFile,
  minHeight = 640,
  overlay = "health",
  onOverlayChange,
  lenses = OVERLAY_ORDER,
  overlayLoading = false,
  chrome = "canvas",
}: CodeHealthMapProps) {
  const overlaySpec = OVERLAY_SPECS[overlay] ?? OVERLAY_SPECS.health;
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ width: 0, height: 0 });
  const [focusModule, setFocusModule] = useState<string | null>(null);
  const [hovered, setHovered] = useState<CodeHealthMapFile | null>(null);
  const famFor = useCommunityFamilies();

  // Latest callbacks held in refs so the node-layer handlers stay referentially
  // stable across renders (the parent re-renders on every hover to drive the
  // rail). Stable handlers let the memoized <FileNodes> skip re-rendering its
  // ~2,000 circles when only the hover/selection highlight changes.
  const onSelectRef = useRef(onSelectFile);
  onSelectRef.current = onSelectFile;
  const onHoverRef = useRef(onHoverFile);
  onHoverRef.current = onHoverFile;
  const handleSelect = useCallback((path: string) => onSelectRef.current?.(path), []);
  const handleHoverEnter = useCallback((f: CodeHealthMapFile) => {
    setHovered(f);
    onHoverRef.current?.(f);
  }, []);
  const handleHoverLeave = useCallback((f: CodeHealthMapFile) => {
    setHovered((h) => (h === f ? null : h));
  }, []);

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

  // Esc always exits a focused galaxy — a guaranteed zoom-out.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFocusModule(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const S = Math.max(0, Math.min(dims.width, dims.height));

  // Galaxy placement (d3-pack) + hub-and-spoke file layout (phyllotaxis).
  const { galaxies, byModule } = useMemo(() => {
    if (S === 0 || files.length === 0) {
      return { galaxies: [] as Galaxy[], byModule: new Map<string, Galaxy>() };
    }
    const aggs = groupByModule(files);
    const root = d3
      .hierarchy<{ value?: number; agg?: GalaxyAgg; children?: unknown[] }>(
        { children: aggs.map((a) => ({ value: a.totalNloc, agg: a })) },
        (d) => d.children as { value?: number; agg?: GalaxyAgg }[] | undefined,
      )
      .sum((d) => d.value ?? 0);
    d3.pack<{ value?: number; agg?: GalaxyAgg }>().size([S, S]).padding(S * 0.012)(
      root as d3.HierarchyNode<{ value?: number; agg?: GalaxyAgg }>,
    );
    const galaxies: Galaxy[] = [];
    for (const leaf of root.leaves() as d3.HierarchyCircularNode<{ agg?: GalaxyAgg }>[]) {
      const agg = leaf.data.agg;
      if (!agg) continue;
      const R = leaf.r;
      const cx = leaf.x;
      const cy = leaf.y;
      const n = agg.files.length;
      const spread = R * 0.84;
      const maxNodeR = Math.max(2, R * 0.16);
      const nodes: FileNode[] = agg.files.map((file, i) => {
        const rr = spread * Math.sqrt((i + 0.55) / n);
        const ang = i * GOLDEN_ANGLE;
        const nodeR = Math.max(
          1.2,
          Math.min(maxNodeR, maxNodeR * Math.sqrt(file.nloc / agg.maxNloc)),
        );
        return { file, x: cx + rr * Math.cos(ang), y: cy + rr * Math.sin(ang), r: nodeR };
      });
      galaxies.push({ module: agg.module, cx, cy, R, fileCount: n, colorId: moduleColorId(agg.module), nodes });
    }
    const byModule = new Map(galaxies.map((g) => [g.module, g]));
    return { galaxies, byModule };
  }, [files, S]);

  // path → laid-out node, for the highlight overlay to position itself without
  // re-rendering the whole node layer on hover/selection.
  const nodeIndex = useMemo(() => {
    const m = new Map<string, FileNode>();
    for (const g of galaxies) for (const nd of g.nodes) m.set(nd.file.file_path, nd);
    return m;
  }, [galaxies]);

  // Starfield — static layer, regenerated only on resize.
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

  const focusGalaxy = focusModule ? byModule.get(focusModule) ?? null : null;

  const offX = (dims.width - S) / 2;
  const offY = (dims.height - S) / 2;
  const k = focusGalaxy ? (S * 0.9) / (2 * focusGalaxy.R) : 1;
  const camX = focusGalaxy ? focusGalaxy.cx : S / 2;
  const camY = focusGalaxy ? focusGalaxy.cy : S / 2;
  const tx = dims.width / 2 - k * (camX + offX);
  const ty = dims.height / 2 - k * (camY + offY);
  const toScreen = (x: number, y: number) => ({ x: k * (x + offX) + tx, y: k * (y + offY) + ty });

  const q = (search ?? "").trim().toLowerCase();

  if (S === 0) {
    return <div ref={containerRef} className="w-full rounded-xl bg-[var(--color-bg-root)]" style={{ minHeight }} />;
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

  return (
    <div
      ref={containerRef}
      className="relative w-full overflow-hidden rounded-xl border border-[var(--color-border-default)]"
      style={{ minHeight }}
    >
      <svg
        width={dims.width}
        height={dims.height}
        role="img"
        aria-label={`Code universe: modules as galaxies, files radiating from each hub, sized by lines of code and colored by ${overlaySpec.label.toLowerCase()}`}
        className="block"
      >
        <defs>
          <radialGradient id="ch-mist" cx="50%" cy="42%" r="75%">
            <stop offset="0%" stopColor="var(--color-bg-surface)" />
            <stop offset="100%" stopColor="var(--color-bg-root)" />
          </radialGradient>
          {/* Nebula falloff. This was an feGaussianBlur, which is an offscreen
              raster pass per blob — and the blobs live inside the group whose
              transform animates on every galaxy zoom, so all of them re-rastered
              every frame of a 460ms transition. A gradient is painted directly,
              costs nothing to animate, and the stops below are tuned to match
              the old stdDeviation=7 edge: flat through the middle, soft over the
              last ~15%. `currentColor` resolves per blob, so one def serves
              every galaxy family instead of one gradient each. */}
          <radialGradient id="ch-nebula">
            <stop offset="0%" stopColor="currentColor" stopOpacity="1" />
            <stop offset="70%" stopColor="currentColor" stopOpacity="1" />
            <stop offset="88%" stopColor="currentColor" stopOpacity="0.55" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* Misty backdrop + starfield (static, clicking it exits a galaxy). */}
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

        <g style={{ transition: "transform 460ms cubic-bezier(0.4,0,0.2,1)" }} transform={`translate(${tx},${ty}) scale(${k})`}>
          {/* Nebula blobs (back layer) */}
          {galaxies.map((g) => {
            const fam = famFor(g.colorId);
            const faded = focusGalaxy != null && focusGalaxy !== g;
            return (
              <circle
                key={`blob-${g.module}`}
                cx={g.cx + offX}
                cy={g.cy + offY}
                // Was 0.96 with a blur that spread past the edge; the gradient
                // stops inside its own radius, so the blob grows to compensate.
                r={g.R * 1.02}
                // `color` feeds the gradient's currentColor stops.
                style={{ color: fam.hub }}
                fill="url(#ch-nebula)"
                fillOpacity={faded ? 0.05 : 0.16}
                data-galaxy={g.module}
                className="cursor-zoom-in"
                onClick={(e) => {
                  e.stopPropagation();
                  setFocusModule((m) => (m === g.module ? null : g.module));
                }}
              />
            );
          })}

          {/* Spokes — hub to its biggest files */}
          {galaxies.map((g) => {
            const fam = famFor(g.colorId);
            const faded = focusGalaxy != null && focusGalaxy !== g;
            if (faded) return null;
            return (
              <g key={`spokes-${g.module}`} stroke={fam.hub} strokeOpacity={0.28}>
                {g.nodes.slice(0, spokeCap).map((nd) => (
                  <line
                    key={nd.file.file_path}
                    x1={g.cx + offX}
                    y1={g.cy + offY}
                    x2={nd.x + offX}
                    y2={nd.y + offY}
                    strokeWidth={0.6}
                    vectorEffect="non-scaling-stroke"
                  />
                ))}
              </g>
            );
          })}

          {/* File nodes — memoized base layer (~2,000 circles). Hover and
              selection are drawn by the cheap highlight layer below, so moving
              the mouse never re-renders this layer. */}
          <FileNodes
            galaxies={galaxies}
            focusModuleKey={focusGalaxy?.module ?? null}
            fill={overlaySpec.fill}
            q={q}
            offX={offX}
            offY={offY}
            strokeWidth={0.5 / k}
            interactive={!!onSelectFile}
            onSelect={handleSelect}
            onHoverEnter={handleHoverEnter}
            onHoverLeave={handleHoverLeave}
          />

          {/* Highlight overlay — the hovered (enlarged, opaque) and selected
              (ring) nodes only, drawn on top. Re-renders on hover/select; the
              base node layer above stays untouched. */}
          <NodeHighlight
            hovered={hovered}
            selectedPath={selectedPath ?? null}
            nodeIndex={nodeIndex}
            offX={offX}
            offY={offY}
            fill={overlaySpec.fill}
          />

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
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
                className="pointer-events-none"
              />
            );
          })}
        </g>
      </svg>

      {/* Editorial galaxy labels — real-px HTML overlay, crisp at any zoom */}
      <div className="pointer-events-none absolute inset-0">
        {labelled.map((g) => {
          const fam = famFor(g.colorId);
          // toScreen already folds in offX/offY — pass the raw pack coords so
          // the label lands on the hub (don't double-add the offset).
          const p = toScreen(g.cx, g.cy);
          if (p.x < -40 || p.x > dims.width + 40 || p.y < 0 || p.y > dims.height) return null;
          const big = focusGalaxy === g;
          return (
            <span
              key={`lbl-${g.module}`}
              // Mono, not serif. These are data-viz labels on a canvas overlay
              // — machine-produced names, which the type rules put in mono;
              // serif is reserved for long-form reading surfaces.
              className={`absolute -translate-x-1/2 -translate-y-1/2 inline-flex items-center gap-1.5 whitespace-nowrap rounded bg-[var(--color-bg-glass)] px-1.5 py-0.5 font-mono uppercase tracking-[0.12em] text-[var(--color-text-primary)] shadow-sm ring-1 ring-[var(--color-border-default)] backdrop-blur-sm ${
                big ? "text-[11px] font-medium" : "text-[10px]"
              }`}
              style={{ left: p.x, top: p.y }}
            >
              {/* Galaxy color as a dot — the text itself stays high-contrast so
                  purple/plum module hues remain legible in dark mode. */}
              <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: fam.hub }} aria-hidden />
              {g.module}
            </span>
          );
        })}
      </div>

      {/* Lens switcher + legend, floated over the canvas. Only for hosts that
          have nowhere else to put them (the VS Code webview). The web page
          passes chrome="none" and lays both out around the map instead, so the
          reader is not looking at the field through three glass panels. */}
      {chrome === "canvas" ? (
        <>
          {onOverlayChange ? (
            <div className="absolute left-3 top-3 flex flex-wrap gap-1 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] p-1 text-xs shadow-sm backdrop-blur-sm">
              {lenses.map((mode) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => onOverlayChange(mode)}
                  aria-pressed={overlay === mode}
                  className={
                    overlay === mode
                      ? "rounded px-2 py-1 font-medium bg-[var(--color-accent-primary)] text-[var(--color-text-inverse)]"
                      : "rounded px-2 py-1 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
                  }
                >
                  {OVERLAY_SPECS[mode].label}
                </button>
              ))}
            </div>
          ) : null}

          <div
            className={`absolute left-3 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] px-3 py-2 text-xs shadow-sm backdrop-blur-sm ${
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

      {/* Zoom-out breadcrumb — only shown while a galaxy is focused */}
      {focusGalaxy ? (
        <div className="absolute right-3 top-3 flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-glass)] px-2.5 py-1.5 text-xs shadow-sm backdrop-blur-sm">
          <span className="max-w-[180px] truncate font-medium text-[var(--color-text-primary)]">{focusGalaxy.module}</span>
          <button
            type="button"
            onClick={() => setFocusModule(null)}
            className="ml-1 rounded border border-[var(--color-border-default)] px-1.5 py-0.5 text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
          >
            ← Overview
          </button>
        </div>
      ) : null}

      {/* Hover tooltip */}
      {hovered ? <HoverCard file={hovered} /> : null}
    </div>
  );
}

/**
 * The base file-node layer: every file as a circle, sized + colored by the
 * active lens. Memoized and fed only stable props (laid-out galaxies, a stable
 * fill fn, stable handlers), so a hover — which re-renders the parent — never
 * reconciles these ~2,000 elements. Hover/selection live in {@link NodeHighlight}.
 */
const FileNodes = memo(function FileNodes({
  galaxies,
  focusModuleKey,
  fill,
  q,
  offX,
  offY,
  strokeWidth,
  interactive,
  onSelect,
  onHoverEnter,
  onHoverLeave,
}: {
  galaxies: Galaxy[];
  focusModuleKey: string | null;
  fill: (f: CodeHealthMapFile) => string;
  q: string;
  offX: number;
  offY: number;
  /**
   * Stroke in user units, pre-divided by the zoom scale so it lands on 0.5
   * device px at rest.
   *
   * The obvious spelling is `vectorEffect="non-scaling-stroke"`, which is what
   * this used. But Chrome recomputes stroke geometry per element per frame for
   * that attribute, and these are ~2,000 elements inside the group whose
   * transform animates on every galaxy zoom. Dividing by `k` instead is free.
   * The trade is that during the 460ms transition the browser interpolates the
   * transform while this value is already at its destination, so strokes are
   * fractionally off mid-flight — invisible on a 0.5px line under a moving
   * field, and the field is correct the instant it settles.
   */
  strokeWidth: number;
  interactive: boolean;
  onSelect: (path: string) => void;
  onHoverEnter: (f: CodeHealthMapFile) => void;
  onHoverLeave: (f: CodeHealthMapFile) => void;
}) {
  return (
    <>
      {galaxies.map((g) => {
        const faded = focusModuleKey != null && g.module !== focusModuleKey;
        return (
          <g key={`nodes-${g.module}`}>
            {g.nodes.map((nd) => {
              const f = nd.file;
              const dim = q.length > 0 && !f.file_path.toLowerCase().includes(q);
              return (
                /* No <title> child. It was ~2,000 extra DOM nodes driving a
                   native tooltip that appears on its own delay, in the corner
                   the pointer is in, restating what HoverCard already shows
                   the moment you touch a node — two tooltips racing. `data-path`
                   costs no node and gives tests a stable handle. */
                <circle
                  key={f.file_path}
                  data-path={f.file_path}
                  cx={nd.x + offX}
                  cy={nd.y + offY}
                  r={nd.r}
                  fill={fill(f)}
                  fillOpacity={faded ? 0.18 : dim ? 0.12 : 0.9}
                  stroke="var(--color-bg-root)"
                  strokeWidth={strokeWidth}
                  className={interactive ? "cursor-pointer" : undefined}
                  onMouseEnter={() => onHoverEnter(f)}
                  onMouseLeave={() => onHoverLeave(f)}
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelect(f.file_path);
                  }}
                />
              );
            })}
          </g>
        );
      })}
    </>
  );
});

/** The hovered + selected nodes only, drawn on top of the static node layer so
 *  pointer movement repaints two circles instead of the whole field. */
function NodeHighlight({
  hovered,
  selectedPath,
  nodeIndex,
  offX,
  offY,
  fill,
}: {
  hovered: CodeHealthMapFile | null;
  selectedPath: string | null;
  nodeIndex: Map<string, FileNode>;
  offX: number;
  offY: number;
  fill: (f: CodeHealthMapFile) => string;
}) {
  const sel = selectedPath ? nodeIndex.get(selectedPath) : null;
  const hov = hovered ? nodeIndex.get(hovered.file_path) : null;
  return (
    <g className="pointer-events-none">
      {sel ? (
        <circle
          cx={sel.x + offX}
          cy={sel.y + offY}
          r={sel.r}
          fill={fill(sel.file)}
          fillOpacity={1}
          stroke="var(--color-text-primary)"
          strokeWidth={2}
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
      {hov && hov !== sel ? (
        <circle
          cx={hov.x + offX}
          cy={hov.y + offY}
          r={hov.r + 1.5}
          fill={fill(hov.file)}
          fillOpacity={1}
          stroke="var(--color-bg-root)"
          strokeWidth={0.5}
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
    </g>
  );
}

function HoverCard({ file }: { file: CodeHealthMapFile }) {
  const cov = file.line_coverage_pct;
  return (
    <div className="pointer-events-none absolute bottom-3 left-3 max-w-[75%] rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2.5 py-1.5 text-xs shadow-md">
      <div className="truncate font-mono text-[var(--color-text-primary)]">{file.file_path}</div>
      <div className="text-[var(--color-text-tertiary)]">
        score {file.score.toFixed(1)} · {file.nloc.toLocaleString()} NLOC
        {cov != null ? ` · ${Math.round(cov)}% cov` : ""}
        {file.has_test_file ? "" : " · untested"}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Map chrome
 *
 * The switcher and the key, as components a host can place around the map
 * rather than on top of it. Both read the same {@link OVERLAY_SPECS} the
 * canvas does, so an off-canvas legend can never drift from the fills it is
 * describing.
 * ------------------------------------------------------------------ */

/** The legend's colour rows, shared by both placements. */
function MapLegendRows({ spec, loading }: { spec: OverlaySpec; loading: boolean }) {
  if (loading) {
    return (
      <div className="flex items-center gap-1.5 text-[var(--color-text-tertiary)]">
        <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-[var(--color-text-tertiary)]" />
        loading {spec.label.toLowerCase()}…
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-1">
      {spec.legend.map((row) => (
        <span key={row.label} className="flex items-center gap-1.5 text-[var(--color-text-tertiary)]">
          <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: row.fill }} />
          {row.label}
        </span>
      ))}
    </div>
  );
}

/**
 * The lens switcher as a real segmented control, for `chrome="none"` hosts.
 *
 * A radiogroup rather than a row of `aria-pressed` buttons: picking a lens is
 * one choice out of a set, and the roving-tabindex arrow-key behaviour comes
 * free with the role.
 */
export function MapLensSwitcher({
  overlay,
  onOverlayChange,
  lenses = OVERLAY_ORDER,
  className,
}: {
  overlay: CodeHealthOverlay;
  onOverlayChange: (overlay: CodeHealthOverlay) => void;
  lenses?: CodeHealthOverlay[];
  className?: string;
}) {
  const onKeyDown = (e: ReactKeyboardEvent) => {
    const i = lenses.indexOf(overlay);
    if (i < 0) return;
    let next = i;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % lenses.length;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp")
      next = (i - 1 + lenses.length) % lenses.length;
    else return;
    e.preventDefault();
    const target = lenses[next];
    if (target) onOverlayChange(target);
  };

  return (
    <div
      role="radiogroup"
      aria-label="Map lens"
      onKeyDown={onKeyDown}
      className={`inline-flex rounded-md border border-[var(--color-border-default)] p-0.5 ${className ?? ""}`}
    >
      {lenses.map((mode) => {
        const active = overlay === mode;
        return (
          <button
            key={mode}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onOverlayChange(mode)}
            className={`rounded px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)] ${
              active
                ? "bg-[var(--color-bg-elevated)] font-semibold text-[var(--color-text-primary)]"
                : "font-medium text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
            }`}
          >
            {OVERLAY_SPECS[mode].label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * The active lens's key, laid out as a horizontal strip to sit under the map.
 *
 * Same content as the on-canvas panel, arranged to read as a caption rather
 * than as a floating card: the swatches run inline and the lens caption closes
 * the line.
 */
export function MapLegend({
  overlay,
  loading = false,
  className,
}: {
  overlay: CodeHealthOverlay;
  loading?: boolean;
  className?: string;
}) {
  const spec = OVERLAY_SPECS[overlay] ?? OVERLAY_SPECS.health;
  return (
    <div
      className={`flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-[var(--color-text-tertiary)] ${className ?? ""}`}
    >
      {loading ? (
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--color-text-tertiary)]" />
          loading {spec.label.toLowerCase()}…
        </span>
      ) : (
        spec.legend.map((row) => (
          <span key={row.label} className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: row.fill }} />
            {row.label}
          </span>
        ))
      )}
      <span className="font-mono text-[10px] uppercase tracking-[0.12em]">{spec.caption}</span>
    </div>
  );
}
