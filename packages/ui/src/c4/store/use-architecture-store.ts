"use client";

import { create } from "zustand";
import { devtools } from "zustand/middleware";
import type { ReactFlowInstance } from "@xyflow/react";
import type {
  ArchitectureView,
  ArchNode,
  ArchEdge,
  ArchLayer,
  NavigationLevel,
  Persona,
  DetailLevel,
  SearchResult,
  ArchFilters,
  ContainerLayoutResult,
} from "../types";

interface ArchitectureStoreState {
  view: ArchitectureView | null;
  nodesById: Map<string, ArchNode>;
  edgesBySource: Map<string, ArchEdge[]>;
  edgesByTarget: Map<string, ArchEdge[]>;
  nodeIdToLayerId: Map<string, string>;
  nodeIdToSubGroupId: Map<string, string>;

  navigationLevel: NavigationLevel;
  activeLayerId: string | null;
  activeSubGroupId: string | null;
  selectedNodeId: string | null;

  expandedContainers: Set<string>;
  containerLayoutCache: Map<string, ContainerLayoutResult>;
  containerSizeMemory: Map<string, { width: number; height: number }>;
  stage1Tick: number;

  persona: Persona;
  detailLevel: DetailLevel;

  searchQuery: string;
  searchResults: SearchResult[];

  filters: ArchFilters;
  nodeTypeFilters: Record<string, boolean>;
  filterPanelOpen: boolean;
  /** Test layer is demoted by default (locked decision 2); this restores it. */
  showTests: boolean;

  tourActive: boolean;
  currentTourStep: number;
  tourHighlightedNodeIds: Set<string>;

  focusNodeId: string | null;

  diffMode: boolean;
  changedNodeIds: Set<string>;
  affectedNodeIds: Set<string>;

  codeViewerOpen: boolean;
  codeViewerNodeId: string | null;
  codeViewerExpanded: boolean;

  pathFinderOpen: boolean;
  reactFlowInstance: ReactFlowInstance | null;
}

interface ArchitectureStoreActions {
  setView: (view: ArchitectureView) => void;
  clearView: () => void;

  drillIntoLayer: (layerId: string) => void;
  drillIntoSubGroup: (subGroupId: string) => void;
  drillOut: () => void;

  selectNode: (nodeId: string | null) => void;

  toggleContainer: (containerId: string) => void;
  setContainerLayout: (containerId: string, layout: ContainerLayoutResult) => void;
  setContainerSizeEstimate: (containerId: string, size: { width: number; height: number }) => void;
  bumpStage1Tick: () => void;

  setPersona: (persona: Persona) => void;
  setDetailLevel: (level: DetailLevel) => void;

  setSearchQuery: (query: string) => void;
  setSearchResults: (results: SearchResult[]) => void;
  clearSearch: () => void;

  setNodeTypeFilter: (nodeType: string, visible: boolean) => void;
  setComplexityFilter: (complexity: string, visible: boolean) => void;
  setLayerFilter: (layerId: string, visible: boolean) => void;
  setEdgeCategoryFilter: (category: string, visible: boolean) => void;
  resetFilters: () => void;
  setFilterPanelOpen: (open: boolean) => void;
  setShowTests: (show: boolean) => void;

  startTour: () => void;
  endTour: () => void;
  nextTourStep: () => void;
  prevTourStep: () => void;
  goToTourStep: (step: number) => void;

  setFocusNode: (nodeId: string | null) => void;
  clearFocus: () => void;

  setDiffMode: (on: boolean) => void;
  setDiffData: (changed: Set<string>, affected: Set<string>) => void;

  openCodeViewer: (nodeId: string) => void;
  closeCodeViewer: () => void;
  toggleCodeViewerExpanded: () => void;

  setPathFinderOpen: (open: boolean) => void;
  setReactFlowInstance: (instance: ReactFlowInstance | null) => void;
}

export type ArchitectureStore = ArchitectureStoreState & ArchitectureStoreActions;

