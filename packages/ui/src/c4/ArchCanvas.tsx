"use client";

import * as React from "react";
import {
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AlertTriangle } from "lucide-react";
import { archNodeTypes } from "./nodes";
import { archEdgeTypes } from "../graph-primitives";
import { ArchLegend } from "./panels";
import { KEYFRAMES } from "./theme/theme-variables";
import { EmptyState } from "../shared/empty-state";
import { OwlLoader } from "../shared/owl-loader";

export interface ArchCanvasProps {
  nodes: Node[];
  edges: Edge[];
  loading?: boolean;
  error?: { message: string } | null;
  /** Weak aggregated edges hidden to keep the diagram legible. */
  hiddenEdgeCount?: number;
  onInit?: ((instance: ReactFlowInstance) => void) | undefined;
  onNodeClick?: NodeMouseHandler<Node> | undefined;
  onNodeDoubleClick?: NodeMouseHandler<Node> | undefined;
  onPaneClick?: (() => void) | undefined;
  /** Empty-state copy for the canvas (kept generic so the term lives on the
   *  hosting route, not the diagram). */
  errorTitle?: string;
  loadingLabel?: string;
  /** Web-owned overlays (detail panel host, filter panel, code viewer,
   *  path-finder modal) layered above the canvas. */
  children?: React.ReactNode;
}

/**
 * The layered-architecture ReactFlow canvas. Owns the diagram chrome that used
 * to be stranded in the web route (Controls, MiniMap, the "laying out" owl chip,
 * the "+N weaker links" chip, and the ArchLegend placement) so the layered view
 * upgrades via a package bump. Deliberately airy: no grid background and no
 * canvas fill behind the diagram.
 */
export function ArchCanvas({
  nodes,
  edges,
  loading = false,
  error = null,
  hiddenEdgeCount = 0,
  onInit,
  onNodeClick,
  onNodeDoubleClick,
  onPaneClick,
  errorTitle = "Couldn't load the architecture layers",
  loadingLabel = "Loading layers…",
  children,
}: ArchCanvasProps) {
  return (
    <>
      <style>{KEYFRAMES.accentPulse}{KEYFRAMES.edgeFlow}{`
        /* Zoom-into-tier feel: nodes glide to their next slot. */
        @media (prefers-reduced-motion: no-preference) {
          .react-flow__node { transition: transform 180ms ease; }
        }
      `}</style>
      <div className="relative h-full min-h-0 w-full">
        {error && (
          <div className="absolute inset-0 z-10 flex items-center justify-center">
            <EmptyState
              icon={<AlertTriangle className="h-5 w-5" aria-hidden />}
              title={errorTitle}
              description={error.message}
              className="max-w-md p-8"
            />
          </div>
        )}
        {loading && nodes.length === 0 && !error && (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center">
            <OwlLoader size={120} label={loadingLabel} className="min-h-0" />
          </div>
        )}
        {/* Re-layout feedback: ELK stage-2 on big layers used to freeze silently
            — a small owl chip says the canvas is still thinking. */}
        {loading && nodes.length > 0 && !error && (
          <div className="pointer-events-none absolute left-1/2 top-3 z-10 -translate-x-1/2 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)]/95 px-3 py-1 shadow-sm">
            <OwlLoader
              size={28}
              label="Laying out…"
              className="min-h-0 flex-row gap-2 text-[10px]"
            />
          </div>
        )}

        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={archNodeTypes}
          edgeTypes={archEdgeTypes}
          {...(onNodeClick ? { onNodeClick } : {})}
          {...(onNodeDoubleClick ? { onNodeDoubleClick } : {})}
          {...(onPaneClick ? { onPaneClick } : {})}
          {...(onInit ? { onInit } : {})}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.1}
          maxZoom={3}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
        >
          {/* No grid background and no canvas fill — the diagram breathes on the
              page background (the Lines grid was removed per the airy directive). */}
          <Controls showInteractive={false} />
          {/* maskColor comes from --xy-minimap-mask-background (theme-aware). */}
          <MiniMap pannable zoomable />
        </ReactFlow>

        {hiddenEdgeCount > 0 && (
          <div
            className="absolute bottom-4 left-14 z-10 rounded-full border border-[var(--color-border-default)] bg-[var(--color-bg-secondary)]/90 px-3 py-1 text-xs text-[var(--color-text-secondary)]"
            title="Weakest aggregated connections are hidden to keep the view legible. Drill in to see them."
          >
            +{hiddenEdgeCount} weaker link{hiddenEdgeCount === 1 ? "" : "s"} hidden
          </div>
        )}

        {/* Decoder ring — collapsible, every tier. */}
        <div className="absolute bottom-4 right-[224px] z-10">
          <ArchLegend />
        </div>

        {children}
      </div>
    </>
  );
}
