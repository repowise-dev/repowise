import { describe, it, expect, beforeEach } from "vitest";
import { useArchitectureStore } from "../../src/c4/store/use-architecture-store";
import type {
  ArchNode,
  ArchEdge,
  ArchLayer,
  ArchTourStep,
  ArchNodeType,
  NavigationLevel,
  Persona,
  DetailLevel,
  SearchMode,
  SearchResult,
  ArchFilters,
  ContainerLayoutResult,
} from "../../src/c4/types";
import { createMockView } from "./fixtures";

const mockView = createMockView();

const store = useArchitectureStore;

beforeEach(() => {
  store.setState(store.getInitialState());
});

describe("useArchitectureStore", () => {
  describe("initial state", () => {
    it("starts with view null, overview level, overview persona, file detail", () => {
      const state = store.getState();
      expect(state.view).toBeNull();
      expect(state.navigationLevel).toBe("overview");
      expect(state.persona).toBe("overview");
      expect(state.detailLevel).toBe("file");
      expect(state.activeLayerId).toBeNull();
      expect(state.selectedNodeId).toBeNull();
      expect(state.expandedContainers.size).toBe(0);
      expect(state.containerLayoutCache.size).toBe(0);
      expect(state.nodesById.size).toBe(0);
      expect(state.edgesBySource.size).toBe(0);
      expect(state.edgesByTarget.size).toBe(0);
      expect(state.nodeIdToLayerId.size).toBe(0);
      expect(state.tourActive).toBe(false);
      expect(state.searchQuery).toBe("");
      expect(state.searchResults).toEqual([]);
      expect(state.focusNodeId).toBeNull();
      expect(state.diffMode).toBe(false);
      expect(state.codeViewerOpen).toBe(false);
    });
  });

  describe("setView builds indexes", () => {
    it("populates nodesById, edgesBySource, edgesByTarget, nodeIdToLayerId", () => {
      store.getState().setView(mockView);
      const state = store.getState();

      expect(state.nodesById.size).toBe(3);
      expect(state.nodesById.get("src/app.py")?.name).toBe("app.py");
      expect(state.nodesById.get("src/models.py")?.node_type).toBe("file");

      expect(state.edgesBySource.get("src/app.py")?.length).toBe(2);
      expect(state.edgesBySource.get("src/routes.py")?.length).toBe(1);
      expect(state.edgesBySource.has("src/models.py")).toBe(false);

      expect(state.edgesByTarget.get("src/routes.py")?.length).toBe(1);
      expect(state.edgesByTarget.get("src/models.py")?.length).toBe(2);

      expect(state.nodeIdToLayerId.get("src/app.py")).toBe("layer:api");
      expect(state.nodeIdToLayerId.get("src/routes.py")).toBe("layer:api");
      expect(state.nodeIdToLayerId.get("src/models.py")).toBe("layer:core");
    });
  });

  describe("setView initializes filters", () => {
    it("sets filters to include all node types, complexities, layers from view data", () => {
      store.getState().setView(mockView);
      const state = store.getState();

      expect(state.filters.nodeTypes.has("file")).toBe(true);
      expect(state.filters.complexities.has("simple")).toBe(true);
      expect(state.filters.complexities.has("moderate")).toBe(true);
      expect(state.filters.complexities.has("complex")).toBe(true);
      expect(state.filters.layerIds.has("layer:api")).toBe(true);
      expect(state.filters.layerIds.has("layer:core")).toBe(true);
      expect(state.filters.edgeCategories.has("imports")).toBe(true);
      expect(state.filters.edgeCategories.has("calls")).toBe(true);
    });
  });

  describe("navigation drill in/out", () => {
    it("drillIntoLayer sets layer-detail and activeLayerId, drillOut returns to overview", () => {
      store.getState().setView(mockView);
      store.getState().drillIntoLayer("layer:api");

      let state = store.getState();
      expect(state.navigationLevel).toBe("layer-detail");
      expect(state.activeLayerId).toBe("layer:api");
      expect(state.selectedNodeId).toBeNull();

      store.getState().drillOut();
      state = store.getState();
      expect(state.navigationLevel).toBe("overview");
      expect(state.activeLayerId).toBeNull();
    });

    it("drillIntoLayer clears expanded containers and layout cache", () => {
      store.getState().setView(mockView);
      store.getState().toggleContainer("dir:src");
      expect(store.getState().expandedContainers.size).toBe(1);

      store.getState().drillIntoLayer("layer:api");
      expect(store.getState().expandedContainers.size).toBe(0);
      expect(store.getState().containerLayoutCache.size).toBe(0);
    });
  });

  describe("selectNode", () => {
    it("selectNode auto-drills into the node's layer if different from active", () => {
      store.getState().setView(mockView);
      store.getState().drillIntoLayer("layer:api");
      store.getState().selectNode("src/models.py");

      const state = store.getState();
      expect(state.activeLayerId).toBe("layer:core");
      expect(state.navigationLevel).toBe("layer-detail");
      expect(state.selectedNodeId).toBe("src/models.py");
    });
  });

  describe("toggle container", () => {
    it("toggleContainer adds to expanded set, toggle again removes and clears layout cache", () => {
      store.getState().setView(mockView);
      store.getState().toggleContainer("dir:src");
      expect(store.getState().expandedContainers.has("dir:src")).toBe(true);

      const mockLayout: ContainerLayoutResult = {
        positions: new Map([["a", { x: 0, y: 0 }]]),
        size: { width: 200, height: 100 },
      };
      store.getState().setContainerLayout("dir:src", mockLayout);
      expect(store.getState().containerLayoutCache.has("dir:src")).toBe(true);

      store.getState().toggleContainer("dir:src");
      expect(store.getState().expandedContainers.has("dir:src")).toBe(false);
      expect(store.getState().containerLayoutCache.has("dir:src")).toBe(false);
    });
  });

  describe("persona clears caches", () => {
    it("setPersona clears containerLayoutCache and expandedContainers", () => {
      store.getState().setView(mockView);
      store.getState().toggleContainer("dir:src");
      expect(store.getState().expandedContainers.size).toBe(1);

      store.getState().setPersona("learn");
      expect(store.getState().persona).toBe("learn");
      expect(store.getState().expandedContainers.size).toBe(0);
      expect(store.getState().containerLayoutCache.size).toBe(0);
    });
  });

  describe("detail level clears caches", () => {
    it("setDetailLevel clears containerLayoutCache and expandedContainers", () => {
      store.getState().setView(mockView);
      store.getState().toggleContainer("dir:src");

      store.getState().setDetailLevel("symbol");
      expect(store.getState().detailLevel).toBe("symbol");
      expect(store.getState().expandedContainers.size).toBe(0);
      expect(store.getState().containerLayoutCache.size).toBe(0);
    });
  });

  describe("filter reset", () => {
    it("resetFilters restores all filter sets to full membership from current view", () => {
      store.getState().setView(mockView);
      store.getState().setNodeTypeFilter("file", false);
      expect(store.getState().filters.nodeTypes.has("file")).toBe(false);

      store.getState().resetFilters();
      const state = store.getState();
      expect(state.filters.nodeTypes.has("file")).toBe(true);
      expect(state.filters.complexities.has("simple")).toBe(true);
      expect(state.filters.complexities.has("moderate")).toBe(true);
      expect(state.filters.complexities.has("complex")).toBe(true);
      expect(state.filters.layerIds.has("layer:api")).toBe(true);
      expect(state.filters.layerIds.has("layer:core")).toBe(true);
    });
  });

  describe("tour lifecycle", () => {
    it("startTour → nextTourStep → prevTourStep → endTour transitions", () => {
      store.getState().setView(mockView);

      store.getState().startTour();
      let state = store.getState();
      expect(state.tourActive).toBe(true);
      expect(state.currentTourStep).toBe(0);
      expect(state.tourHighlightedNodeIds.has("src/app.py")).toBe(true);

      store.getState().nextTourStep();
      state = store.getState();
      expect(state.currentTourStep).toBe(1);
      expect(state.tourHighlightedNodeIds.has("src/routes.py")).toBe(true);
      expect(state.tourHighlightedNodeIds.has("src/app.py")).toBe(false);

      store.getState().prevTourStep();
      state = store.getState();
      expect(state.currentTourStep).toBe(0);
      expect(state.tourHighlightedNodeIds.has("src/app.py")).toBe(true);

      store.getState().endTour();
      state = store.getState();
      expect(state.tourActive).toBe(false);
      expect(state.currentTourStep).toBe(0);
      expect(state.tourHighlightedNodeIds.size).toBe(0);
    });
  });

  describe("tour step bounds", () => {
    it("prevTourStep at step 0 stays at 0", () => {
      store.getState().setView(mockView);
      store.getState().startTour();
      store.getState().prevTourStep();
      expect(store.getState().currentTourStep).toBe(0);
    });

    it("nextTourStep at last step stays at last step", () => {
      store.getState().setView(mockView);
      store.getState().startTour();
      store.getState().nextTourStep();
      store.getState().nextTourStep();
      expect(store.getState().currentTourStep).toBe(2);
      store.getState().nextTourStep();
      expect(store.getState().currentTourStep).toBe(2);
    });
  });

  describe("focus mode", () => {
    it("setFocusNode sets focusNodeId, clearFocus sets it to null", () => {
      store.getState().setFocusNode("src/app.py");
      expect(store.getState().focusNodeId).toBe("src/app.py");

      store.getState().clearFocus();
      expect(store.getState().focusNodeId).toBeNull();
    });
  });

  describe("code viewer", () => {
    it("openCodeViewer opens with nodeId, toggleCodeViewerExpanded toggles, closeCodeViewer resets", () => {
      store.getState().openCodeViewer("src/app.py");
      let state = store.getState();
      expect(state.codeViewerOpen).toBe(true);
      expect(state.codeViewerNodeId).toBe("src/app.py");
      expect(state.codeViewerExpanded).toBe(false);

      store.getState().toggleCodeViewerExpanded();
      expect(store.getState().codeViewerExpanded).toBe(true);

      store.getState().closeCodeViewer();
      state = store.getState();
      expect(state.codeViewerOpen).toBe(false);
      expect(state.codeViewerNodeId).toBeNull();
      expect(state.codeViewerExpanded).toBe(false);
    });
  });

  describe("diff mode", () => {
    it("setDiffMode and setDiffData set state correctly", () => {
      store.getState().setDiffMode(true);
      expect(store.getState().diffMode).toBe(true);

      const changed = new Set(["src/app.py"]);
      const affected = new Set(["src/routes.py"]);
      store.getState().setDiffData(changed, affected);

      const state = store.getState();
      expect(state.changedNodeIds.has("src/app.py")).toBe(true);
      expect(state.affectedNodeIds.has("src/routes.py")).toBe(true);
    });
  });

  describe("clearView", () => {
    it("resets all state back to initial", () => {
      store.getState().setView(mockView);
      store.getState().drillIntoLayer("layer:api");
      store.getState().selectNode("src/app.py");
      store.getState().setPersona("learn");
      store.getState().setFocusNode("src/app.py");

      store.getState().clearView();
      const state = store.getState();

      expect(state.view).toBeNull();
      expect(state.nodesById.size).toBe(0);
      expect(state.edgesBySource.size).toBe(0);
      expect(state.edgesByTarget.size).toBe(0);
      expect(state.nodeIdToLayerId.size).toBe(0);
      expect(state.navigationLevel).toBe("overview");
      expect(state.activeLayerId).toBeNull();
      expect(state.selectedNodeId).toBeNull();
      expect(state.persona).toBe("overview");
      expect(state.focusNodeId).toBeNull();
      expect(state.tourActive).toBe(false);
    });
  });

  describe("types compile", () => {
    it("all new types can be constructed and used correctly", () => {
      const node: ArchNode = mockView.nodes[0]!;
      expect(node.id).toBe("src/app.py");

      const edge: ArchEdge = mockView.edges[0]!;
      expect(edge.source).toBe("src/app.py");

      const layer: ArchLayer = mockView.layers[0]!;
      expect(layer.id).toBe("layer:api");

      const step: ArchTourStep = mockView.tour[0]!;
      expect(step.order).toBe(1);

      const nodeType: ArchNodeType = "file";
      expect(nodeType).toBe("file");

      const navLevel: NavigationLevel = "overview";
      expect(navLevel).toBe("overview");

      const persona: Persona = "learn";
      expect(persona).toBe("learn");

      const detail: DetailLevel = "symbol";
      expect(detail).toBe("symbol");

      const mode: SearchMode = "semantic";
      expect(mode).toBe("semantic");

      const result: SearchResult = {
        nodeId: "x",
        name: "test",
        node_type: "file",
        score: 0.9,
        matchedField: "name",
      };
      expect(result.score).toBe(0.9);

      const filters: ArchFilters = {
        nodeTypes: new Set(["file"]),
        complexities: new Set(["simple"]),
        layerIds: new Set(["layer:api"]),
        edgeCategories: new Set(["imports"]),
      };
      expect(filters.nodeTypes.size).toBe(1);

      const layout: ContainerLayoutResult = {
        positions: new Map([["a", { x: 10, y: 20 }]]),
        size: { width: 300, height: 200 },
      };
      expect(layout.size.width).toBe(300);
    });
  });
});

