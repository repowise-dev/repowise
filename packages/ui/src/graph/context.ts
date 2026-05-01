"use client";

import { createContext, useContext } from "react";
import type { ColorMode } from "./graph-toolbar";

export interface GraphContextValue {
  highlightedPath: Set<string>;
  highlightedEdges: Set<string>;
  colorMode: ColorMode;
  riskScores: Map<string, number>;
  hoveredNodeId: string | null;
  connectedNodeIds: Set<string>;
  connectedEdgeIds: Set<string>;
  selectedNodeId: string | null;
  searchDimmedNodes: Set<string> | null;
}

const defaultValue: GraphContextValue = {
  highlightedPath: new Set(),
  highlightedEdges: new Set(),
  colorMode: "language",
  riskScores: new Map(),
  hoveredNodeId: null,
  connectedNodeIds: new Set(),
  connectedEdgeIds: new Set(),
  selectedNodeId: null,
  searchDimmedNodes: null,
};

export const GraphContext = createContext<GraphContextValue>(defaultValue);

export const GraphProvider = GraphContext.Provider;

export function useGraphContext(): GraphContextValue {
  return useContext(GraphContext);
}
