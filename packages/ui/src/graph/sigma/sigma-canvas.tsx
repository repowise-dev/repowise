"use client";

import {
  useRef,
  useState,
  useCallback,
  forwardRef,
  useImperativeHandle,
  useEffect,
} from "react";
import type Graph from "graphology";
import type { SigmaNodeEventPayload } from "sigma/types";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "./types";
import type { ColorMode, ViewMode } from "../graph-toolbar";
import type { Signal } from "../context";
import type {
  GraphNode as GraphNodeResponse,
  GraphLink as GraphEdgeResponse,
  ModuleNode as ModuleNodeResponse,
  ModuleEdge as ModuleEdgeResponse,
} from "@repowise-dev/types/graph";
import { useSigmaRenderer } from "./use-sigma";
import { useFA2Layout } from "./use-fa2-layout";
import { useElkSigmaLayout } from "./use-elk-sigma-layout";
import { SigmaControls } from "./sigma-controls";
import { SigmaMinimap } from "./sigma-minimap";
import { DepthRings } from "./depth-rings";

export interface SigmaCanvasProps {
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes> | null;
  // "radial" = constellation: positions are pre-computed, no FA2/ELK runs.
  layoutMode: "force" | "hierarchical" | "radial";
  viewMode: ViewMode;
  selectedNodeId: string | null;
  hoveredNodeId: string | null;
  highlightedPath: Set<string>;
  highlightedEdges: Set<string>;
  searchDimmedNodes: Set<string> | null;
  communityDimmedNodes: Set<string> | null;
  /** Constellation blossom: clusters dimmed to ~35% while a hub is expanded. */
  expandDimmedNodes?: Set<string> | null | undefined;
  colorMode: ColorMode;
  activeSignals: Set<Signal>;
  graphTheme: "light" | "dark";
  fileNodes?: GraphNodeResponse[] | undefined;
  fileEdges?: GraphEdgeResponse[] | undefined;
  moduleNodes?: ModuleNodeResponse[] | undefined;
  moduleEdges?: ModuleEdgeResponse[] | undefined;
  onNodeClick?: ((nodeId: string, nodeType: string) => void) | undefined;
  onNodeHover?: ((nodeId: string | null) => void) | undefined;
  onNodeContextMenu?:
    | ((event: MouseEvent, nodeId: string, nodeType: string) => void)
    | undefined;
  /** Returns true when the double-click performed an action (drill/expand/docs)
   *  so the canvas suppresses Sigma's built-in camera zoom. Returning false (or
   *  void) leaves the default zoom intact (e.g. constellation core). */
  onNodeDoubleClick?:
    | ((nodeId: string, nodeType: string) => boolean | void)
    | undefined;
  onStageClick?: (() => void) | undefined;
  hiddenNodes?: Set<string> | undefined;
  visibleEdgeTypes?: Set<string> | undefined;
  /** Concentric depth-ring radii (graph coords) for the constellation underlay. */
  depthRingRadii?: readonly [number, number, number] | null | undefined;
}

export interface SigmaCanvasHandle {
  focusNode: (nodeId: string, ratio?: number) => void;
  fitView: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
}

