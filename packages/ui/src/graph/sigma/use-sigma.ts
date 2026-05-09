import { useRef, useEffect, useCallback, useState, type RefObject } from "react";
import type Sigma from "sigma";
import type Graph from "graphology";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./types";
import type { ColorMode } from "../graph-toolbar";
import type { Signal } from "../context";
import {
  LABEL_FONT,
  LABEL_SIZE,
  LABEL_DENSITY,
  LABEL_GRID_CELL_SIZE,
  LABEL_RENDERED_SIZE_THRESHOLD,
  getCommunityColor,
  languageColor,
} from "./constants";

// ---- Color helpers (kept minimal — avoid regex in hot paths) ----

function hexToRgb(hex: string): [number, number, number] {
  const v = parseInt(hex.slice(1), 16);
  return [(v >> 16) & 255, (v >> 8) & 255, v & 255];
}

function rgbToHex(r: number, g: number, b: number): string {
  return "#" + ((1 << 24) | (r << 16) | (g << 8) | b).toString(16).slice(1);
}

const BG_R = 18, BG_G = 18, BG_B = 28;

function dimColor(hex: string, amount: number): string {
  const [r, g, b] = hexToRgb(hex);
  return rgbToHex(
    Math.round(BG_R + (r - BG_R) * amount),
    Math.round(BG_G + (g - BG_G) * amount),
    Math.round(BG_B + (b - BG_B) * amount),
  );
}

function brightenColor(hex: string, factor: number): string {
  const [r, g, b] = hexToRgb(hex);
  return rgbToHex(
    Math.round(r + ((255 - r) * (factor - 1)) / factor),
    Math.round(g + ((255 - g) * (factor - 1)) / factor),
    Math.round(b + ((255 - b) * (factor - 1)) / factor),
  );
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
  containerRef: RefObject<HTMLDivElement | null>;
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes> | null;
  selectedNodeId: string | null;
  hoveredNodeId: string | null;
  highlightedPath: Set<string>;
  highlightedEdges: Set<string>;
  searchDimmedNodes: Set<string> | null;
  communityDimmedNodes: Set<string> | null;
  colorMode: ColorMode;
  activeSignals: Set<Signal>;
  graphTheme: "light" | "dark";
  hiddenNodes?: Set<string> | undefined;
  visibleEdgeTypes?: Set<string> | undefined;
}

export interface UseSigmaReturn {
  sigma: Sigma | null;
  focusNode: (nodeId: string) => void;
  fitView: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
}

export function useSigmaRenderer(options: UseSigmaOptions): UseSigmaReturn {
  const sigmaRef = useRef<Sigma | null>(null);
  const [sigmaReady, setSigmaReady] = useState<Sigma | null>(null);
  const selectedRef = useRef<string | null>(null);
  const highlightedPathRef = useRef<Set<string>>(new Set());
  const highlightedEdgesRef = useRef<Set<string>>(new Set());
  const searchDimmedRef = useRef<Set<string> | null>(null);
  const communityDimmedRef = useRef<Set<string> | null>(null);
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
    hiddenNodesRef.current = options.hiddenNodes;
    sigmaRef.current?.refresh();
  }, [
    options.selectedNodeId,
    options.highlightedPath,
    options.highlightedEdges,
    options.searchDimmedNodes,
    options.communityDimmedNodes,
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
    const cm = options.colorMode;
    graph.updateEachNodeAttributes(
      (_node, attrs) => {
        let color: string;
        if (cm === "language") {
          color = languageColor(attrs.language || "other");
        } else if (cm === "community") {
          color = getCommunityColor(attrs.communityId);
        } else {
          const risk = attrs.pagerank * 3;
          color = risk > 0.7 ? "#ef4444" : risk > 0.3 ? "#f59e0b" : "#22c55e";
        }
        if (attrs.isDead) color = desaturateColor(color, 0.6);
        if (attrs.isHotspot) color = tintColor(color, "#f97316", 0.4);
        if (attrs.color === color) return attrs;
        return { ...attrs, color };
      },
      { attributes: ["color"] },
    );
  }, [options.colorMode, options.graph]);

  // Initialize Sigma (dynamic import to avoid SSR WebGL crash)
  useEffect(() => {
    const container = options.containerRef.current;
    if (!container) return;

    let cancelled = false;
    let sigmaInstance: Sigma | null = null;

    (async () => {
      const [{ default: SigmaConstructor }, { default: EdgeCurveProgram }, graphologyModule] =
        await Promise.all([
          import("sigma"),
          import("@sigma/edge-curve"),
          import("graphology"),
        ]);

      if (cancelled) return;

      const graph =
        options.graph ?? new graphologyModule.default() as Graph<SigmaNodeAttributes, SigmaEdgeAttributes>;
      graphRef.current = options.graph;

      const sigma = new SigmaConstructor(graph, container, {
        renderLabels: true,
        labelFont: LABEL_FONT,
        labelSize: LABEL_SIZE,
        labelDensity: LABEL_DENSITY,
        labelGridCellSize: LABEL_GRID_CELL_SIZE,
        labelRenderedSizeThreshold: LABEL_RENDERED_SIZE_THRESHOLD,
        labelColor: { color: "#e4e4ed" },
        defaultNodeColor: "#6b7280",
        defaultEdgeColor: "#2a2a3a",
        defaultEdgeType: "curved",
        edgeProgramClasses: {
          curved: EdgeCurveProgram,
        },
        minCameraRatio: 0.002,
        maxCameraRatio: 50,
        hideEdgesOnMove: true,
        zIndex: true,

        defaultDrawNodeHover: (context, data, settings) => {
          const label = data.label;
          if (!label) return;

          const extra = data as Record<string, unknown>;
          const fullPath = (extra.fullPath as string) ?? undefined;

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

          context.fillStyle = "#12121c";
          context.beginPath();
          context.roundRect(x - width / 2, y - height / 2, width, height, radius);
          context.fill();
          context.lineWidth = 2;
          context.strokeStyle = data.color || "#6366f1";
          context.stroke();

          context.textAlign = "center";
          context.textBaseline = "middle";

          const labelY = showPath ? y - (lineGap + secondarySize) / 2 : y;
          context.fillStyle = "#f5f5f7";
          context.font = `500 ${primarySize}px ${font}`;
          context.fillText(label, x, labelY);

          if (showPath) {
            context.fillStyle = "#888888";
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
        },

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

          // Fast path: nothing active — return data unchanged, zero allocation
          if (!selected && pathNodes.size === 0 && !searchDimmed && !communityDimmed) {
            return data;
          }

          if (searchDimmed?.has(node)) {
            return { ...data, color: dimColor(data.color, 0.12), size: (data.size || 6) * 0.5, zIndex: 0 };
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
              return { ...data, color: "#f59e0b", size: Math.max(3, (data.size || 1) * 3), zIndex: 2 };
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options.containerRef.current]);

  // Update graph when it changes
  useEffect(() => {
    const sigma = sigmaRef.current;
    if (!sigma || !options.graph) return;
    graphRef.current = options.graph;
    sigma.setGraph(options.graph);
    sigma.getCamera().animatedReset({ duration: 500 });
  }, [options.graph]);

  const focusNode = useCallback((nodeId: string) => {
    const sigma = sigmaRef.current;
    const graph = graphRef.current;
    if (!sigma || !graph || !graph.hasNode(nodeId)) return;
    const attrs = graph.getNodeAttributes(nodeId);
    sigma.getCamera().animate(
      { x: attrs.x, y: attrs.y, ratio: 0.15 },
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
