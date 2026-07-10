import { useRef, useEffect, useCallback, useState } from "react";
import type Sigma from "sigma";
import type Graph from "graphology";
import type { NodeLabelDrawingFunction, drawDiscNodeLabel } from "sigma/rendering";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./types";
import type { ColorMode } from "../graph-toolbar";
import type { Signal } from "../context";
import {
  LABEL_FONT,
  LABEL_SIZE,
  LABEL_GRID_CELL_SIZE,
  getLabelDensity,
  getLabelRenderedSizeThreshold,
  edgeColorsForTheme,
  type EdgeKind,
  languageColor,
} from "./constants";
import { getCommunityFamily, resolveToken, useThemeVersion } from "../../shared/use-theme-tokens";

// ---- Color helpers (kept minimal — avoid regex in hot paths) ----

function hexToRgb(hex: string): [number, number, number] {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function rgbToHex(r: number, g: number, b: number): string {
  return "#" + ((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1);
}

const THEME_COLORS = {
  dark:  { bg: [18, 18, 28] as const, text: "#f5f5f7", subtitle: "#888888", tooltip: "#12121c" },
  light: { bg: [250, 250, 252] as const, text: "#1a1a2e", subtitle: "#666666", tooltip: "#ffffff" },
};

/**
 * Hub/core disc-label drawer, built per theme. Hub/core labels render centered
 * *inside* the disc (ROBOTICS-style) with a soft halo ring in the family hue;
 * everything else falls through to Sigma's stock side-label drawer. Factored out
 * of the init effect so the theme effect can re-set it on light/dark toggle
 * (the closure must NOT capture stale theme colors). Closes over only its args +
 * the stable THEME_COLORS import.
 */
function makeDrawNodeLabel(
  graphTheme: "light" | "dark",
  drawDisc: typeof drawDiscNodeLabel,
): NodeLabelDrawingFunction {
  return (context, data, settings) => {
    const extra = data as unknown as Record<string, unknown>;
    const kind = extra.nodeType as string | undefined;
    if (kind !== "hub" && kind !== "core") {
      drawDisc(context, data, settings);
      return;
    }

    const theme = THEME_COLORS[graphTheme] ?? THEME_COLORS.dark;
    const size = data.size || 20;

    // Soft 2px halo ring in the family hue (emulated — NodeCircleProgram
    // has no border and @sigma/node-border isn't a dependency).
    const halo = (extra.haloColor as string) || data.color;
    context.beginPath();
    context.arc(data.x, data.y, size + 2.5, 0, Math.PI * 2);
    context.lineWidth = 2;
    context.strokeStyle = halo;
    context.globalAlpha = 0.55;
    context.stroke();
    context.globalAlpha = 1;

    const label = data.label;
    if (!label) return;

    // Fit the uppercase label inside the disc; shrink for long names.
    const font = settings.labelFont || "JetBrains Mono, monospace";
    let fontSize = Math.max(9, Math.min(13, size * 0.55));
    context.font = `600 ${fontSize}px ${font}`;
    const maxWidth = size * 1.9;
    while (context.measureText(label).width > maxWidth && fontSize > 7) {
      fontSize -= 1;
      context.font = `600 ${fontSize}px ${font}`;
    }
    context.textAlign = "center";
    context.textBaseline = "middle";
    // Core is dark → light text; hubs are warm → dark text for contrast.
    context.fillStyle = kind === "core" ? theme.text : "#1a1320";
    context.fillText(label, data.x, data.y);
  };
}

/**
 * Hover tooltip drawer, built per theme. Hubs get a small surface card (member
 * count, doc %, langs); other nodes get a label/path pill. Factored out of the
 * init effect alongside makeDrawNodeLabel so the theme effect can re-set it on
 * light/dark toggle. Closes over only its arg + the stable THEME_COLORS import.
 */
function makeDrawNodeHover(graphTheme: "light" | "dark"): NodeLabelDrawingFunction {
  return (context, data, settings) => {
    const label = data.label;
    if (!label) return;

    const theme = THEME_COLORS[graphTheme] ?? THEME_COLORS.dark;
    const extra = data as Record<string, unknown>;
    const fullPath = (extra.fullPath as string) ?? undefined;

    // Hub/module tooltip: a small surface card. First disclosure layer —
    // headline stats only; the full detail lives in the inspection panel.
    if (extra.nodeType === "hub" || extra.nodeType === "module") {
      const font = settings.labelFont || "JetBrains Mono, monospace";
      const docPct = Math.round(((extra.docCoveragePct as number) ?? 0) * 100);
      const lines: string[] = [];
      if (extra.nodeType === "hub") {
        const members = (extra.memberCount as number) ?? 0;
        lines.push(`${members} file${members === 1 ? "" : "s"} · ${docPct}% documented`);
        const langs = ((extra.languages as string[]) ?? []).slice(0, 3).join(", ");
        if (langs) lines.push(langs);
      } else {
        const files = (extra.fileCount as number) ?? 0;
        lines.push(`${files} file${files === 1 ? "" : "s"} · ${docPct}% documented`);
        const hot = (extra.hotspotCount as number) ?? 0;
        const dead = (extra.deadCount as number) ?? 0;
        const issues: string[] = [];
        if (hot > 0) issues.push(`${hot} hotspot${hot === 1 ? "" : "s"}`);
        if (dead > 0) issues.push(`${dead} dead file${dead === 1 ? "" : "s"}`);
        if (issues.length > 0) lines.push(issues.join(" · "));
      }

      const titleSize = (settings.labelSize || 11) + 1;
      const lineSize = 9;
      context.font = `600 ${titleSize}px ${font}`;
      let maxW = context.measureText(label).width;
      context.font = `400 ${lineSize}px ${font}`;
      for (const l of lines) maxW = Math.max(maxW, context.measureText(l).width);

      const padX = 12;
      const padY = 8;
      const gap = 4;
      const w = maxW + padX * 2;
      const h = titleSize + lines.length * (lineSize + gap) + padY * 2;
      const nodeSize = data.size || 20;
      const cx = data.x;
      const cy = data.y - nodeSize - 14 - h / 2;

      context.fillStyle = theme.tooltip;
      context.beginPath();
      context.roundRect(cx - w / 2, cy - h / 2, w, h, 6);
      context.fill();
      context.lineWidth = 1.5;
      context.strokeStyle = data.color || "#6366f1";
      context.stroke();

      context.textAlign = "center";
      context.textBaseline = "top";
      let ty = cy - h / 2 + padY;
      context.fillStyle = theme.text;
      context.font = `600 ${titleSize}px ${font}`;
      context.fillText(label, cx, ty);
      ty += titleSize + gap;
      context.fillStyle = theme.subtitle;
      context.font = `400 ${lineSize}px ${font}`;
      for (const l of lines) {
        context.fillText(l, cx, ty);
        ty += lineSize + gap;
      }

      // Halo emphasis on hover.
      context.beginPath();
      context.arc(data.x, data.y, nodeSize + 4, 0, Math.PI * 2);
      context.strokeStyle = (extra.haloColor as string) || data.color || "#6366f1";
      context.lineWidth = 2.5;
      context.globalAlpha = 0.6;
      context.stroke();
      context.globalAlpha = 1;
      return;
    }

    const primarySize = settings.labelSize || 11;
    const secondarySize = 9;
    const font = settings.labelFont || "JetBrains Mono, monospace";
    context.font = `500 ${primarySize}px ${font}`;
    const labelWidth = context.measureText(label).width;

    let showPath = false;
    let pathWidth = 0;
    if (fullPath && fullPath !== label) {
      context.font = `400 ${secondarySize}px ${font}`;
      pathWidth = context.measureText(fullPath).width;
      showPath = true;
    }

    const nodeSize = data.size || 8;
    const paddingX = 10;
    const paddingY = 5;
    const lineGap = showPath ? 3 : 0;
    const width = Math.max(labelWidth, pathWidth) + paddingX * 2;
    const height =
      primarySize + (showPath ? lineGap + secondarySize : 0) + paddingY * 2;
    const radius = 5;
    const x = data.x;
    const y = data.y - nodeSize - 12 - height / 2;

    context.fillStyle = theme.tooltip;
    context.beginPath();
    context.roundRect(x - width / 2, y - height / 2, width, height, radius);
    context.fill();
    context.lineWidth = 2;
    context.strokeStyle = data.color || "#6366f1";
    context.stroke();

    context.textAlign = "center";
    context.textBaseline = "middle";

    const labelY = showPath ? y - (lineGap + secondarySize) / 2 : y;
    context.fillStyle = theme.text;
    context.font = `500 ${primarySize}px ${font}`;
    context.fillText(label, x, labelY);

    if (showPath) {
      context.fillStyle = theme.subtitle;
      context.font = `400 ${secondarySize}px ${font}`;
      context.fillText(
        fullPath!,
        x,
        labelY + primarySize / 2 + lineGap + secondarySize / 2,
      );
    }

    context.beginPath();
    context.arc(data.x, data.y, nodeSize + 4, 0, Math.PI * 2);
    context.strokeStyle = data.color || "#6366f1";
    context.lineWidth = 2;
    context.globalAlpha = 0.5;
    context.stroke();
    context.globalAlpha = 1;
  };
}

/**
 * Theme-aware viz colors resolved from the live design tokens. Read on the
 * React side (where getComputedStyle works) and keyed to the theme version, so
 * canvas painting tracks light/dark. Mirrors the per-theme THEME_COLORS shape.
 */
interface VizPalette {
  risk: { high: string; medium: string; low: string };
  hotspot: string;
  decision: string;
  label: string;
  pathHighlight: string;
  edge: Record<EdgeKind, string>;
}

function resolveVizPalette(theme: "light" | "dark"): VizPalette {
  return {
    risk: {
      high: resolveToken("--color-risk-high", "#b23a2e"),
      medium: resolveToken("--color-risk-medium", "#9a6614"),
      low: resolveToken("--color-risk-low", "#1d8155"),
    },
    hotspot: resolveToken("--color-warning", "#9a6614"),
    decision: resolveToken("--color-warning", "#9a6614"),
    label: resolveToken("--color-text-secondary", theme === "dark" ? "#a79db3" : "#5e5360"),
    pathHighlight: resolveToken("--color-accent-fill", "#f59520"),
    edge: edgeColorsForTheme(theme),
  };
}

let activeBg: readonly [number, number, number] = THEME_COLORS.dark.bg;

const dimColorCache = new Map<string, string>();
const brightenColorCache = new Map<string, string>();

function clearColorCaches() {
  dimColorCache.clear();
  brightenColorCache.clear();
}

function dimColor(hex: string, amount: number): string {
  const key = hex + amount;
  const cached = dimColorCache.get(key);
  if (cached) return cached;
  const [r, g, b] = hexToRgb(hex);
  const result = rgbToHex(
    Math.round(activeBg[0] + (r - activeBg[0]) * amount),
    Math.round(activeBg[1] + (g - activeBg[1]) * amount),
    Math.round(activeBg[2] + (b - activeBg[2]) * amount),
  );
  dimColorCache.set(key, result);
  return result;
}

function brightenColor(hex: string, factor: number): string {
  const key = hex + factor;
  const cached = brightenColorCache.get(key);
  if (cached) return cached;
  const [r, g, b] = hexToRgb(hex);
  const result = rgbToHex(
    Math.round(r + ((255 - r) * (factor - 1)) / factor),
    Math.round(g + ((255 - g) * (factor - 1)) / factor),
    Math.round(b + ((255 - b) * (factor - 1)) / factor),
  );
  brightenColorCache.set(key, result);
  return result;
}

function desaturateColor(hex: string, amount: number): string {
  const [r, g, b] = hexToRgb(hex);
  const gray = 0.299 * r + 0.587 * g + 0.114 * b;
  return rgbToHex(
    Math.round(r + (gray - r) * amount),
    Math.round(g + (gray - g) * amount),
    Math.round(b + (gray - b) * amount),
  );
}

function tintColor(hex: string, tintHex: string, amount: number): string {
  const [r, g, b] = hexToRgb(hex);
  const [tr, tg, tb] = hexToRgb(tintHex);
  return rgbToHex(
    Math.round(r + (tr - r) * amount),
    Math.round(g + (tg - g) * amount),
    Math.round(b + (tb - b) * amount),
  );
}

export interface UseSigmaOptions {
  container: HTMLDivElement | null;
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes> | null;
  selectedNodeId: string | null;
  hoveredNodeId: string | null;
  highlightedPath: Set<string>;
  highlightedEdges: Set<string>;
  searchDimmedNodes: Set<string> | null;
  communityDimmedNodes: Set<string> | null;
  /** Constellation blossom: non-expanded clusters dimmed to ~35% while a hub
   *  is expanded, so the open cluster reads as foreground. */
  expandDimmedNodes?: Set<string> | null | undefined;
  colorMode: ColorMode;
  activeSignals: Set<Signal>;
  graphTheme: "light" | "dark";
  hiddenNodes?: Set<string> | undefined;
  visibleEdgeTypes?: Set<string> | undefined;
}

export interface UseSigmaReturn {
  sigma: Sigma | null;
  /** Ease the camera onto a node. `ratio` controls the resting zoom (smaller =
   *  closer); defaults to 0.15 (tight, for small file nodes). Pass a larger
   *  ratio for big constellation hubs so the surrounding cluster stays visible. */
  focusNode: (nodeId: string, ratio?: number) => void;
  fitView: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
}

export function useSigmaRenderer(options: UseSigmaOptions): UseSigmaReturn {
  const newBg = (THEME_COLORS[options.graphTheme] ?? THEME_COLORS.dark).bg;
  if (newBg !== activeBg) {
    activeBg = newBg;
    clearColorCaches();
  }

  // Re-resolve theme tokens (risk / hotspot / community / edge / label) when the
  // theme flips so the canvas repaints in the active palette.
  const themeVersion = useThemeVersion();
  const vizRef = useRef<VizPalette>(resolveVizPalette(options.graphTheme));

  const sigmaRef = useRef<Sigma | null>(null);
  // Sigma's stock disc-label drawer, captured from the dynamic import in the
  // init effect so the theme effect can rebuild the label drawer factory.
  const drawDiscRef = useRef<typeof drawDiscNodeLabel | null>(null);
  const [sigmaReady, setSigmaReady] = useState<Sigma | null>(null);
  const selectedRef = useRef<string | null>(null);
  const highlightedPathRef = useRef<Set<string>>(new Set());
  const highlightedEdgesRef = useRef<Set<string>>(new Set());
  const searchDimmedRef = useRef<Set<string> | null>(null);
  const communityDimmedRef = useRef<Set<string> | null>(null);
  const expandDimmedRef = useRef<Set<string> | null>(null);
  const hiddenNodesRef = useRef<Set<string> | undefined>(undefined);
  const graphRef = useRef<Graph<
    SigmaNodeAttributes,
    SigmaEdgeAttributes
  > | null>(null);

  // Sync interaction state refs (no color work here — that's in the graph effect)
  useEffect(() => {
    selectedRef.current = options.selectedNodeId;
    highlightedPathRef.current = options.highlightedPath;
    highlightedEdgesRef.current = options.highlightedEdges;
    searchDimmedRef.current = options.searchDimmedNodes;
    communityDimmedRef.current = options.communityDimmedNodes;
    expandDimmedRef.current = options.expandDimmedNodes ?? null;
    hiddenNodesRef.current = options.hiddenNodes;
    sigmaRef.current?.refresh();
  }, [
    options.selectedNodeId,
    options.highlightedPath,
    options.highlightedEdges,
    options.searchDimmedNodes,
    options.communityDimmedNodes,
    options.expandDimmedNodes,
    options.hiddenNodes,
  ]);

  // Pre-hide edges by type on the graphology graph (batch: 1 event instead of N)
  useEffect(() => {
    const graph = options.graph;
    if (!graph || graph.size === 0) return;
    const visibleTypes = options.visibleEdgeTypes;
    graph.updateEachEdgeAttributes(
      (_edge, attrs) => {
        const shouldHide = visibleTypes ? !visibleTypes.has(attrs.edgeKind) : false;
        if (attrs.hidden === shouldHide) return attrs;
        return { ...attrs, hidden: shouldHide };
      },
      { attributes: ["hidden"] },
    );
  }, [options.visibleEdgeTypes, options.graph]);

  // Pre-apply node colors on the graphology graph (batch: 1 event instead of N)
  useEffect(() => {
    const graph = options.graph;
    if (!graph || graph.order === 0) return;
    const viz = (vizRef.current = resolveVizPalette(options.graphTheme));
    const cm = options.colorMode;
    const coreColor = resolveToken("--color-bg-inset", "#141415");
    graph.updateEachNodeAttributes(
      (_node, attrs) => {
        let color: string;
        // Constellation kinds are always family-colored (hub hue) regardless of
        // the active colorMode — the radial view *is* the community view. The
        // repo-core is a dark plum disc; its halo borrows the soft canvas dot.
        if (attrs.nodeType === "hub") {
          const family = getCommunityFamily(attrs.communityId);
          color = family.hub;
          const next = { ...attrs, color, haloColor: family.satellite || family.hub };
          if (attrs.color === color && attrs.haloColor === next.haloColor) return attrs;
          return next;
        }
        if (attrs.nodeType === "core") {
          color = coreColor;
          if (attrs.color === color) return attrs;
          return { ...attrs, color };
        }
        if (cm === "language") {
          // Modules aggregate many languages and carry none themselves — fall
          // back to the community hue instead of a meaningless "other" gray.
          color =
            attrs.nodeType === "module"
              ? getCommunityFamily(attrs.communityId).hub
              : languageColor(attrs.language || "other");
        } else if (cm === "community") {
          // Modules (centroids) get the hub hue; files use the softer satellite
          // tint so leaves recede behind their community's anchor.
          const family = getCommunityFamily(attrs.communityId);
          color = attrs.nodeType === "module" ? family.hub : family.satellite;
        } else {
          const risk = attrs.pagerank * 3;
          color = risk > 0.7 ? viz.risk.high : risk > 0.3 ? viz.risk.medium : viz.risk.low;
        }
        if (attrs.isDead) color = desaturateColor(color, 0.6);
        if (attrs.isHotspot) color = tintColor(color, viz.hotspot, 0.4);
        // Decision-anchored files get a subtle warm tint so they're
        // discoverable on the canvas without dominating it.
        if (attrs.hasDecision) color = tintColor(color, viz.decision, 0.25);
        if (attrs.color === color) return attrs;
        return { ...attrs, color };
      },
      { attributes: ["color"] },
    );
  }, [options.colorMode, options.graph, options.graphTheme, themeVersion]);

  // Re-color edges by semantic kind for the active theme (canvas can't resolve
  // var()). Build-time colors are placeholders; this is the source of truth.
  useEffect(() => {
    const graph = options.graph;
    if (!graph || graph.size === 0) return;
    const edge = (vizRef.current = resolveVizPalette(options.graphTheme)).edge;
    graph.updateEachEdgeAttributes(
      (_edgeKey, attrs) => {
        const color = edge[attrs.edgeKind] ?? edge.import;
        if (attrs.color === color) return attrs;
        return { ...attrs, color };
      },
      { attributes: ["color"] },
    );
  }, [options.graph, options.graphTheme, themeVersion]);

  // Keep the label color in the active text token when the theme flips, and
  // rebuild the hub/core disc-label + hover drawers so their theme-dependent
  // text/tooltip colors track the toggle (the init-effect closures would
  // otherwise stay pinned to the mount-time theme until remount).
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma) return;
    const label = (vizRef.current = resolveVizPalette(options.graphTheme)).label;
    sigma.setSetting("labelColor", { color: label });
    const drawDisc = drawDiscRef.current;
    if (drawDisc) {
      sigma.setSetting(
        "defaultDrawNodeLabel",
        makeDrawNodeLabel(options.graphTheme, drawDisc),
      );
    }
    sigma.setSetting("defaultDrawNodeHover", makeDrawNodeHover(options.graphTheme));
    sigma.refresh();
  }, [options.graphTheme, themeVersion]);

  // Initialize Sigma (dynamic import to avoid SSR WebGL crash)
  useEffect(() => {
    const container = options.container;
    if (!container) return;

    let cancelled = false;
    let sigmaInstance: Sigma | null = null;

    (async () => {
      const [{ default: SigmaConstructor }, edgeCurveModule, sigmaRendering, graphologyModule] =
        await Promise.all([
          import("sigma"),
          import("@sigma/edge-curve"),
          import("sigma/rendering"),
          import("graphology"),
        ]);
      const EdgeCurveProgram = edgeCurveModule.default;
      const EdgeCurvedArrowProgram = edgeCurveModule.EdgeCurvedArrowProgram;
      const EdgeLineProgram = sigmaRendering.EdgeLineProgram;
      const EdgeArrowProgram = sigmaRendering.EdgeArrowProgram;
      const drawDiscNodeLabel = sigmaRendering.drawDiscNodeLabel;
      drawDiscRef.current = drawDiscNodeLabel;

      if (cancelled) return;

      const graph =
        options.graph ?? new graphologyModule.default() as Graph<SigmaNodeAttributes, SigmaEdgeAttributes>;
      graphRef.current = options.graph;

      const sigma = new SigmaConstructor(graph, container, {
        renderLabels: true,
        labelFont: LABEL_FONT,
        labelSize: LABEL_SIZE,
        labelDensity: getLabelDensity(graph.order),
        labelGridCellSize: LABEL_GRID_CELL_SIZE,
        labelRenderedSizeThreshold: getLabelRenderedSizeThreshold(graph.order),
        labelColor: { color: vizRef.current.label },
        defaultNodeColor: "#6b7280",
        defaultEdgeColor: "#2a2a3a",
        defaultEdgeType: "curved",
        edgeProgramClasses: {
          curved: EdgeCurveProgram,
          curvedArrow: EdgeCurvedArrowProgram,
          arrow: EdgeArrowProgram,
          line: EdgeLineProgram,
        },
        minCameraRatio: 0.002,
        maxCameraRatio: 50,
        hideEdgesOnMove: true,
        zIndex: true,

        // Hub/core disc labels + hover tooltips. Built per theme via module-level
        // factories so the theme effect can re-set them on light/dark toggle
        // without remount (single source of truth — no duplicated drawer logic).
        defaultDrawNodeLabel: makeDrawNodeLabel(options.graphTheme, drawDiscNodeLabel),
        defaultDrawNodeHover: makeDrawNodeHover(options.graphTheme),

        // --- nodeReducer: ONLY handles interaction state (selection, search, path) ---
        // Colors and sizes are pre-set on the graphology graph by the effect above.
        nodeReducer: (node, data) => {
          if (data.hidden) return data;

          const hiddenSet = hiddenNodesRef.current;
          if (hiddenSet?.has(node)) return { ...data, hidden: true };

          const selected = selectedRef.current;
          const pathNodes = highlightedPathRef.current;
          const searchDimmed = searchDimmedRef.current;
          const communityDimmed = communityDimmedRef.current;
          const expandDimmed = expandDimmedRef.current;

          // Fast path: nothing active — return data unchanged, zero allocation
          if (
            !selected &&
            pathNodes.size === 0 &&
            !searchDimmed &&
            !communityDimmed &&
            !expandDimmed
          ) {
            return data;
          }

          if (searchDimmed?.has(node)) {
            return { ...data, color: dimColor(data.color, 0.12), size: (data.size || 6) * 0.5, zIndex: 0 };
          }

          // Blossom dim: other clusters recede to ~35% (size unchanged so the
          // unexpanded constellation stays legible underneath).
          if (expandDimmed?.has(node)) {
            return { ...data, color: dimColor(data.color, 0.35), zIndex: 0 };
          }

          if (communityDimmed?.has(node)) {
            return { ...data, color: dimColor(data.color, 0.1), size: (data.size || 6) * 0.5, zIndex: 0 };
          }

          if (pathNodes.size > 0) {
            if (pathNodes.has(node)) {
              return { ...data, zIndex: 2, highlighted: true };
            }
            return { ...data, color: dimColor(data.color, 0.15), size: (data.size || 6) * 0.5, zIndex: 0 };
          }

          if (selected) {
            const graph = graphRef.current;
            if (graph) {
              if (node === selected) {
                return { ...data, size: (data.size || 6) * 1.8, zIndex: 2, highlighted: true };
              }
              if (graph.hasEdge(node, selected) || graph.hasEdge(selected, node)) {
                return { ...data, size: (data.size || 6) * 1.3, zIndex: 1 };
              }
              return { ...data, color: dimColor(data.color, 0.25), size: (data.size || 6) * 0.6, zIndex: 0 };
            }
          }

          return data;
        },

        // --- edgeReducer: interaction state only ---
        // Edge visibility by type is pre-set on the graph. No idle dimming.
        edgeReducer: (edge, data) => {
          if (data.hidden) return data;

          const selected = selectedRef.current;
          const pathEdges = highlightedEdgesRef.current;
          const pathNodes = highlightedPathRef.current;

          // Fast path: nothing active — zero allocation
          if (!selected && pathEdges.size === 0) return data;

          const graph = graphRef.current;

          if (pathEdges.size > 0) {
            if (pathEdges.has(edge)) {
              return { ...data, color: vizRef.current.pathHighlight, size: Math.max(3, (data.size || 1) * 3), zIndex: 2 };
            }
            if (pathNodes.size > 0 && graph) {
              const [source, target] = graph.extremities(edge);
              if (source && target && pathNodes.has(source) && pathNodes.has(target)) {
                return data;
              }
            }
            return { ...data, color: dimColor(data.color, 0.08), size: 0.2, zIndex: 0 };
          }

          if (selected && graph) {
            const [source, target] = graph.extremities(edge);
            const isConnected = source === selected || target === selected;
            if (isConnected) {
              return { ...data, color: brightenColor(data.color, 1.5), size: Math.max(3, (data.size || 1) * 4), zIndex: 2 };
            }
            return { ...data, color: dimColor(data.color, 0.1), size: 0.3, zIndex: 0 };
          }

          return data;
        },
      });

      sigmaInstance = sigma;
      sigmaRef.current = sigma;
      setSigmaReady(sigma);
    })();

    return () => {
      cancelled = true;
      if (sigmaInstance) {
        sigmaInstance.kill();
        sigmaInstance = null;
      }
      sigmaRef.current = null;
      setSigmaReady(null);
    };
  }, [options.container]);

  // Update graph when it changes
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma || !options.graph) return;
    graphRef.current = options.graph;
    sigma.setGraph(options.graph);
    sigma.getCamera().animatedReset({ duration: 500 });
  }, [options.graph]);

  const focusNode = useCallback((nodeId: string, ratio = 0.15) => {
    const sigma = sigmaRef.current;
    const graph = graphRef.current;
    if (!sigma || !graph || !graph.hasNode(nodeId)) return;
    // Camera state lives in Sigma's *framed* (normalized) coordinate space, NOT
    // raw graph coords. Raw graph x/y (radial hubs sit hundreds of units from
    // the origin) would fly the camera off into blank canvas. getNodeDisplayData
    // returns the node's position already in the camera's coordinate system.
    const display = sigma.getNodeDisplayData(nodeId);
    if (!display) return;
    sigma.getCamera().animate(
      { x: display.x, y: display.y, ratio },
      { duration: 400 },
    );
  }, []);

  const fitView = useCallback(() => {
    sigmaRef.current?.getCamera().animatedReset({ duration: 300 });
  }, []);

  const zoomIn = useCallback(() => {
    sigmaRef.current?.getCamera().animatedZoom({ duration: 200 });
  }, []);

  const zoomOut = useCallback(() => {
    sigmaRef.current?.getCamera().animatedUnzoom({ duration: 200 });
  }, []);

  return {
    sigma: sigmaReady,
    focusNode,
    fitView,
    zoomIn,
    zoomOut,
  };
}