const DEFAULT_NODE_TYPE_FILTERS: Record<string, boolean> = {
  code: true,
  config: true,
  docs: true,
  infra: true,
  data: true,
};

function buildFiltersFromView(view: ArchitectureView): ArchFilters {
  const nodeTypes = new Set<string>();
  const complexities = new Set<string>();
  for (const node of view.nodes) {
    nodeTypes.add(node.node_type);
    complexities.add(node.complexity);
  }
  const layerIds = new Set(view.layers.map((l: ArchLayer) => l.id));
  const edgeCategories = new Set(view.edges.map((e: ArchEdge) => e.edge_type));
  return { nodeTypes, complexities, layerIds, edgeCategories };
}

function buildIndexes(view: ArchitectureView) {
  const nodesById = new Map<string, ArchNode>();
  const edgesBySource = new Map<string, ArchEdge[]>();
  const edgesByTarget = new Map<string, ArchEdge[]>();
  const nodeIdToLayerId = new Map<string, string>();
  const nodeIdToSubGroupId = new Map<string, string>();

  for (const node of view.nodes) {
    nodesById.set(node.id, node);
  }

  for (const edge of view.edges) {
    const srcList = edgesBySource.get(edge.source);
    if (srcList) {
      srcList.push(edge);
    } else {
      edgesBySource.set(edge.source, [edge]);
    }
    const tgtList = edgesByTarget.get(edge.target);
    if (tgtList) {
      tgtList.push(edge);
    } else {
      edgesByTarget.set(edge.target, [edge]);
    }
  }

  for (const layer of view.layers) {
    for (const nodeId of layer.node_ids) {
      nodeIdToLayerId.set(nodeId, layer.id);
    }
    for (const group of layer.sub_groups) {
      for (const nodeId of group.node_ids) {
        nodeIdToSubGroupId.set(nodeId, group.id);
      }
    }
  }

  return { nodesById, edgesBySource, edgesByTarget, nodeIdToLayerId, nodeIdToSubGroupId };
}

/** Tolerate older / uncurated backend payloads (kg-ux hardening): the API
 * response may predate the curated-KG schema (no sub_groups / entry_points /
 * tour — e.g. a server running pre-curation code), and JSON from the wire is
 * never as trustworthy as the TS types claim. Every field the viewer
 * iterates gets a safe default so a stale backend degrades to the uncurated
 * experience instead of crashing the page. */
function normalizeView(view: ArchitectureView): ArchitectureView {
  return {
    ...view,
    layers: (view.layers ?? []).map((l, i) => ({
      ...l,
      node_ids: l.node_ids ?? [],
      sub_groups: l.sub_groups ?? [],
      display_order: l.display_order ?? i,
      health_score: l.health_score ?? null,
      complexity_distribution: l.complexity_distribution ?? {},
    })),
    nodes: (view.nodes ?? []).map((n) => ({ ...n, tags: n.tags ?? [] })),
    edges: view.edges ?? [],
    tour: view.tour ?? [],
    languages: view.languages ?? [],
    frameworks: view.frameworks ?? [],
    external_systems: view.external_systems ?? [],
    entry_points: view.entry_points ?? [],
    entry_candidates: view.entry_candidates ?? [],
  };
}

/** Drill state that lands on *nodeId*'s file card: its layer, and — when the
 * layer is curated into sub-groups — the sub-group that contains it.
 * Returns {} when the node's layer is unknown or already active. */
function drillStateForNode(
  state: ArchitectureStoreState,
  nodeId: string | null,
): Partial<ArchitectureStoreState> {
  if (!nodeId) return {};
  const layerId = state.nodeIdToLayerId.get(nodeId) ?? null;
  if (!layerId) return {};
  const subGroupId = state.nodeIdToSubGroupId.get(nodeId) ?? null;
  if (
    state.navigationLevel === "layer-detail" &&
    layerId === state.activeLayerId &&
    subGroupId === state.activeSubGroupId
  ) {
    return {};
  }
  return {
    navigationLevel: "layer-detail" as NavigationLevel,
    activeLayerId: layerId,
    activeSubGroupId: subGroupId,
    expandedContainers: new Set<string>(),
    containerLayoutCache: new Map<string, ContainerLayoutResult>(),
  };
}

