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
} from "@repowise-dev/types/graph";
import { useSigmaRenderer, type CameraPosition } from "./use-sigma";
import { useFA2Layout } from "./use-fa2-layout";
import { useElkSigmaLayout } from "./use-elk-sigma-layout";
import { SigmaControls } from "./sigma-controls";
import { DepthRings } from "./depth-rings";
import { CanvasGround } from "./canvas-ground";

export interface SigmaCanvasProps {
  graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes> | null;
  // "radial" = constellation: positions are pre-computed, no FA2/ELK runs.
  layoutMode: "force" | "hierarchical" | "radial";
  viewMode: ViewMode;
  selectedNodeId: string | null;
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
  onNodeClick?: ((nodeId: string, nodeType: string) => void) | undefined;
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
  /** Fired when the hierarchical (ELK) layout refuses to run (too many
   *  nodes) so the host can explain the dead-looking toggle to the user. */
  onLayoutSkipped?: ((reason: string) => void) | undefined;
  hiddenNodes?: Set<string> | undefined;
  visibleEdgeTypes?: Set<string> | undefined;
  /** Concentric depth-ring radii (graph coords) for the constellation underlay. */
  depthRingRadii?: readonly [number, number, number] | null | undefined;
  /** Suppress the camera easings. Honour `prefers-reduced-motion` here rather
   *  than reading the media query inside the renderer. */
  reducedMotion?: boolean | undefined;
}

export interface SigmaCanvasHandle {
  focusNode: (nodeId: string, ratio?: number) => void;
  fitView: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  /** Where the camera would sit to frame `nodeId` in the graph drawn *now*. */
  nodeCamera: (nodeId: string, ratio?: number) => CameraPosition | null;
  /** Seed the camera's starting point for the next graph swap (consumed once),
   *  so a drill-down reads as one movement instead of a hard cut. */
  setEntryCamera: (state: CameraPosition | null) => void;
}

export const SigmaCanvas = forwardRef<SigmaCanvasHandle, SigmaCanvasProps>(
  function SigmaCanvas(props, ref) {
    const [container, setContainer] = useState<HTMLDivElement | null>(null);
    const containerCallback = useCallback((node: HTMLDivElement | null) => {
      setContainer(node);
    }, []);

    const { sigma, focusNode, fitView, zoomIn, zoomOut, nodeCamera, setEntryCamera } =
      useSigmaRenderer({
      container,
      graph: props.graph,
      selectedNodeId: props.selectedNodeId,
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
      reducedMotion: props.reducedMotion,
    });

    const { isRunning: isLayoutRunning } = useFA2Layout({
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
      onSkipped: props.onLayoutSkipped,
    });

    // Keep callback refs up to date so Sigma event handlers always use latest props
    const onNodeClickRef = useRef(props.onNodeClick);
    const onStageClickRef = useRef(props.onStageClick);
    const onNodeContextMenuRef = useRef(props.onNodeContextMenu);
    const onNodeDoubleClickRef = useRef(props.onNodeDoubleClick);
    useEffect(() => {
      onNodeClickRef.current = props.onNodeClick;
      onStageClickRef.current = props.onStageClick;
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

      // Cursor affordance only. Sigma draws its own hover highlight, so hover
      // never needs to reach React — an `onNodeHover` callback used to publish
      // it into shell state that nothing read, re-rendering the whole shell on
      // every hover transition.
      const handleEnterNode = () => {
        if (container) container.style.cursor = "pointer";
      };

      const handleLeaveNode = () => {
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

    // Sigma 3 re-measures only inside its own render, and only listens on
    // window resize. The canvas cell now changes size without the window
    // doing so: the rail toggles the grid, the key row collapses, a banner
    // appears. On a settled graph nothing would trigger a render, leaving
    // stale dimensions behind the ground and the rings too.
    useEffect(() => {
      if (!sigma || !container || typeof ResizeObserver === "undefined") return;
      const observer = new ResizeObserver(() => sigma.refresh());
      observer.observe(container);
      return () => observer.disconnect();
    }, [sigma, container]);

    useImperativeHandle(ref, () => ({
      focusNode,
      fitView,
      zoomIn,
      zoomOut,
      nodeCamera,
      setEntryCamera,
    }));

    const hasDepthRings = !!props.depthRingRadii;
    return (
      <div
        className="relative w-full h-full"
        style={
          // The plane is painted here, under the ground, so the underlays show
          // through a transparent Sigma container. Rule 8: the canvas is the
          // subject, so it takes the page plane rather than a well below it.
          props.graphTheme === "dark" ? { background: "var(--color-bg-root)" } : undefined
        }
      >
        {/* Every scope gets a ground. The rings were constellation-only, which
            left the file graph drawing nodes on a flat plane. */}
        <CanvasGround sigma={sigma} />
        {hasDepthRings && props.depthRingRadii && (
          <DepthRings sigma={sigma} ringRadii={props.depthRingRadii} />
        )}
        <div
          ref={containerCallback}
          className="w-full h-full relative z-[1]"
          style={{ background: "transparent", cursor: "grab" }}
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
        />
      </div>
    );
  },
);