describe("setView legacy-payload hardening (kg-ux)", () => {
  it("survives an API response that predates the curated-KG schema", () => {
    // A pre-curation backend serves layers without sub_groups and no
    // entry_points/tour — the page must degrade, never crash
    // ("layer.sub_groups is not iterable" regression).
    const legacy = createMockView();
    for (const layer of legacy.layers) {
      delete (layer as Partial<ArchLayer>).sub_groups;
      delete (layer as Partial<ArchLayer>).display_order;
    }
    delete (legacy as Partial<typeof legacy>).entry_points;
    delete (legacy as Partial<typeof legacy>).entry_candidates;
    delete (legacy as Partial<typeof legacy>).tour;
    for (const node of legacy.nodes) {
      delete (node as Partial<ArchNode>).tags;
    }

    expect(() => store.getState().setView(legacy)).not.toThrow();

    const view = store.getState().view!;
    expect(view.layers.every((l) => Array.isArray(l.sub_groups))).toBe(true);
    expect(view.layers.map((l) => l.display_order)).toEqual(
      view.layers.map((_, i) => i),
    );
    expect(view.entry_points).toEqual([]);
    expect(view.tour).toEqual([]);
    expect(view.nodes.every((n) => Array.isArray(n.tags))).toBe(true);
  });
});
