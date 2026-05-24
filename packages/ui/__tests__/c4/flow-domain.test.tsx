import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { useArchitectureStore } from "../../src/c4/store/use-architecture-store";
import { findShortestPath } from "../../src/c4/utils/graph-algorithms";
import { useDiffNodeStyle } from "../../src/c4/overlays/DiffOverlay";
import { PathFinderModal } from "../../src/c4/panels/PathFinderModal";
import { ExecutionFlowOverlay } from "../../src/c4/overlays/ExecutionFlowOverlay";
import type { ExecutionFlowEntry } from "../../src/c4/overlays/ExecutionFlowOverlay";
import type { ArchitectureView, ArchEdge } from "../../src/c4/types";

const mockView: ArchitectureView = {
  project_name: "test-project",
  project_description: "A test project",
  layers: [
    { id: "layer:api", name: "API", description: "API layer", node_ids: ["src/app.py", "src/routes.py"], file_count: 2, complexity_distribution: { simple: 1, moderate: 1, complex: 0 }, health_score: 85 },
    { id: "layer:core", name: "Core", description: "Core logic", node_ids: ["src/models.py"], file_count: 1, complexity_distribution: { simple: 0, moderate: 0, complex: 1 }, health_score: 72 },
  ],
  nodes: [
    { id: "src/app.py", node_type: "file", name: "app.py", file_path: "src/app.py", line_range: null, summary: "Main app", complexity: "simple", tags: ["python"], language: "python", pagerank: 0.5, pagerank_percentile: 90, betweenness: 0.3, in_degree: 2, out_degree: 3, community_id: 1, is_entry_point: true, is_test: false, is_hotspot: false, is_dead: false, has_doc: true, primary_owner: "dev1", primary_owner_pct: 0.75, bus_factor: 2 },
    { id: "src/routes.py", node_type: "file", name: "routes.py", file_path: "src/routes.py", line_range: null, summary: "Route handlers", complexity: "moderate", tags: ["python"], language: "python", pagerank: 0.3, pagerank_percentile: 60, betweenness: 0.1, in_degree: 1, out_degree: 2, community_id: 1, is_entry_point: false, is_test: false, is_hotspot: true, is_dead: false, has_doc: false, primary_owner: "dev2", primary_owner_pct: 0.5, bus_factor: 3 },
    { id: "src/models.py", node_type: "file", name: "models.py", file_path: "src/models.py", line_range: null, summary: "Data models", complexity: "complex", tags: ["python"], language: "python", pagerank: 0.8, pagerank_percentile: 95, betweenness: 0.5, in_degree: 5, out_degree: 1, community_id: 2, is_entry_point: false, is_test: false, is_hotspot: false, is_dead: false, has_doc: true, primary_owner: "dev1", primary_owner_pct: 0.9, bus_factor: 1 },
  ],
  edges: [
    { source: "src/app.py", target: "src/routes.py", edge_type: "imports", direction: "forward" as const, weight: 1, confidence: 1 },
    { source: "src/app.py", target: "src/models.py", edge_type: "imports", direction: "forward" as const, weight: 1, confidence: 1 },
    { source: "src/routes.py", target: "src/models.py", edge_type: "calls", direction: "forward" as const, weight: 0.8, confidence: 0.9 },
  ],
  tour: [],
  total_files: 3,
  total_symbols: 25,
  total_edges: 3,
  languages: ["python"],
  frameworks: ["fastapi"],
  external_systems: [],
};

const store = useArchitectureStore;

beforeEach(() => {
  store.setState(store.getInitialState());
});

describe("findShortestPath", () => {
  it("finds direct path between connected nodes", () => {
    const result = findShortestPath(mockView.edges, "src/app.py", "src/models.py");
    expect(result).toEqual(["src/app.py", "src/models.py"]);
  });

  it("returns null for disconnected nodes", () => {
    const disconnectedEdges: ArchEdge[] = [
      mockView.edges[0]!,
      { source: "isolated/a.py", target: "isolated/b.py", edge_type: "imports", direction: "forward" as const, weight: 1, confidence: 1 },
    ];
    const result = findShortestPath(disconnectedEdges, "src/app.py", "isolated/b.py");
    expect(result).toBeNull();
  });

  it("returns single-element array for same source and target", () => {
    const result = findShortestPath(mockView.edges, "src/app.py", "src/app.py");
    expect(result).toEqual(["src/app.py"]);
  });
});

describe("PathFinderModal", () => {
  it("renders when pathFinderOpen is true", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().setPathFinderOpen(true);
    });
    render(<PathFinderModal />);
    expect(screen.getByText("Path Finder")).toBeTruthy();
    const selects = screen.getAllByRole("combobox");
    expect(selects).toHaveLength(2);
  });

  it("closes when close button is clicked", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().setPathFinderOpen(true);
    });
    render(<PathFinderModal />);
    const closeButton = screen.getByLabelText("Close path finder");
    fireEvent.click(closeButton);
    expect(store.getState().pathFinderOpen).toBe(false);
  });
});

describe("ExecutionFlowOverlay", () => {
  it("renders flow entries with cross-boundary indicator", () => {
    const flows: ExecutionFlowEntry[] = [
      { id: "flow-1", entry_point: "main.py", score: 0.95, call_chain: ["main.py", "app.py", "routes.py"], crosses_community: false },
      { id: "flow-2", entry_point: "worker.py", score: 0.72, call_chain: ["worker.py", "tasks.py"], crosses_community: true },
    ];
    render(<ExecutionFlowOverlay flows={flows} visible={true} />);
    expect(screen.getByText("main.py")).toBeTruthy();
    expect(screen.getByText("worker.py")).toBeTruthy();
    expect(screen.getByText("⚠ Cross-boundary")).toBeTruthy();
  });
});

describe("useDiffNodeStyle", () => {
  function DiffStyleDisplay({ nodeId }: { nodeId: string }) {
    const style = useDiffNodeStyle(nodeId);
    return <div data-testid="diff-result">{style ? style.diffState : "none"}</div>;
  }

  it("returns changed for nodes in changedNodeIds", () => {
    act(() => {
      store.getState().setDiffMode(true);
      store.getState().setDiffData(new Set(["src/app.py"]), new Set());
    });
    render(<DiffStyleDisplay nodeId="src/app.py" />);
    expect(screen.getByTestId("diff-result").textContent).toBe("changed");
  });

  it("returns faded for nodes not in changed or affected sets", () => {
    act(() => {
      store.getState().setDiffMode(true);
      store.getState().setDiffData(new Set(["src/app.py"]), new Set(["src/routes.py"]));
    });
    render(<DiffStyleDisplay nodeId="src/models.py" />);
    expect(screen.getByTestId("diff-result").textContent).toBe("faded");
  });
});
