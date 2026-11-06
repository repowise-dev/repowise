"use client";

/**
 * The drawing plane: React Flow over the drawn cards and connectors, with
 * pointer and keyboard input reported as one `onActivate` call so a click,
 * a double-click and Enter on a focused card reach the same handler. Cards
 * and connectors are in the tab order; Escape asks the map to back out a
 * level. Fits the drawing whenever the level's layout changes or the canvas
 * resizes, without animating when the reader prefers reduced motion.
 */

import { useCallback, useEffect, useMemo, type FocusEvent, type KeyboardEvent, type ReactNode } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  useReactFlow,
  useStore,
  type Edge,
  type EdgeMouseHandler,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { ActivateTrigger } from "./activate";
import { SystemMapBand } from "./system-map-band";
import { SystemMapColumnLabel } from "./system-map-column-label";
import { SystemMapEdge } from "./system-map-edge";
import type { SystemMapFocusStore } from "./system-map-focus";
import { SystemMapGroupNode } from "./system-map-group-node";
import { SystemMapNode } from "./system-map-node";
import { SystemMapMoreNode, SystemMapTrayNode } from "./system-map-tray-node";

/** Below this many cards the whole map fits on screen and a minimap is noise. */
export const SYSTEM_MAP_MINIMAP_MIN_NODES = 40;

const NODE_TYPES = {
  systemService: SystemMapNode,
  systemGroup: SystemMapGroupNode,
  systemTray: SystemMapTrayNode,
  systemMore: SystemMapMoreNode,
  systemColumnLabel: SystemMapColumnLabel,
};
const EDGE_TYPES = { systemEdge: SystemMapEdge, systemBand: SystemMapBand };

const NODE_HELP = "Press Space for details. Press Enter to open a group or focus a service. Escape goes back a level.";

const FIT_OPTIONS = { padding: 0.08, maxZoom: 1.2 } as const;


export interface SystemMapCanvasProps {
  nodes: Node[];
  edges: Edge[];
  focus: SystemMapFocusStore;
  /** Changes when the drawing should be refitted (a new level). */
  fitKey: string;
  /** False while the level's layout is still running; the fit waits for it. */
  settled: boolean;
  onActivate: (id: string, kind: "node" | "edge", trigger: ActivateTrigger) => void;
  onPaneClick: () => void;
  onEscape: () => void;
  /** Drawn over the plane when there is nothing to draw (empty or error state). */
  empty?: ReactNode;
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

export function SystemMapCanvas({ nodes, edges, focus, fitKey, settled, onActivate, onPaneClick, onEscape, empty }: SystemMapCanvasProps) {
  const onNodeClick = useCallback<NodeMouseHandler>((_, n) => onActivate(n.id, "node", "click"), [onActivate]);
  const onNodeDoubleClick = useCallback<NodeMouseHandler>((_, n) => onActivate(n.id, "node", "double"), [onActivate]);
  const onEdgeClick = useCallback<EdgeMouseHandler>((_, e) => onActivate(e.id, "edge", "click"), [onActivate]);
  const onNodeMouseEnter = useCallback<NodeMouseHandler>((_, n) => focus.setHover({ node: n.id }), [focus]);
  const onNodeMouseLeave = useCallback<NodeMouseHandler>(() => focus.setHover({ node: null }), [focus]);
  const onEdgeMouseEnter = useCallback<EdgeMouseHandler>((_, e) => focus.setHover({ edge: e.id }), [focus]);
  const onEdgeMouseLeave = useCallback<EdgeMouseHandler>(() => focus.setHover({ edge: null }), [focus]);

  // Keyboard: React Flow's own selection is off, so Enter and Space on a
  // focused card or connector land here and do what a click (or, on an
  // aggregate, a double-click) does.
  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onEscape();
        return;
      }
      if (e.key !== "Enter" && e.key !== " ") return;
      const target = e.target as HTMLElement;
      const el = target.closest?.(".react-flow__node, .react-flow__edge");
      const id = el?.getAttribute("data-id");
      // Only the focused card or connector itself; a control inside a card keeps its own key handling.
      if (!el || !id || el !== target) return;
      e.preventDefault();
      // Space selects, as a click does; Enter opens a group or focuses a service, as a double-click does.
      onActivate(id, el.classList.contains("react-flow__node") ? "node" : "edge", e.key === " " ? "click" : "key");
    },
    [onActivate, onEscape],
  );
  // A focused connector shows what hovering it shows.
  const onFocusCapture = useCallback(
    (e: FocusEvent<HTMLDivElement>) => {
      const el = (e.target as HTMLElement).closest?.(".react-flow__edge");
      focus.setHover({ edge: el?.getAttribute("data-id") ?? null });
    },
    [focus],
  );
  const onBlurCapture = useCallback(() => focus.setHover({ edge: null }), [focus]);

  const ariaLabelConfig = useMemo(
    () => ({
      "node.a11yDescription.default": NODE_HELP,
      "node.a11yDescription.keyboardDisabled": NODE_HELP,
      "edge.a11yDescription.default": "Press Enter to show this line's or band's details. Escape goes back a level.",
    }),
    [],
  );

  return (
    <div className="h-full w-full" onKeyDown={onKeyDown} onFocusCapture={onFocusCapture} onBlurCapture={onBlurCapture}>
      {empty}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onNodeClick={onNodeClick}
        onNodeDoubleClick={onNodeDoubleClick}
        onEdgeClick={onEdgeClick}
        onPaneClick={onPaneClick}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseLeave={onNodeMouseLeave}
        onEdgeMouseEnter={onEdgeMouseEnter}
        onEdgeMouseLeave={onEdgeMouseLeave}
        elementsSelectable={false}
        nodesDraggable={false}
        nodesConnectable={false}
        zoomOnDoubleClick={false}
        fitView
        fitViewOptions={FIT_OPTIONS}
        minZoom={0.15}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        ariaLabelConfig={ariaLabelConfig}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="var(--color-border-hover)" />
        <Controls showInteractive={false} style={{ marginBottom: "var(--canvas-chrome-bottom, 15px)" }} />
        {nodes.length >= SYSTEM_MAP_MINIMAP_MIN_NODES && (
          <MiniMap
            pannable
            zoomable
            bgColor="var(--color-bg-surface)"
            maskColor="color-mix(in srgb, var(--color-bg-root) 70%, transparent)"
            nodeColor="var(--color-border-hover)"
            nodeStrokeColor="var(--color-text-tertiary)"
            style={{ border: "1px solid var(--color-border-default)", marginBottom: "var(--canvas-chrome-bottom, 15px)" }}
          />
        )}
        <FitOnChange fitKey={fitKey} ready={settled && nodes.length > 0} />
      </ReactFlow>
    </div>
  );
}

/**
 * Fits once a level's layout lands and when the canvas resizes. `fitView` on
 * mount alone would fit an empty field, because the async layout places
 * cards after React Flow's first pass.
 */
function FitOnChange({ fitKey, ready }: { fitKey: string; ready: boolean }) {
  const rf = useReactFlow();
  const width = useStore((s) => s.width);
  const height = useStore((s) => s.height);
  useEffect(() => {
    if (!ready || width === 0 || height === 0) return;
    const id = requestAnimationFrame(() => {
      void rf.fitView({ ...FIT_OPTIONS, duration: prefersReducedMotion() ? 0 : 200 });
    });
    return () => cancelAnimationFrame(id);
  }, [rf, ready, fitKey, width, height]);
  return null;
}
