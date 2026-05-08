import { useRef, useEffect, useCallback, type RefObject } from "react";
import Sigma from "sigma";
import type Graph from "graphology";
import EdgeCurveProgram from "@sigma/edge-curve";
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

function hexToRgb(hex: string): { r: number; g: number; b: number } {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  return result
    ? {
        r: parseInt(result[1]!, 16),
        g: parseInt(result[2]!, 16),
        b: parseInt(result[3]!, 16),
      }
    : { r: 100, g: 100, b: 100 };
}

function rgbToHex(r: number, g: number, b: number): string {
  return (
    "#" +
    [r, g, b]
      .map((x) => {
        const hex = Math.max(0, Math.min(255, Math.round(x))).toString(16);
        return hex.length === 1 ? "0" + hex : hex;
      })
      .join("")
  );
}

function desaturateColor(hex: string, amount: number): string {
  const rgb = hexToRgb(hex);
  const gray = 0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b;
  return rgbToHex(
    rgb.r + (gray - rgb.r) * amount,
    rgb.g + (gray - rgb.g) * amount,
    rgb.b + (gray - rgb.b) * amount,
  );
}

function tintColor(hex: string, tintHex: string, amount: number): string {
  const rgb = hexToRgb(hex);
  const tint = hexToRgb(tintHex);
  return rgbToHex(
    rgb.r + (tint.r - rgb.r) * amount,
    rgb.g + (tint.g - rgb.g) * amount,
    rgb.b + (tint.b - rgb.b) * amount,
  );
}

function dimColor(hex: string, amount: number): string {
  const rgb = hexToRgb(hex);
  const bg = { r: 18, g: 18, b: 28 };
  return rgbToHex(
    bg.r + (rgb.r - bg.r) * amount,
    bg.g + (rgb.g - bg.g) * amount,
    bg.b + (rgb.b - bg.b) * amount,
  );
}

