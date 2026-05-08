"use client";

import {
  useRef,
  forwardRef,
  useImperativeHandle,
  useEffect,
} from "react";
import type Graph from "graphology";
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

export interface SigmaCanvasProps {
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes> | null;
  layoutMode: "force" | "hierarchical";
  viewMode: ViewMode;
  selectedNodeId: string | null;
  hoveredNodeId: string | null;
  highlightedPath: Set<string>;
  highlightedEdges: Set<string>;
  searchDimmedNodes: Set<string> | null;
  communityDimmedNodes: Set<string> | null;
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
  onNodeDoubleClick?: ((nodeId: string, nodeType: string) => void) | undefined;
  onStageClick?: (() => void) | undefined;
}

export interface SigmaCanvasHandle {
  focusNode: (nodeId: string) => void;
  fitView: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
}

export const SigmaCanvas = forwardRef<SigmaCanvasHandle, SigmaCanvasProps>(
  function SigmaCanvas(props, ref) {
    const containerRef = useRef<HTMLDivElement>(null);

    const { sigma, focusNode, fitView, zoomIn, zoomOut } = useSigmaRenderer({
      containerRef,
      graph: props.graph,
      selectedNodeId: props.selectedNodeId,
      hoveredNodeId: props.hoveredNodeId,
      highlightedPath: props.highlightedPath,
      highlightedEdges: props.highlightedEdges,
      searchDimmedNodes: props.searchDimmedNodes,
      communityDimmedNodes: props.communityDimmedNodes,
      colorMode: props.colorMode,
      activeSignals: props.activeSignals,
      graphTheme: props.graphTheme,
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

    // Wire Sigma events
    useEffect(() => {
      if (!sigma) return;

      const handleClickNode = ({ node }: { node: string }) => {
        const graph = sigma.getGraph();
        const attrs = graph.getNodeAttributes(node);
        props.onNodeClick?.(node, attrs.nodeType);
      };

      const handleClickStage = () => {
        props.onStageClick?.();
      };

      const handleEnterNode = ({ node }: { node: string }) => {
        props.onNodeHover?.(node);
        if (containerRef.current)
          containerRef.current.style.cursor = "pointer";
      };

      const handleLeaveNode = () => {
        props.onNodeHover?.(null);
        if (containerRef.current) containerRef.current.style.cursor = "grab";
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
          props.onNodeContextMenu?.(event.original, node, attrs.nodeType);
        }
      };

      const handleDoubleClickNode = ({ node }: { node: string }) => {
        const graph = sigma.getGraph();
        const attrs = graph.getNodeAttributes(node);
        props.onNodeDoubleClick?.(node, attrs.nodeType);
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
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [sigma]);

    useImperativeHandle(ref, () => ({
      focusNode,
      fitView,
      zoomIn,
      zoomOut,
    }));

    return (
      <div className="relative w-full h-full">
        <div
          ref={containerRef}
          className="w-full h-full"
          style={{
            background:
              props.graphTheme === "dark" ? "#0f0f1a" : "transparent",
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
