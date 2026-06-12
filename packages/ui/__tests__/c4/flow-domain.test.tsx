import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { useArchitectureStore } from "../../src/c4/store/use-architecture-store";
import { findShortestPath } from "../../src/c4/utils/graph-algorithms";
import { PathFinderModal } from "../../src/c4/panels/PathFinderModal";
import { ExecutionFlowOverlay } from "../../src/c4/overlays/ExecutionFlowOverlay";
import type { ExecutionFlowEntry } from "../../src/c4/overlays/ExecutionFlowOverlay";
import type { ArchEdge } from "../../src/c4/types";
import { createMockView } from "./fixtures";

const mockView = createMockView({ tour: [] });

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
    // Node pickers are typeahead inputs now (the old <select> over every
    // node didn't scale past small repos).
    expect(screen.getByLabelText("From node")).toBeTruthy();
    expect(screen.getByLabelText("To node")).toBeTruthy();
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

