"use client";

import { useEffect, useState } from "react";
import { MarkerType, type Node, type Edge } from "@xyflow/react";
import { useArchitectureStore } from "../store/use-architecture-store";
import type { ArchitectureView, ArchNode, ArchEdge, ArchLayer } from "../types";
import { PERSONA_NODE_TYPES } from "../types";
import {
  buildContainers,
  enforceBoxBudget,
  getStandaloneNodeIds,
  MAX_VISIBLE_BOXES,
} from "../layout/containers";
import { aggregateEdges, capAggregatedEdges } from "../layout/edge-aggregation";
import {
  assignSlotsByRank,
  computeStage1Layout,
  computeStage2Layout,
  estimateContainerSize,
  hoistPrioritySlots,
  ARCH_NODE_SIZES,
  type ContainerAtom,
  type PortalSpec,
} from "../layout/two-stage-layout";
import { THEME, edgeColor } from "../theme/theme-variables";

export interface ArchitectureLayoutResult {
  nodes: Node[];
  edges: Edge[];
  loading: boolean;
  issues: string[];
  /** Aggregated arrows dropped by the visible-tier budget ("+N weaker links"). */
  hiddenEdgeCount: number;
}

export function useArchitectureLayout(): ArchitectureLayoutResult {
  const view = useArchitectureStore((s) => s.view);
  const navigationLevel = useArchitectureStore((s) => s.navigationLevel);
  const activeLayerId = useArchitectureStore((s) => s.activeLayerId);
  const activeSubGroupId = useArchitectureStore((s) => s.activeSubGroupId);
  const detailLevel = useArchitectureStore((s) => s.detailLevel);
  const persona = useArchitectureStore((s) => s.persona);
  const filters = useArchitectureStore((s) => s.filters);
  const showTests = useArchitectureStore((s) => s.showTests);
  const expandedContainers = useArchitectureStore((s) => s.expandedContainers);
  const expandedContainersVersion = useArchitectureStore((s) => s.expandedContainers.size);
  const stage1Tick = useArchitectureStore((s) => s.stage1Tick);
  const focusNodeId = useArchitectureStore((s) => s.focusNodeId);
  const selectedNodeId = useArchitectureStore((s) => s.selectedNodeId);
  const tourHighlightedNodeIds = useArchitectureStore((s) => s.tourHighlightedNodeIds);
  const tourActive = useArchitectureStore((s) => s.tourActive);
  const currentTourStep = useArchitectureStore((s) => s.currentTourStep);
  const searchResults = useArchitectureStore((s) => s.searchResults);
  const diffMode = useArchitectureStore((s) => s.diffMode);
  const changedNodeIds = useArchitectureStore((s) => s.changedNodeIds);
  const affectedNodeIds = useArchitectureStore((s) => s.affectedNodeIds);

  const [result, setResult] = useState<ArchitectureLayoutResult>({
    nodes: [],
    edges: [],
    loading: false,
    issues: [],
    hiddenEdgeCount: 0,
  });

  useEffect(() => {
    if (!view) {
      setResult({ nodes: [], edges: [], loading: false, issues: [], hiddenEdgeCount: 0 });
      return;
    }

    let cancelled = false;
    setResult((r) => ({ ...r, loading: true }));

    const run = async () => {
      if (navigationLevel === "overview") {
        return computeOverviewLayout(view);
      }
      if (navigationLevel === "layer-groups") {
        return computeLayerGroupsLayout(view);
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
    activeSubGroupId,
    detailLevel,
    persona,
    filters,
    showTests,
    expandedContainers,
    expandedContainersVersion,
    stage1Tick,
    focusNodeId,
    selectedNodeId,
    tourHighlightedNodeIds,
    tourActive,
    currentTourStep,
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

    const { visible: aggregated, hiddenCount } = capAggregatedEdges(
      aggregateEdges(currentView.edges, nodeToLayer),
    );
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

    // Curated artifacts carry a dependency-ordered display_order — re-rank
    // the card slots so the stack reads top→bottom in that order (position =
    // meaning). Uncurated views keep the edge-derived stacking untouched
    // (P6): their display_order is just insertion order, not a claim.
    const isCurated =
      currentView.layers.some((l: ArchLayer) => l.sub_groups.length > 0) ||
      currentView.entry_points.length > 0;

    const { positions: rawPositions, issues } = await computeStage1Layout(
      [],
      layerNodes,
      [],
      layoutEdges,
      new Map(),
    );
    const positions = isCurated
      ? assignSlotsByRank(
          rawPositions,
          new Map(displayLayers.map((l: ArchLayer) => [l.id, l.display_order])),
        )
      : rawPositions;

    const searchHighlightIds = new Set<string>(searchResults.map((r) => r.nodeId));
    // Dim-on-select (kg-ux plan B5): selecting a layer card fades unrelated
    // cards and arrows — fade, never vanish.
    const selectedLayerId =
      selectedNodeId && displayLayers.some((l: ArchLayer) => l.id === selectedNodeId)
        ? selectedNodeId
        : null;
    const connectedLayers = new Set<string>();
    if (selectedLayerId) {
      connectedLayers.add(selectedLayerId);
      for (const agg of aggregated) {
        if (agg.source === selectedLayerId) connectedLayers.add(agg.target);
        if (agg.target === selectedLayerId) connectedLayers.add(agg.source);
      }
    }
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
          // Tests mirror the code — demoted unless restored (decision 2).
          demoted: layer.id === "layer:test" && !showTests,
          dimmed:
            (selectedLayerId ? !connectedLayers.has(layer.id) : false) ||
            (searchHighlightIds.size > 0 && !hasSearchHit),
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
        color: "var(--color-diagram-edge)",
      },
      data: {
        edge_type: agg.dominantType,
        count: agg.count,
        category: agg.dominantType,
        dimmed: selectedLayerId
          ? agg.source !== selectedLayerId && agg.target !== selectedLayerId
          : false,
      },
    }));

    return { nodes, edges, loading: false, issues, hiddenEdgeCount: hiddenCount };
  }

  /** Cards for one curated layer: its sub-groups plus a synthetic card for any
   * files the curation pass left ungrouped, so every file keeps a home.
   * Enforces the visible-box budget: the lowest-pagerank groups collapse into
   * one "+N more groups" card before the tier ever exceeds the budget (P2).
   * Deterministic, so the groups tier and detail tier always agree on
   * membership. */
  function subGroupCards(
    layer: ArchLayer,
    nodesById: Map<string, ArchNode>,
  ): { id: string; name: string; node_ids: string[] }[] {
    const grouped = new Set(layer.sub_groups.flatMap((g) => g.node_ids));
    const leftovers = layer.node_ids.filter((id) => !grouped.has(id));
    const cards = layer.sub_groups.map((g) => ({ id: g.id, name: g.name, node_ids: g.node_ids }));
    if (leftovers.length > 0) {
      cards.push({ id: `${layer.id}:__ungrouped`, name: "Other files", node_ids: leftovers });
    }
    if (cards.length <= MAX_VISIBLE_BOXES) {
      return cards;
    }
    const score = (card: { node_ids: string[] }) =>
      Math.max(...card.node_ids.map((id) => nodesById.get(id)?.pagerank ?? 0));
    const ranked = [...cards].sort((a, b) => score(b) - score(a));
    const kept = ranked.slice(0, MAX_VISIBLE_BOXES - 1);
    const merged = ranked.slice(MAX_VISIBLE_BOXES - 1);
    const keptSet = new Set(kept.map((c) => c.id));
    return [
      ...cards.filter((c) => keptSet.has(c.id)),
      {
        id: `${layer.id}:__more`,
        name: `+${merged.length} more groups`,
        node_ids: merged.flatMap((c) => c.node_ids),
      },
    ];
  }

  /** Shape a sub-group card as an ArchLayer so LayerClusterNode renders it
   * (kind: "subGroup") — reuse, not a new node component. */
  function synthesizeCardLayer(
    card: { id: string; name: string; node_ids: string[] },
    nodesById: Map<string, ArchNode>,
  ): ArchLayer {
    const dist: Record<string, number> = { simple: 0, moderate: 0, complex: 0 };
    for (const id of card.node_ids) {
      const c = nodesById.get(id)?.complexity ?? "simple";
      dist[c] = (dist[c] ?? 0) + 1;
    }
    return {
      id: card.id,
      name: card.name,
      description: "",
      node_ids: card.node_ids,
      file_count: card.node_ids.length,
      complexity_distribution: dist,
      health_score: null,
      sub_groups: [],
      display_order: 0,
    };
  }

  // The intermediate "layer-groups" tier: only sub-group cards + aggregated
  // arrows + cross-layer portals. Never renders file cards (budget P2).
  async function computeLayerGroupsLayout(currentView: ArchitectureView): Promise<ArchitectureLayoutResult> {
    const layer = currentView.layers.find((l: ArchLayer) => l.id === activeLayerId);
    if (!layer) {
      return { nodes: [], edges: [], loading: false, issues: [`Layer ${activeLayerId} not found`], hiddenEdgeCount: 0 };
    }
    if (layer.sub_groups.length === 0) {
      // Layer lost its groups (e.g. filters) — degrade to the detail tier.
      return computeDetailLayout(currentView);
    }

    const nodesById = new Map(currentView.nodes.map((n: ArchNode) => [n.id, n]));
    const cards = subGroupCards(layer, nodesById);

    const nodeToBox = new Map<string, string>();
    for (const card of cards) {
      for (const id of card.node_ids) {
        nodeToBox.set(id, card.id);
      }
    }
    const { visible: aggregated, hiddenCount } = capAggregatedEdges(
      aggregateEdges(currentView.edges, nodeToBox),
    );

    const nodeIdToLayerId = useArchitectureStore.getState().nodeIdToLayerId;
    const layerNodeIdSet = new Set(layer.node_ids);
    const portalSpecs = buildPortalSpecs(
      currentView.edges, layerNodeIdSet, layer, currentView.layers, nodeIdToLayerId,
    );

    const cardLayoutNodes = cards.map((card) => ({
      id: card.id,
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
      cardLayoutNodes,
      portalSpecs,
      layoutEdges,
      new Map(),
    );

    const searchHighlightIds = new Set<string>(searchResults.map((r) => r.nodeId));
    // Dim-on-select (kg-ux plan B5) — card-level, same rule as overview.
    const selectedCardId =
      selectedNodeId && cards.some((c) => c.id === selectedNodeId) ? selectedNodeId : null;
    const connectedCards = new Set<string>();
    if (selectedCardId) {
      connectedCards.add(selectedCardId);
      for (const agg of aggregated) {
        if (agg.source === selectedCardId) connectedCards.add(agg.target);
        if (agg.target === selectedCardId) connectedCards.add(agg.source);
      }
    }
    const nodes: Node[] = cards.map((card) => {
      const pos = positions.get(card.id) ?? { x: 0, y: 0, width: 0, height: 0 };
      return {
        id: card.id,
        type: "subGroupCluster",
        position: { x: pos.x, y: pos.y },
        data: {
          layer: synthesizeCardLayer(card, nodesById),
          kind: "subGroup",
          searchHighlight: card.node_ids.some((nid) => searchHighlightIds.has(nid)),
          dimmed:
            (selectedCardId ? !connectedCards.has(card.id) : false) ||
            (searchHighlightIds.size > 0 &&
              !card.node_ids.some((nid) => searchHighlightIds.has(nid))),
        },
        width: ARCH_NODE_SIZES.layerCluster.width,
        height: ARCH_NODE_SIZES.layerCluster.height,
      };
    });

    // "You are here": dashed boundary around the drilled layer's cards.
    const groupsFrame = buildScopeFrameNode(
      `frame:${layer.id}`,
      layer.name,
      cards.map((card) => {
        const pos = positions.get(card.id) ?? { x: 0, y: 0, width: 0, height: 0 };
        return {
          x: pos.x,
          y: pos.y,
          width: ARCH_NODE_SIZES.layerCluster.width,
          height: ARCH_NODE_SIZES.layerCluster.height,
        };
      }),
    );
    if (groupsFrame) nodes.unshift(groupsFrame);

    for (const portal of portalSpecs) {
      const pos = positions.get(portal.id) ?? { x: 0, y: 0, width: 0, height: 0 };
      nodes.push({
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

    const edges: Edge[] = aggregated.map((agg) => ({
      id: agg.id,
      source: agg.source,
      target: agg.target,
      type: "arch",
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 14,
        height: 14,
        color: "var(--color-diagram-edge)",
      },
      data: {
        edge_type: agg.dominantType,
        count: agg.count,
        category: agg.dominantType,
        dimmed: selectedCardId
          ? agg.source !== selectedCardId && agg.target !== selectedCardId
          : false,
      },
    }));

    return { nodes, edges, loading: false, issues, hiddenEdgeCount: hiddenCount };
  }

  async function computeDetailLayout(currentView: ArchitectureView): Promise<ArchitectureLayoutResult> {
    const containerLayoutCache = useArchitectureStore.getState().containerLayoutCache;
    const containerSizeMemory = useArchitectureStore.getState().containerSizeMemory;
    const layer = currentView.layers.find((l: ArchLayer) => l.id === activeLayerId);
    if (!layer) {
      return { nodes: [], edges: [], loading: false, issues: [`Layer ${activeLayerId} not found`], hiddenEdgeCount: 0 };
    }

    const nodesById = new Map(currentView.nodes.map((n: ArchNode) => [n.id, n]));

    // When a curated sub-group is active, file cards are scoped to it and the
    // sibling groups stay collapsed cards (locked decision 1).
    let scopeNodeIds = layer.node_ids;
    let siblingCards: { id: string; name: string; node_ids: string[] }[] = [];
    const activeCard = activeSubGroupId
      ? subGroupCards(layer, nodesById).find((c) => c.id === activeSubGroupId)
      : undefined;
    if (activeSubGroupId && activeCard) {
      scopeNodeIds = activeCard.node_ids;
      siblingCards = subGroupCards(layer, nodesById).filter((c) => c.id !== activeSubGroupId);
    }

    const layerNodeIds = new Set(scopeNodeIds);
    let layerNodes = currentView.nodes.filter((n: ArchNode) => layerNodeIds.has(n.id));

    layerNodes = filterByPersona(layerNodes);
    layerNodes = filterByDetailLevel(layerNodes);
    layerNodes = applyUserFilters(layerNodes);

    if (focusNodeId) {
      layerNodes = applyFocusMode(layerNodes, currentView.edges);
    }

    const visibleNodeIds = new Set(layerNodes.map((n: ArchNode) => n.id));

    // Curated sub-groups from the artifact drive grouping (P3); folder /
    // community heuristics remain the fallback for uncurated layers (P6).
    // Inside one sub-group the heuristics regroup its files.
    const rawContainers = activeCard
      ? buildContainers(layerNodes, currentView.edges, "auto")
      : buildContainers(layerNodes, currentView.edges, "curated", layer.sub_groups);
    const rawStandaloneIds = getStandaloneNodeIds(layerNodes, rawContainers);

    // Hard visible-box budget (P2): sibling cards occupy slots too, so the
    // file tier degrades into a "+N more" container before exceeding it.
    const { containers, standaloneIds } = enforceBoxBudget(
      rawContainers,
      rawStandaloneIds,
      (id) => nodesById.get(id)?.pagerank ?? 0,
      Math.max(4, MAX_VISIBLE_BOXES - siblingCards.length),
    );

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
    // Sibling groups absorb their members so in-layer cross-group edges
    // aggregate into one labelled arrow per visible card pair (P2).
    for (const sibling of siblingCards) {
      for (const id of sibling.node_ids) {
        nodeToBox.set(id, sibling.id);
      }
    }

    const { visible: aggregated, hiddenCount } = capAggregatedEdges(
      aggregateEdges(currentView.edges, nodeToBox),
    );

    const standaloneLayoutNodes = standaloneIds.map((id) => {
      const node = nodesById.get(id);
      const nodeType = node?.node_type ?? "file";
      const sizes = ARCH_NODE_SIZES[nodeType as keyof typeof ARCH_NODE_SIZES] ?? ARCH_NODE_SIZES.file;
      return { id, width: sizes.width, height: sizes.height };
    });
    for (const sibling of siblingCards) {
      standaloneLayoutNodes.push({
        id: sibling.id,
        width: ARCH_NODE_SIZES.layerCluster.width,
        height: ARCH_NODE_SIZES.layerCluster.height,
      });
    }

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
      expandedContainers,
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
      // Selecting a folder/sibling card keeps its own members lit (B5).
      const selectedContainer = containers.find((c) => c.id === selectedNodeId);
      if (selectedContainer) {
        for (const id of selectedContainer.childNodeIds) selectedConnectedNodes.add(id);
      }
      const selectedSibling = siblingCards.find((sb) => sb.id === selectedNodeId);
      if (selectedSibling) {
        for (const id of selectedSibling.node_ids) selectedConnectedNodes.add(id);
      }
    }
    // Box identity of the selection, for box-level dimming.
    const selectedBox = selectedNodeId ? nodeToBox.get(selectedNodeId) ?? selectedNodeId : null;

    // Scope members (containers + standalone files) collect their rects so
    // the "you are here" frame can wrap them; siblings/portals stay outside.
    const frameRects: { x: number; y: number; width: number; height: number }[] = [];

    for (const container of containers) {
      const pos = stage1Positions.get(container.id) ?? { x: 0, y: 0, width: 0, height: 0 };
      const isExpanded = expandedContainers.has(container.id);
      const searchHitCount = container.childNodeIds.filter((id) => searchHighlightIds.has(id)).length;
      frameRects.push({ x: pos.x, y: pos.y, width: pos.width, height: pos.height });

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
          dimmed:
            (selectedBox
              ? container.id !== selectedBox &&
                !container.childNodeIds.some((id) => selectedConnectedNodes.has(id))
              : false) ||
            (searchHighlightIds.size > 0 && searchHitCount === 0),
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
          // Entry points anchor to the top of their group (plan C-3);
          // demoted barrels never count as entries.
          const hoisted = hoistPrioritySlots(stage2.positions, childLayoutNodes, (id) => {
            const child = nodesById.get(id);
            return Boolean(child?.is_entry_point) && !(child?.tags.includes("barrel") ?? false);
          });
          const layoutResult = {
            positions: hoisted,
            size: stage2.actualSize,
          };
          store.setContainerLayout(container.id, layoutResult);

          const estimated = containerSizeMemory.get(container.id) ?? estimateContainerSize(container.childNodeIds.length);
          const deviation = Math.abs(stage2.actualSize.width - estimated.width) / Math.max(estimated.width, 1);
          if (deviation > 0.2) {
            store.setContainerSizeEstimate(container.id, stage2.actualSize);
            store.bumpStage1Tick();
          }

          addChildNodes(allNodes, container, hoisted, pos, nodesById, searchHighlightIds, selectedConnectedNodes);
        }
      }
    }

    for (const id of standaloneIds) {
      const node = nodesById.get(id);
      if (!node) continue;
      const pos = stage1Positions.get(id) ?? { x: 0, y: 0, width: 0, height: 0 };
      const sizes = ARCH_NODE_SIZES[node.node_type as keyof typeof ARCH_NODE_SIZES] ?? ARCH_NODE_SIZES.file;
      frameRects.push({ x: pos.x, y: pos.y, width: sizes.width, height: sizes.height });
      allNodes.push({
        id: node.id,
        type: "archFile",
        position: { x: pos.x, y: pos.y },
        data: buildArchFileData(node, searchHighlightIds, selectedConnectedNodes),
        width: sizes.width,
        height: sizes.height,
      });
    }

    // "You are here": dashed boundary around the active scope's content.
    const detailFrame = buildScopeFrameNode(
      `frame:${activeSubGroupId ?? layer.id}`,
      activeCard ? `${layer.name} › ${activeCard.name}` : layer.name,
      frameRects,
    );
    if (detailFrame) allNodes.unshift(detailFrame);

    for (const sibling of siblingCards) {
      const pos = stage1Positions.get(sibling.id) ?? { x: 0, y: 0, width: 0, height: 0 };
      allNodes.push({
        id: sibling.id,
        type: "subGroupCluster",
        position: { x: pos.x, y: pos.y },
        data: {
          layer: synthesizeCardLayer(sibling, nodesById),
          kind: "subGroup",
          sibling: true,
          searchHighlight: sibling.node_ids.some((nid) => searchHighlightIds.has(nid)),
          dimmed:
            (selectedBox
              ? sibling.id !== selectedBox &&
                !sibling.node_ids.some((id) => selectedConnectedNodes.has(id))
              : false) ||
            (searchHighlightIds.size > 0 &&
              !sibling.node_ids.some((nid) => searchHighlightIds.has(nid))),
        },
        width: ARCH_NODE_SIZES.layerCluster.width,
        height: ARCH_NODE_SIZES.layerCluster.height,
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
          // Portals never appear in aggregated edges (nodeToBox has no portal
          // mapping), so testing aggregated here dimmed every portal whenever
          // anything was selected. A portal is related when the selection's
          // neighbourhood reaches into its target layer.
          dimmed: selectedBox
            ? ![...selectedConnectedNodes].some(
                (id) => nodeIdToLayerId.get(id) === portal.targetLayerId,
              )
            : false,
        },
        width: ARCH_NODE_SIZES.portal.width,
        height: ARCH_NODE_SIZES.portal.height,
      });
    }

    for (const agg of aggregated) {
      const isDimmed = selectedNodeId
        ? agg.source !== selectedBox &&
          agg.target !== selectedBox &&
          !selectedConnectedNodes.has(agg.source) &&
          !selectedConnectedNodes.has(agg.target)
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
          color: "var(--color-diagram-edge)",
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

    return { nodes: allNodes, edges: allEdges, loading: false, issues, hiddenEdgeCount: hiddenCount };
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
    const selectionDim = connectedNodes && selectedNodeId
      ? !connectedNodes.has(node.id)
      : false;
    // Search recedes everything that doesn't match (kg-ux plan B6) —
    // matches keep the accent highlight, the rest fades.
    const searchDim = searchHighlightIds.size > 0 && !searchHighlightIds.has(node.id);
    const dimmed = selectionDim || searchDim;
    const isTourTarget = tourActive && tourHighlightedNodeIds.has(node.id);
    return {
      node,
      hasDocs: node.has_doc,
      searchHighlight: searchHighlightIds.has(node.id),
      tourHighlight: tourHighlightedNodeIds.has(node.id),
      // Numbered step badge on the highlighted node (plan C-2).
      tourStepNumber: isTourTarget ? currentTourStep + 1 : undefined,
      diffState,
      dimmed,
    };
  }

  /** Dashed "you are here" boundary behind the drilled scope (kg-ux §2.4).
   * Pure underlay: zIndex -1, non-interactive, sized to the members' bbox. */
  function buildScopeFrameNode(
    frameId: string,
    label: string,
    rects: { x: number; y: number; width: number; height: number }[],
  ): Node | null {
    if (rects.length === 0) return null;
    const PAD = { top: 56, right: 40, bottom: 40, left: 40 };
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const r of rects) {
      minX = Math.min(minX, r.x);
      minY = Math.min(minY, r.y);
      maxX = Math.max(maxX, r.x + r.width);
      maxY = Math.max(maxY, r.y + r.height);
    }
    const width = maxX - minX + PAD.left + PAD.right;
    const height = maxY - minY + PAD.top + PAD.bottom;
    return {
      id: frameId,
      type: "scopeFrame",
      position: { x: minX - PAD.left, y: minY - PAD.top },
      data: { label, width, height },
      width,
      height,
      zIndex: -1,
      selectable: false,
      draggable: false,
      focusable: false,
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