function brightenColor(hex: string, factor: number): string {
  const rgb = hexToRgb(hex);
  return rgbToHex(
    rgb.r + ((255 - rgb.r) * (factor - 1)) / factor,
    rgb.g + ((255 - rgb.g) * (factor - 1)) / factor,
    rgb.b + ((255 - rgb.b) * (factor - 1)) / factor,
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
  const selectedRef = useRef<string | null>(null);
  const highlightedPathRef = useRef<Set<string>>(new Set());
  const highlightedEdgesRef = useRef<Set<string>>(new Set());
  const searchDimmedRef = useRef<Set<string> | null>(null);
  const communityDimmedRef = useRef<Set<string> | null>(null);
  const colorModeRef = useRef<ColorMode>("language");
  const hiddenNodesRef = useRef<Set<string> | undefined>(undefined);
  const visibleEdgeTypesRef = useRef<Set<string> | undefined>(undefined);
  const graphRef = useRef<Graph<
    SigmaNodeAttributes,
    SigmaEdgeAttributes
  > | null>(null);

  // Sync refs from options
  useEffect(() => {
    selectedRef.current = options.selectedNodeId;
    highlightedPathRef.current = options.highlightedPath;
    highlightedEdgesRef.current = options.highlightedEdges;
    searchDimmedRef.current = options.searchDimmedNodes;
    communityDimmedRef.current = options.communityDimmedNodes;
    colorModeRef.current = options.colorMode;
    hiddenNodesRef.current = options.hiddenNodes;
    visibleEdgeTypesRef.current = options.visibleEdgeTypes;
    sigmaRef.current?.refresh();
  }, [
    options.selectedNodeId,
    options.highlightedPath,
    options.highlightedEdges,
    options.searchDimmedNodes,
    options.communityDimmedNodes,
    options.colorMode,
    options.hiddenNodes,
    options.visibleEdgeTypes,
  ]);

  // Initialize Sigma
  useEffect(() => {
    const container = options.containerRef.current;
    if (!container) return;

    const graph =
      options.graph ?? new (require("graphology").default)() as Graph<SigmaNodeAttributes, SigmaEdgeAttributes>;
    graphRef.current = options.graph;

    const sigma = new Sigma(graph, container, {
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

        const size = settings.labelSize || 11;
        const font = settings.labelFont || "JetBrains Mono, monospace";
        const weight = "500";
        context.font = `${weight} ${size}px ${font}`;
        const textWidth = context.measureText(label).width;

        const nodeSize = data.size || 8;
        const x = data.x;
        const y = data.y - nodeSize - 10;
        const paddingX = 8;
        const paddingY = 5;
        const height = size + paddingY * 2;
        const width = textWidth + paddingX * 2;
        const radius = 4;

        context.fillStyle = "#12121c";
        context.beginPath();
        context.roundRect(
          x - width / 2,
          y - height / 2,
          width,
          height,
          radius,
        );
        context.fill();
        context.lineWidth = 2;
        context.strokeStyle = data.color || "#6366f1";
        context.stroke();

        context.fillStyle = "#f5f5f7";
        context.textAlign = "center";
        context.textBaseline = "middle";
        context.fillText(label, x, y);

        context.beginPath();
        context.arc(data.x, data.y, nodeSize + 4, 0, Math.PI * 2);
        context.strokeStyle = data.color || "#6366f1";
        context.lineWidth = 2;
        context.globalAlpha = 0.5;
        context.stroke();
        context.globalAlpha = 1;
      },

      nodeReducer: (node, data) => {
        const res = { ...data };
        if (data.hidden) {
          res.hidden = true;
          return res;
        }

        const hiddenSet = hiddenNodesRef.current;
        if (hiddenSet && hiddenSet.has(node)) {
          res.hidden = true;
          return res;
        }

        const graph = graphRef.current;
        const selected = selectedRef.current;
        const pathNodes = highlightedPathRef.current;
        const searchDimmed = searchDimmedRef.current;
        const communityDimmed = communityDimmedRef.current;
        const cm = colorModeRef.current;

        const attrs = graph?.getNodeAttributes(node);
        if (attrs) {
          if (cm === "language") {
            res.color = languageColor(attrs.language || "other");
          } else if (cm === "community") {
            res.color = getCommunityColor(attrs.communityId);
          } else if (cm === "risk") {
            const risk = attrs.pagerank * 3;
            res.color =
              risk > 0.7 ? "#ef4444" : risk > 0.3 ? "#f59e0b" : "#22c55e";
          }
        }

        // Signal overlays
        if (attrs) {
          if (attrs.isDead) {
            res.color = desaturateColor(res.color, 0.6);
          }
          if (attrs.isHotspot) {
            res.color = tintColor(res.color, "#f97316", 0.4);
          }
          if (attrs.isEntryPoint) {
            res.size = (res.size || 6) * 1.5;
          }

          if (attrs.betweenness > 0.01) {
            res.size = (res.size || 6) * (1 + Math.min(attrs.betweenness * 3, 0.8));
          }

          if (attrs.nodeType === "module") {
            const docPct = attrs.docCoveragePct ?? 0;
            if (docPct < 0.3) {
              res.color = desaturateColor(res.color, 0.4);
            }
          }
        }

        if (searchDimmed && searchDimmed.has(node)) {
          res.color = dimColor(res.color, 0.12);
          res.size = (data.size || 6) * 0.5;
          res.zIndex = 0;
          return res;
        }

        if (communityDimmed && communityDimmed.has(node)) {
          res.color = dimColor(res.color, 0.1);
          res.size = (data.size || 6) * 0.5;
          res.zIndex = 0;
          return res;
        }

        if (pathNodes.size > 0) {
          if (pathNodes.has(node)) {
            res.zIndex = 2;
            res.highlighted = true;
          } else {
            res.color = dimColor(res.color, 0.15);
            res.size = (data.size || 6) * 0.5;
            res.zIndex = 0;
          }
          return res;
        }

        if (selected && graph) {
          const isSelected = node === selected;
          const isNeighbor =
            graph.hasEdge(node, selected) || graph.hasEdge(selected, node);

          if (isSelected) {
            res.size = (data.size || 6) * 1.8;
            res.zIndex = 2;
            res.highlighted = true;
          } else if (isNeighbor) {
            res.size = (data.size || 6) * 1.3;
            res.zIndex = 1;
          } else {
            res.color = dimColor(res.color, 0.25);
            res.size = (data.size || 6) * 0.6;
            res.zIndex = 0;
          }
        }

        return res;
      },

      edgeReducer: (edge, data) => {
        const res = { ...data };

        const visibleTypes = visibleEdgeTypesRef.current;
        if (visibleTypes) {
          const g = graphRef.current;
          if (g) {
            const edgeAttrs = g.getEdgeAttributes(edge);
            if (!visibleTypes.has(edgeAttrs.edgeKind)) {
              res.hidden = true;
              return res;
            }
          }
        }

        const selected = selectedRef.current;
        const pathNodes = highlightedPathRef.current;
        const pathEdges = highlightedEdgesRef.current;
        const graph = graphRef.current;

        if (pathEdges.size > 0) {
          if (pathEdges.has(edge)) {
            res.color = "#f59e0b";
            res.size = Math.max(3, (data.size || 1) * 3);
            res.zIndex = 2;
          } else if (pathNodes.size > 0 && graph) {
            const [source, target] = graph.extremities(edge);
            if (source && target && pathNodes.has(source) && pathNodes.has(target)) {
              res.size = data.size || 1;
            } else {
              res.color = dimColor(data.color, 0.08);
              res.size = 0.2;
              res.zIndex = 0;
            }
          }
          return res;
        }

        if (selected && graph) {
          const [source, target] = graph.extremities(edge);
          const isConnected = source === selected || target === selected;
          if (isConnected) {
            res.color = brightenColor(data.color, 1.5);
            res.size = Math.max(3, (data.size || 1) * 4);
            res.zIndex = 2;
          } else {
            res.color = dimColor(data.color, 0.1);
            res.size = 0.3;
            res.zIndex = 0;
          }
        }

        return res;
      },
    });

    sigmaRef.current = sigma;

    return () => {
      sigma.kill();
      sigmaRef.current = null;
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
    sigma: sigmaRef.current,
    focusNode,
    fitView,
    zoomIn,
    zoomOut,
  };
}
