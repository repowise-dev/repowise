"use client";

import { createContext, useContext } from "react";
import type { ColorMode, ViewMode, LayoutMode, GraphTheme } from "./graph-toolbar";

export interface GraphContextValue {
  highlightedPath: Set<string>;
  highlightedEdges: Set<string>;
  colorMode: ColorMode;
  viewMode: ViewMode;
  riskScores: Map<string, number>;
  hoveredNodeId: string | null;
  connectedNodeIds: Set<string>;
  connectedEdgeIds: Set<string>;
  selectedNodeId: string | null;
  searchDimmedNodes: Set<string> | null;
  communityDimmedNodes: Set<string> | null;
  layoutMode: LayoutMode;
  graphTheme: GraphTheme;
  maxPagerank: number;
  medianPagerank: number;
}

const defaultValue: GraphContextValue = {
  highlightedPath: new Set(),
  highlightedEdges: new Set(),
  colorMode: "language",
  viewMode: "module",
  riskScores: new Map(),
  hoveredNodeId: null,
  connectedNodeIds: new Set(),
  connectedEdgeIds: new Set(),
  selectedNodeId: null,
  searchDimmedNodes: null,
  communityDimmedNodes: null,
  layoutMode: "hierarchical",
  graphTheme: "light",
  maxPagerank: 0,
  medianPagerank: 0,
};

export const GraphContext = createContext<GraphContextValue>(defaultValue);

export const GraphProvider = GraphContext.Provider;

export function useGraphContext(): GraphContextValue {
  return useContext(GraphContext);
}
