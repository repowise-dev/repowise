"use client";

import { useEffect, useState } from "react";
import { MarkerType, type Node, type Edge } from "@xyflow/react";
import { useArchitectureStore } from "../store/use-architecture-store";
import type { ArchitectureView, ArchNode, ArchEdge, ArchLayer } from "../types";
import { PERSONA_NODE_TYPES } from "../types";
import { buildContainers, getStandaloneNodeIds } from "../layout/containers";
import { aggregateEdges } from "../layout/edge-aggregation";
import {
  computeStage1Layout,
  computeStage2Layout,
  estimateContainerSize,
  ARCH_NODE_SIZES,
  type ContainerAtom,
  type PortalSpec,
} from "../layout/two-stage-layout";
import { THEME } from "../theme/theme-variables";

export interface ArchitectureLayoutResult {
  nodes: Node[];
  edges: Edge[];
  loading: boolean;
  issues: string[];
}

export function useArchitectureLayout(): ArchitectureLayoutResult {
  const view = useArchitectureStore((s) => s.view);
  const navigationLevel = useArchitectureStore((s) => s.navigationLevel);
  const activeLayerId = useArchitectureStore((s) => s.activeLayerId);
  const detailLevel = useArchitectureStore((s) => s.detailLevel);
  const persona = useArchitectureStore((s) => s.persona);
  const filters = useArchitectureStore((s) => s.filters);
  const expandedContainers = useArchitectureStore((s) => s.expandedContainers);
  const expandedContainersVersion = useArchitectureStore((s) => s.expandedContainers.size);
  const stage1Tick = useArchitectureStore((s) => s.stage1Tick);
  const focusNodeId = useArchitectureStore((s) => s.focusNodeId);
  const selectedNodeId = useArchitectureStore((s) => s.selectedNodeId);
  const tourHighlightedNodeIds = useArchitectureStore((s) => s.tourHighlightedNodeIds);
  const searchResults = useArchitectureStore((s) => s.searchResults);
  const diffMode = useArchitectureStore((s) => s.diffMode);
  const changedNodeIds = useArchitectureStore((s) => s.changedNodeIds);
  const affectedNodeIds = useArchitectureStore((s) => s.affectedNodeIds);

  const [result, setResult] = useState<ArchitectureLayoutResult>({
    nodes: [],
    edges: [],
    loading: false,
    issues: [],
  });

  useEffect(() => {
    if (!view) {
      setResult({ nodes: [], edges: [], loading: false, issues: [] });
      return;
    }

    let cancelled = false;
    setResult((r) => ({ ...r, loading: true }));

    const run = async () => {
      if (navigationLevel === "overview") {
        return computeOverviewLayout(view);
      }
      return computeDetailLayout(view);
    };

    void run().then((layout) => {
      if (cancelled) return;
      setResult(layout);
    });

    return () => {
      cancelled = true;
    };
  }, [
    view,
    navigationLevel,
    activeLayerId,
    detailLevel,
    persona,
    filters,
    expandedContainers,
    expandedContainersVersion,
    stage1Tick,
    focusNodeId,
    selectedNodeId,
    tourHighlightedNodeIds,
    searchResults,
    diffMode,
    changedNodeIds,
    affectedNodeIds,
  ]);

  async function computeOverviewLayout(currentView: ArchitectureView): Promise<ArchitectureLayoutResult> {
    const MAX_OVERVIEW_LAYERS = 12;
    let displayLayers = currentView.layers;

    if (displayLayers.length > MAX_OVERVIEW_LAYERS) {
      const sorted = [...displayLayers].sort((a, b) => b.file_count - a.file_count);
      const kept = sorted.slice(0, MAX_OVERVIEW_LAYERS - 1);
      const merged = sorted.slice(MAX_OVERVIEW_LAYERS - 1);

      const otherLayer: ArchLayer = {
        id: "layer:other",
        name: "Other",
        description: `${merged.length} smaller layers`,
        node_ids: merged.flatMap((l) => l.node_ids),
        file_count: merged.reduce((s, l) => s + l.file_count, 0),
        complexity_distribution: merged.reduce<Record<string, number>>((acc, l) => {
          for (const [k, v] of Object.entries(l.complexity_distribution)) {
            acc[k] = (acc[k] ?? 0) + v;
          }
          return acc;
        }, {}),
        health_score: null,
        sub_groups: [],
        display_order: Math.max(...merged.map((l) => l.display_order)),
      };
      displayLayers = [...kept, otherLayer];
    }

    const nodeToLayer = new Map<string, string>();
    for (const layer of displayLayers) {
      for (const nodeId of layer.node_ids) {
        nodeToLayer.set(nodeId, layer.id);
      }
    }

    const aggregated = aggregateEdges(currentView.edges, nodeToLayer);
    const layerNodes = displayLayers.map((layer: ArchLayer) => ({
      id: layer.id,
      width: ARCH_NODE_SIZES.layerCluster.width,
      height: ARCH_NODE_SIZES.layerCluster.height,
    }));

    const layoutEdges = aggregated.map((agg) => ({
      id: agg.id,
      source: agg.source,
      target: agg.target,
    }));

    const { positions, issues } = await computeStage1Layout(
      [],
      layerNodes,
      [],
      layoutEdges,
      new Map(),
    );

    const searchHighlightIds = new Set<string>(searchResults.map((r) => r.nodeId));
    const nodes: Node[] = displayLayers.map((layer: ArchLayer) => {
      const pos = positions.get(layer.id) ?? { x: 0, y: 0, width: 0, height: 0 };
      const hasSearchHit = layer.node_ids.some((nid: string) => searchHighlightIds.has(nid));
      return {
        id: layer.id,
        type: "layerCluster",
        position: { x: pos.x, y: pos.y },
        data: {
          layer,
          searchHighlight: hasSearchHit,
        },
        width: ARCH_NODE_SIZES.layerCluster.width,
        height: ARCH_NODE_SIZES.layerCluster.height,
      };
    });

    const edges: Edge[] = aggregated.map((agg) => ({
      id: agg.id,
      source: agg.source,
      target: agg.target,
      type: "arch",
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 14,
        height: 14,
        color: THEME.edge[agg.dominantType] ?? "#8b9dc3",
      },
      data: {
        edge_type: agg.dominantType,
        count: agg.count,
        category: agg.dominantType,
      },
    }));

    return { nodes, edges, loading: false, issues };
  }

  async function computeDetailLayout(currentView: ArchitectureView): Promise<ArchitectureLayoutResult> {
    const containerLayoutCache = useArchitectureStore.getState().containerLayoutCache;
    const containerSizeMemory = useArchitectureStore.getState().containerSizeMemory;
    const layer = currentView.layers.find((l: ArchLayer) => l.id === activeLayerId);
    if (!layer) {
      return { nodes: [], edges: [], loading: false, issues: [`Layer ${activeLayerId} not found`] };
    }

    const layerNodeIds = new Set(layer.node_ids);
    let layerNodes = currentView.nodes.filter((n: ArchNode) => layerNodeIds.has(n.id));

    layerNodes = filterByPersona(layerNodes);
    layerNodes = filterByDetailLevel(layerNodes);
    layerNodes = applyUserFilters(layerNodes);

    if (focusNodeId) {
      layerNodes = applyFocusMode(layerNodes, currentView.edges);
    }

    const visibleNodeIds = new Set(layerNodes.map((n: ArchNode) => n.id));
    const nodesById = new Map(currentView.nodes.map((n: ArchNode) => [n.id, n]));

    const containers = buildContainers(layerNodes, currentView.edges, "auto");
    const standaloneIds = getStandaloneNodeIds(layerNodes, containers);

    const nodeIdToLayerId = useArchitectureStore.getState().nodeIdToLayerId;
    const portalSpecs = buildPortalSpecs(currentView.edges, visibleNodeIds, layer, currentView.layers, nodeIdToLayerId);

    const nodeToBox = new Map<string, string>();
    for (const container of containers) {
      for (const id of container.childNodeIds) {
        nodeToBox.set(id, container.id);
      }
    }
    for (const id of standaloneIds) {
      nodeToBox.set(id, id);
    }

    const aggregated = aggregateEdges(currentView.edges, nodeToBox);

    const standaloneLayoutNodes = standaloneIds.map((id) => {
      const node = nodesById.get(id);
      const nodeType = node?.node_type ?? "file";
      const sizes = ARCH_NODE_SIZES[nodeType as keyof typeof ARCH_NODE_SIZES] ?? ARCH_NODE_SIZES.file;
      return { id, width: sizes.width, height: sizes.height };
    });

    const stage1Edges = aggregated.map((agg) => ({
      id: agg.id,
      source: agg.source,
      target: agg.target,
    }));

    const { positions: stage1Positions, issues } = await computeStage1Layout(
      containers,
      standaloneLayoutNodes,
      portalSpecs,
      stage1Edges,
      containerSizeMemory,
    );

    const allNodes: Node[] = [];
    const allEdges: Edge[] = [];
    const searchHighlightIds = new Set<string>(searchResults.map((r) => r.nodeId));
    const store = useArchitectureStore.getState();

    const selectedConnectedNodes = new Set<string>();
    if (selectedNodeId) {
      selectedConnectedNodes.add(selectedNodeId);
      for (const e of currentView.edges) {
        if (e.source === selectedNodeId) selectedConnectedNodes.add(e.target);
        if (e.target === selectedNodeId) selectedConnectedNodes.add(e.source);
      }
    }

    for (const container of containers) {
      const pos = stage1Positions.get(container.id) ?? { x: 0, y: 0, width: 0, height: 0 };
      const isExpanded = expandedContainers.has(container.id);
      const searchHitCount = container.childNodeIds.filter((id) => searchHighlightIds.has(id)).length;

      allNodes.push({
        id: container.id,
        type: "archContainer",
        position: { x: pos.x, y: pos.y },
        data: {
          containerId: container.id,
          label: container.label,
          childCount: container.childNodeIds.length,
          expanded: isExpanded,
          searchHitCount,
        },
        width: pos.width,
        height: pos.height,
      });

      if (isExpanded) {
        const cached = containerLayoutCache.get(container.id);
        if (cached) {
          addChildNodes(allNodes, container, cached.positions, pos, nodesById, searchHighlightIds, selectedConnectedNodes);
        } else {
          const childLayoutNodes = container.childNodeIds.map((id) => {
            const node = nodesById.get(id);
            const nodeType = node?.node_type ?? "file";
            const sizes = ARCH_NODE_SIZES[nodeType as keyof typeof ARCH_NODE_SIZES] ?? ARCH_NODE_SIZES.file;
            return { id, width: sizes.width, height: sizes.height };
          });

          const containerNodeIds = new Set(container.childNodeIds);
          const internalEdges = currentView.edges
            .filter((e: ArchEdge) => containerNodeIds.has(e.source) && containerNodeIds.has(e.target))
            .map((e: ArchEdge, i: number) => ({
              id: `ie:${container.id}:${i}`,
              source: e.source,
              target: e.target,
            }));

          const stage2 = await computeStage2Layout(childLayoutNodes, internalEdges);
          const layoutResult = {
            positions: stage2.positions,
            size: stage2.actualSize,
          };
          store.setContainerLayout(container.id, layoutResult);

          const estimated = containerSizeMemory.get(container.id) ?? estimateContainerSize(container.childNodeIds.length);
          const deviation = Math.abs(stage2.actualSize.width - estimated.width) / Math.max(estimated.width, 1);
          if (deviation > 0.2) {
            store.setContainerSizeEstimate(container.id, stage2.actualSize);
            store.bumpStage1Tick();
          }

          addChildNodes(allNodes, container, stage2.positions, pos, nodesById, searchHighlightIds, selectedConnectedNodes);
        }
      }
    }

    for (const id of standaloneIds) {
      const node = nodesById.get(id);
      if (!node) continue;
      const pos = stage1Positions.get(id) ?? { x: 0, y: 0, width: 0, height: 0 };
      const sizes = ARCH_NODE_SIZES[node.node_type as keyof typeof ARCH_NODE_SIZES] ?? ARCH_NODE_SIZES.file;
      allNodes.push({
        id: node.id,
        type: "archFile",
        position: { x: pos.x, y: pos.y },
        data: buildArchFileData(node, searchHighlightIds, selectedConnectedNodes),
        width: sizes.width,
        height: sizes.height,
      });
    }

    for (const portal of portalSpecs) {
      const pos = stage1Positions.get(portal.id) ?? { x: 0, y: 0, width: 0, height: 0 };
      allNodes.push({
        id: portal.id,
        type: "portal",
        position: { x: pos.x, y: pos.y },
        data: {
          targetLayerId: portal.targetLayerId,
          targetLayerName: portal.targetLayerName,
          edgeCount: portal.edgeCount,
        },
        width: ARCH_NODE_SIZES.portal.width,
        height: ARCH_NODE_SIZES.portal.height,
      });
    }

    for (const agg of aggregated) {
      const isDimmed = selectedNodeId
        ? !selectedConnectedNodes.has(agg.source) && !selectedConnectedNodes.has(agg.target)
        : false;
      allEdges.push({
        id: agg.id,
        source: agg.source,
        target: agg.target,
        type: "arch",
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 14,
          height: 14,
          color: THEME.edge[agg.dominantType] ?? "#8b9dc3",
        },
        data: {
          edge_type: agg.dominantType,
          count: agg.count,
          category: agg.dominantType,
          isPortalEdge: false,
          dimmed: isDimmed,
        },
      });
    }

    return { nodes: allNodes, edges: allEdges, loading: false, issues };
  }

  function filterByPersona(nodes: ArchNode[]): ArchNode[] {
    const allowed = PERSONA_NODE_TYPES[persona];
    if (allowed) {
      return nodes.filter((n) => allowed.has(n.node_type));
    }
    return nodes;
  }

  function filterByDetailLevel(nodes: ArchNode[]): ArchNode[] {
    if (detailLevel === "file") {
      return nodes.filter((n) => n.node_type === "file" || n.node_type === "module" || n.node_type === "config" || n.node_type === "document");
    }
    if (detailLevel === "class") {
      return nodes.filter((n) => n.node_type !== "function");
    }
    return nodes;
  }

  function applyUserFilters(nodes: ArchNode[]): ArchNode[] {
    return nodes.filter((n) => {
      if (!filters.nodeTypes.has(n.node_type)) return false;
      if (!filters.complexities.has(n.complexity)) return false;
      return true;
    });
  }

  function applyFocusMode(nodes: ArchNode[], edges: ArchEdge[]): ArchNode[] {
    if (!focusNodeId) return nodes;
    const direct = new Set<string>([focusNodeId]);
    for (const edge of edges) {
      if (edge.source === focusNodeId) direct.add(edge.target);
      if (edge.target === focusNodeId) direct.add(edge.source);
    }
    const neighborhood = new Set(direct);
    for (const nid of direct) {
      for (const edge of edges) {
        if (edge.source === nid) neighborhood.add(edge.target);
        if (edge.target === nid) neighborhood.add(edge.source);
      }
    }
    const focused = nodes.filter((n) => neighborhood.has(n.id));
    if (focused.length < 2) return nodes;
    return focused;
  }

  function buildArchFileData(
    node: ArchNode,
    searchHighlightIds: Set<string>,
    connectedNodes?: Set<string>,
  ) {
    let diffState: "changed" | "affected" | "none" = "none";
    if (diffMode) {
      if (changedNodeIds.has(node.id)) diffState = "changed";
      else if (affectedNodeIds.has(node.id)) diffState = "affected";
    }
    const dimmed = connectedNodes && selectedNodeId
      ? !connectedNodes.has(node.id)
      : false;
    return {
      node,
      hasDocs: node.has_doc,
      searchHighlight: searchHighlightIds.has(node.id),
      tourHighlight: tourHighlightedNodeIds.has(node.id),
      diffState,
      dimmed,
    };
  }

  function addChildNodes(
    allNodes: Node[],
    container: ContainerAtom,
    childPositions: Map<string, { x: number; y: number }>,
    containerPos: { x: number; y: number },
    nodeIndex: Map<string, ArchNode>,
    searchHighlightIds: Set<string>,
    connectedNodes?: Set<string>,
  ) {
    for (const childId of container.childNodeIds) {
      const node = nodeIndex.get(childId);
      if (!node) continue;
      const childPos = childPositions.get(childId) ?? { x: 0, y: 0 };
      const sizes = ARCH_NODE_SIZES[node.node_type as keyof typeof ARCH_NODE_SIZES] ?? ARCH_NODE_SIZES.file;
      allNodes.push({
        id: node.id,
        type: "archFile",
        position: { x: containerPos.x + childPos.x, y: containerPos.y + childPos.y },
        data: buildArchFileData(node, searchHighlightIds, connectedNodes),
        width: sizes.width,
        height: sizes.height,
      });
    }
  }

  function buildPortalSpecs(
    edges: ArchEdge[],
    visibleNodeIds: Set<string>,
    currentLayer: ArchLayer,
    allLayers: ArchLayer[],
    nodeIdToLayerId: Map<string, string>,
  ): PortalSpec[] {
    const crossLayerTargets = new Map<string, number>();

    for (const edge of edges) {
      const sourceInLayer = visibleNodeIds.has(edge.source);
      const targetInLayer = visibleNodeIds.has(edge.target);

      if (sourceInLayer && !targetInLayer) {
        const targetLayer = nodeIdToLayerId.get(edge.target);
        if (targetLayer && targetLayer !== currentLayer.id) {
          crossLayerTargets.set(targetLayer, (crossLayerTargets.get(targetLayer) ?? 0) + 1);
        }
      }
      if (!sourceInLayer && targetInLayer) {
        const sourceLayer = nodeIdToLayerId.get(edge.source);
        if (sourceLayer && sourceLayer !== currentLayer.id) {
          crossLayerTargets.set(sourceLayer, (crossLayerTargets.get(sourceLayer) ?? 0) + 1);
        }
      }
    }

    const portals: PortalSpec[] = [];
    for (const [targetLayerId, edgeCount] of crossLayerTargets) {
      const targetLayer = allLayers.find((l) => l.id === targetLayerId);
      portals.push({
        id: `portal:${currentLayer.id}→${targetLayerId}`,
        sourceLayerId: currentLayer.id,
        targetLayerId,
        targetLayerName: targetLayer?.name ?? targetLayerId,
        edgeCount,
      });
    }

    return portals;
  }

  return result;
}