export const SigmaCanvas = forwardRef<SigmaCanvasHandle, SigmaCanvasProps>(
  function SigmaCanvas(props, ref) {
    const [container, setContainer] = useState<HTMLDivElement | null>(null);
    const containerCallback = useCallback((node: HTMLDivElement | null) => {
      setContainer(node);
    }, []);

    const { sigma, focusNode, fitView, zoomIn, zoomOut } = useSigmaRenderer({
      container,
      graph: props.graph,
      selectedNodeId: props.selectedNodeId,
      hoveredNodeId: props.hoveredNodeId,
      highlightedPath: props.highlightedPath,
      highlightedEdges: props.highlightedEdges,
      searchDimmedNodes: props.searchDimmedNodes,
      communityDimmedNodes: props.communityDimmedNodes,
      expandDimmedNodes: props.expandDimmedNodes,
      colorMode: props.colorMode,
      activeSignals: props.activeSignals,
      graphTheme: props.graphTheme,
      hiddenNodes: props.hiddenNodes,
      visibleEdgeTypes: props.visibleEdgeTypes,
    });

    const { isRunning: isLayoutRunning, toggle: toggleLayout } = useFA2Layout({
      graph: props.graph,
      sigma,
      enabled: props.layoutMode === "force",
    });

    const { isComputing: isElkComputing } = useElkSigmaLayout({
      graph: props.graph,
      sigma,
      enabled: props.layoutMode === "hierarchical",
      fileNodes: props.fileNodes,
      fileEdges: props.fileEdges,
      moduleNodes: props.moduleNodes,
      moduleEdges: props.moduleEdges,
      viewMode: props.viewMode,
    });

    // Keep callback refs up to date so Sigma event handlers always use latest props
    const onNodeClickRef = useRef(props.onNodeClick);
    const onStageClickRef = useRef(props.onStageClick);
    const onNodeHoverRef = useRef(props.onNodeHover);
    const onNodeContextMenuRef = useRef(props.onNodeContextMenu);
    const onNodeDoubleClickRef = useRef(props.onNodeDoubleClick);
    useEffect(() => {
      onNodeClickRef.current = props.onNodeClick;
      onStageClickRef.current = props.onStageClick;
      onNodeHoverRef.current = props.onNodeHover;
      onNodeContextMenuRef.current = props.onNodeContextMenu;
      onNodeDoubleClickRef.current = props.onNodeDoubleClick;
    });

    // Wire Sigma events
    useEffect(() => {
      if (!sigma) return;

      const handleClickNode = ({ node }: { node: string }) => {
        const graph = sigma.getGraph();
        const attrs = graph.getNodeAttributes(node);
        onNodeClickRef.current?.(node, attrs.nodeType);
      };

      const handleClickStage = () => {
        onStageClickRef.current?.();
      };

      const handleEnterNode = ({ node }: { node: string }) => {
        onNodeHoverRef.current?.(node);
        if (container)
          container.style.cursor = "pointer";
      };

      const handleLeaveNode = () => {
        onNodeHoverRef.current?.(null);
        if (container) container.style.cursor = "grab";
      };

      const handleRightClickNode = ({
        node,
        event,
      }: {
        node: string;
        event: { original: MouseEvent | TouchEvent };
      }) => {
        event.original.preventDefault();
        if (event.original instanceof MouseEvent) {
          const graph = sigma.getGraph();
          const attrs = graph.getNodeAttributes(node);
          onNodeContextMenuRef.current?.(event.original, node, attrs.nodeType);
        }
      };

      const handleDoubleClickNode = (payload: SigmaNodeEventPayload) => {
        const { node } = payload;
        const graph = sigma.getGraph();
        const attrs = graph.getNodeAttributes(node);
        const handled = onNodeDoubleClickRef.current?.(node, attrs.nodeType);
        // Suppress Sigma's built-in camera zoom for node types that performed an
        // action (module/hub/file). Node types that no-op (constellation core)
        // return false/void and keep the default zoom.
        if (handled) payload.preventSigmaDefault();
      };

      sigma.on("clickNode", handleClickNode);
      sigma.on("clickStage", handleClickStage);
      sigma.on("enterNode", handleEnterNode);
      sigma.on("leaveNode", handleLeaveNode);
      sigma.on("rightClickNode", handleRightClickNode);
      sigma.on("doubleClickNode", handleDoubleClickNode);

      return () => {
        sigma.off("clickNode", handleClickNode);
        sigma.off("clickStage", handleClickStage);
        sigma.off("enterNode", handleEnterNode);
        sigma.off("leaveNode", handleLeaveNode);
        sigma.off("rightClickNode", handleRightClickNode);
        sigma.off("doubleClickNode", handleDoubleClickNode);
      };
    }, [sigma, container]);

    useImperativeHandle(ref, () => ({
      focusNode,
      fitView,
      zoomIn,
      zoomOut,
    }));

    const hasDepthRings = !!props.depthRingRadii;
    return (
      <div
        className="relative w-full h-full"
        style={
          // For the constellation, paint the dark canvas on the wrapper and keep
          // the sigma container transparent so the depth-ring underlay shows.
          hasDepthRings && props.graphTheme === "dark"
            ? { background: "var(--color-bg-canvas)" }
            : undefined
        }
      >
        {hasDepthRings && props.depthRingRadii && (
          <DepthRings sigma={sigma} ringRadii={props.depthRingRadii} />
        )}
        <div
          ref={containerCallback}
          className="w-full h-full relative z-[1]"
          style={{
            background:
              !hasDepthRings && props.graphTheme === "dark"
                ? "var(--color-bg-canvas)"
                : "transparent",
            cursor: "grab",
          }}
        />
        <SigmaControls
          onZoomIn={zoomIn}
          onZoomOut={zoomOut}
          onFitView={fitView}
          onFocusSelected={
            props.selectedNodeId
              ? () => focusNode(props.selectedNodeId!)
              : undefined
          }
          isLayoutRunning={props.layoutMode === "force" ? isLayoutRunning : isElkComputing}
          onToggleLayout={props.layoutMode === "force" ? toggleLayout : undefined}
          graphTheme={props.graphTheme}
        />
        <SigmaMinimap sigma={sigma} graphTheme={props.graphTheme} />
      </div>
    );
  },
);