/** Drill state for a tour step: follow the step's node when it's in the
 * graph, else fall back to the step's curated layer_id so the canvas still
 * follows the walk (viewer plan C-2). */
function drillStateForStep(
  state: ArchitectureStoreState,
  nodeId: string | null,
  layerId: string | null | undefined,
): Partial<ArchitectureStoreState> {
  if (nodeId && state.nodeIdToLayerId.has(nodeId)) {
    return drillStateForNode(state, nodeId);
  }
  if (
    layerId &&
    layerId !== state.activeLayerId &&
    state.view?.layers.some((l: ArchLayer) => l.id === layerId)
  ) {
    return {
      navigationLevel: "layer-detail" as NavigationLevel,
      activeLayerId: layerId,
      activeSubGroupId: null,
      expandedContainers: new Set<string>(),
      containerLayoutCache: new Map<string, ContainerLayoutResult>(),
    };
  }
  return {};
}

const INITIAL_STATE: ArchitectureStoreState = {
  view: null,
  nodesById: new Map(),
  edgesBySource: new Map(),
  edgesByTarget: new Map(),
  nodeIdToLayerId: new Map(),
  nodeIdToSubGroupId: new Map(),

  navigationLevel: "overview",
  activeLayerId: null,
  activeSubGroupId: null,
  selectedNodeId: null,

  expandedContainers: new Set(),
  containerLayoutCache: new Map(),
  containerSizeMemory: new Map(),
  stage1Tick: 0,

  persona: "overview",
  detailLevel: "file",

  searchQuery: "",
  searchResults: [],

  filters: {
    nodeTypes: new Set(),
    complexities: new Set(),
    layerIds: new Set(),
    edgeCategories: new Set(),
  },
  nodeTypeFilters: { ...DEFAULT_NODE_TYPE_FILTERS },
  filterPanelOpen: false,
  showTests: false,

  tourActive: false,
  currentTourStep: 0,
  tourHighlightedNodeIds: new Set(),

  focusNodeId: null,

  diffMode: false,
  changedNodeIds: new Set(),
  affectedNodeIds: new Set(),

  codeViewerOpen: false,
  codeViewerNodeId: null,
  codeViewerExpanded: false,

  pathFinderOpen: false,
  reactFlowInstance: null,
};

export const useArchitectureStore = create<ArchitectureStore>()(
  devtools(
    (set, get) => ({
      ...INITIAL_STATE,

      setView: (rawView: ArchitectureView) => {
        const view = normalizeView(rawView);
        const { nodesById, edgesBySource, edgesByTarget, nodeIdToLayerId, nodeIdToSubGroupId } =
          buildIndexes(view);
        const filters = buildFiltersFromView(view);
        set({
          view,
          nodesById,
          edgesBySource,
          edgesByTarget,
          nodeIdToLayerId,
          nodeIdToSubGroupId,
          filters,
          navigationLevel: "overview",
          activeLayerId: null,
          activeSubGroupId: null,
          selectedNodeId: null,
          expandedContainers: new Set(),
          containerLayoutCache: new Map(),
          searchQuery: "",
          searchResults: [],
          tourActive: false,
          currentTourStep: 0,
          tourHighlightedNodeIds: new Set(),
          nodeTypeFilters: { ...DEFAULT_NODE_TYPE_FILTERS },
        });
      },

      clearView: () => {
        set({ ...INITIAL_STATE });
      },

      drillIntoLayer: (layerId: string) => {
        // Layers curated into sub-groups land on the intermediate
        // "layer-groups" tier; small/uncurated layers drill straight to
        // file cards (locked decision 1 — never synthesize a one-group tier).
        const layer = get().view?.layers.find((l: ArchLayer) => l.id === layerId);
        const hasSubGroups = (layer?.sub_groups.length ?? 0) > 0;
        set({
          navigationLevel: hasSubGroups ? "layer-groups" : "layer-detail",
          activeLayerId: layerId,
          activeSubGroupId: null,
          selectedNodeId: null,
          focusNodeId: null,
          expandedContainers: new Set(),
          containerLayoutCache: new Map(),
        });
      },

      drillIntoSubGroup: (subGroupId: string) => {
        set({
          navigationLevel: "layer-detail",
          activeSubGroupId: subGroupId,
          selectedNodeId: null,
          focusNodeId: null,
          expandedContainers: new Set(),
          containerLayoutCache: new Map(),
        });
      },

      drillOut: () => {
        const state = get();
        // layer-detail inside a sub-group steps back up to the groups tier;
        // everything else returns to the overview.
        if (state.navigationLevel === "layer-detail" && state.activeSubGroupId) {
          set({
            navigationLevel: "layer-groups",
            activeSubGroupId: null,
            selectedNodeId: null,
            focusNodeId: null,
            expandedContainers: new Set(),
            containerLayoutCache: new Map(),
          });
          return;
        }
        set({
          navigationLevel: "overview",
          activeLayerId: null,
          activeSubGroupId: null,
          selectedNodeId: null,
          focusNodeId: null,
          expandedContainers: new Set(),
          containerLayoutCache: new Map(),
        });
      },

      selectNode: (nodeId: string | null) => {
        const state = get();
        set({
          ...drillStateForNode(state, nodeId),
          selectedNodeId: nodeId,
          focusNodeId: null,
        });
      },

      toggleContainer: (containerId: string) => {
        const state = get();
        const expanded = new Set(state.expandedContainers);
        if (expanded.has(containerId)) {
          expanded.delete(containerId);
          const cache = new Map(state.containerLayoutCache);
          cache.delete(containerId);
          set({ expandedContainers: expanded, containerLayoutCache: cache });
        } else {
          expanded.add(containerId);
          set({ expandedContainers: expanded });
        }
      },

      setContainerLayout: (containerId: string, layout: ContainerLayoutResult) => {
        const cache = new Map(get().containerLayoutCache);
        cache.set(containerId, layout);
        set({ containerLayoutCache: cache });
      },

      setContainerSizeEstimate: (containerId: string, size: { width: number; height: number }) => {
        const memory = new Map(get().containerSizeMemory);
        memory.set(containerId, size);
        set({ containerSizeMemory: memory });
      },

      bumpStage1Tick: () => {
        set({ stage1Tick: get().stage1Tick + 1 });
      },

      setPersona: (persona: Persona) => {
        set({
          persona,
          expandedContainers: new Set(),
          containerLayoutCache: new Map(),
        });
      },

      setDetailLevel: (level: DetailLevel) => {
        set({
          detailLevel: level,
          expandedContainers: new Set(),
          containerLayoutCache: new Map(),
        });
      },

      setSearchQuery: (query: string) => {
        set({ searchQuery: query });
      },

      setSearchResults: (results: SearchResult[]) => {
        set({ searchResults: results });
      },

      clearSearch: () => {
        set({ searchQuery: "", searchResults: [] });
      },

      setNodeTypeFilter: (nodeType: string, visible: boolean) => {
        const state = get();
        const nodeTypes = new Set(state.filters.nodeTypes);
        if (visible) {
          nodeTypes.add(nodeType);
        } else {
          nodeTypes.delete(nodeType);
        }
        set({
          filters: { ...state.filters, nodeTypes },
          containerLayoutCache: new Map(),
        });
      },

      setComplexityFilter: (complexity: string, visible: boolean) => {
        const state = get();
        const complexities = new Set(state.filters.complexities);
        if (visible) {
          complexities.add(complexity);
        } else {
          complexities.delete(complexity);
        }
        set({
          filters: { ...state.filters, complexities },
          containerLayoutCache: new Map(),
        });
      },

      setLayerFilter: (layerId: string, visible: boolean) => {
        const state = get();
        const layerIds = new Set(state.filters.layerIds);
        if (visible) {
          layerIds.add(layerId);
        } else {
          layerIds.delete(layerId);
        }
        set({
          filters: { ...state.filters, layerIds },
          containerLayoutCache: new Map(),
        });
      },

      setEdgeCategoryFilter: (category: string, visible: boolean) => {
        const state = get();
        const edgeCategories = new Set(state.filters.edgeCategories);
        if (visible) {
          edgeCategories.add(category);
        } else {
          edgeCategories.delete(category);
        }
        set({
          filters: { ...state.filters, edgeCategories },
          containerLayoutCache: new Map(),
        });
      },

      resetFilters: () => {
        const state = get();
        if (!state.view) return;
        const filters = buildFiltersFromView(state.view);
        set({
          filters,
          nodeTypeFilters: { ...DEFAULT_NODE_TYPE_FILTERS },
          containerLayoutCache: new Map(),
        });
      },

      setFilterPanelOpen: (open: boolean) => {
        set({ filterPanelOpen: open });
      },

      setShowTests: (show: boolean) => {
        set({ showTests: show });
      },

      startTour: () => {
        const state = get();
        const tour = state.view?.tour;
        if (!tour || tour.length === 0) return;
        const firstStep = tour[0];
        const nodeIds = firstStep?.node_ids ?? [];
        const firstNodeId = nodeIds[0] ?? null;
        set({
          tourActive: true,
          currentTourStep: 0,
          tourHighlightedNodeIds: new Set(nodeIds),
          selectedNodeId: firstNodeId,
          focusNodeId: null,
          ...drillStateForStep(state, firstNodeId, firstStep?.layer_id),
        });
      },

      endTour: () => {
        set({
          tourActive: false,
          currentTourStep: 0,
          tourHighlightedNodeIds: new Set(),
        });
      },

      nextTourStep: () => {
        const state = get();
        const tour = state.view?.tour;
        if (!tour || tour.length === 0) return;
        const maxStep = tour.length - 1;
        if (state.currentTourStep >= maxStep) return;
        get().goToTourStep(state.currentTourStep + 1);
      },

      prevTourStep: () => {
        const state = get();
        const tour = state.view?.tour;
        if (!tour || tour.length === 0) return;
        if (state.currentTourStep <= 0) return;
        get().goToTourStep(state.currentTourStep - 1);
      },

      goToTourStep: (step: number) => {
        const state = get();
        const tour = state.view?.tour;
        if (!tour || tour.length === 0) return;
        const clamped = Math.max(0, Math.min(step, tour.length - 1));
        const tourStep = tour[clamped];
        const nodeIds = tourStep?.node_ids ?? [];
        const firstNodeId = nodeIds[0] ?? null;
        set({
          currentTourStep: clamped,
          tourHighlightedNodeIds: new Set(nodeIds),
          selectedNodeId: firstNodeId,
          focusNodeId: null,
          ...drillStateForStep(state, firstNodeId, tourStep?.layer_id),
        });
      },

      setFocusNode: (nodeId: string | null) => {
        set({ focusNodeId: nodeId });
      },

      clearFocus: () => {
        set({ focusNodeId: null });
      },

      setDiffMode: (on: boolean) => {
        set({ diffMode: on });
      },

      setDiffData: (changed: Set<string>, affected: Set<string>) => {
        set({ changedNodeIds: changed, affectedNodeIds: affected });
      },

      openCodeViewer: (nodeId: string) => {
        set({ codeViewerOpen: true, codeViewerNodeId: nodeId });
      },

      closeCodeViewer: () => {
        set({ codeViewerOpen: false, codeViewerNodeId: null, codeViewerExpanded: false });
      },

      toggleCodeViewerExpanded: () => {
        set({ codeViewerExpanded: !get().codeViewerExpanded });
      },

      setPathFinderOpen: (open: boolean) => {
        set({ pathFinderOpen: open });
      },

      setReactFlowInstance: (instance: ReactFlowInstance | null) => {
        set({ reactFlowInstance: instance });
      },
    }),
    { name: "architecture-store" },
  ),
);
