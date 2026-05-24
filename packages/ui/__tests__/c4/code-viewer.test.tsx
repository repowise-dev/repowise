import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { useArchitectureStore } from "../../src/c4/store/use-architecture-store";
import { CodeViewer, getLanguageFromPath } from "../../src/c4/panels/CodeViewer";
import type { ArchitectureView } from "../../src/c4/types";

const mockView: ArchitectureView = {
  project_name: "test-project",
  project_description: "A test project",
  layers: [
    { id: "layer:api", name: "API", description: "API layer", node_ids: ["src/app.py", "src/routes.py"], file_count: 2, complexity_distribution: { simple: 1, moderate: 1, complex: 0 }, health_score: 85 },
    { id: "layer:core", name: "Core", description: "Core logic", node_ids: ["src/models.py"], file_count: 1, complexity_distribution: { simple: 0, moderate: 0, complex: 1 }, health_score: 72 },
  ],
  nodes: [
    { id: "src/app.py", node_type: "file", name: "app.py", file_path: "src/app.py", line_range: null, summary: "Main app", complexity: "simple", tags: ["python", "entry"], language: "python", pagerank: 0.5, pagerank_percentile: 90, betweenness: 0.3, in_degree: 2, out_degree: 3, community_id: 1, is_entry_point: true, is_test: false, is_hotspot: false, is_dead: false, has_doc: true, primary_owner: "dev1", primary_owner_pct: 0.75, bus_factor: 2 },
    { id: "src/routes.py", node_type: "file", name: "routes.py", file_path: "src/routes.py", line_range: [10, 25], summary: "Route handlers", complexity: "moderate", tags: ["python"], language: "python", pagerank: 0.3, pagerank_percentile: 60, betweenness: 0.1, in_degree: 1, out_degree: 2, community_id: 1, is_entry_point: false, is_test: false, is_hotspot: true, is_dead: false, has_doc: false, primary_owner: "dev2", primary_owner_pct: 0.5, bus_factor: 3 },
    { id: "src/models.py", node_type: "file", name: "models.py", file_path: "src/models.py", line_range: null, summary: "Data models", complexity: "complex", tags: ["python", "core"], language: "python", pagerank: 0.8, pagerank_percentile: 95, betweenness: 0.5, in_degree: 5, out_degree: 1, community_id: 2, is_entry_point: false, is_test: false, is_hotspot: false, is_dead: false, has_doc: true, primary_owner: "dev1", primary_owner_pct: 0.9, bus_factor: 1 },
  ],
  edges: [
    { source: "src/app.py", target: "src/routes.py", edge_type: "imports", direction: "forward", weight: 1, confidence: 1 },
    { source: "src/app.py", target: "src/models.py", edge_type: "imports", direction: "forward", weight: 1, confidence: 1 },
    { source: "src/routes.py", target: "src/models.py", edge_type: "calls", direction: "forward", weight: 0.8, confidence: 0.9 },
  ],
  tour: [
    { order: 1, title: "Entry Point", description: "Start here", node_ids: ["src/app.py"] },
    { order: 2, title: "Routes", description: "HTTP handlers", node_ids: ["src/routes.py"] },
    { order: 3, title: "Models", description: "Data layer", node_ids: ["src/models.py"] },
  ],
  total_files: 3,
  total_symbols: 25,
  total_edges: 3,
  languages: ["python"],
  frameworks: ["fastapi"],
  external_systems: [],
};

const store = useArchitectureStore;
const mockFetchContent = vi.fn();

beforeEach(() => {
  act(() => store.setState(store.getInitialState()));
  mockFetchContent.mockReset();
});

describe("CodeViewer", () => {
  it("renders when open", async () => {
    mockFetchContent.mockResolvedValue("print('hello')\nprint('world')");
    act(() => {
      store.getState().setView(mockView);
      store.getState().openCodeViewer("src/app.py");
    });
    render(<CodeViewer fetchContent={mockFetchContent} />);
    await waitFor(() => {
      expect(screen.getByText("app.py")).toBeInTheDocument();
    });
    expect(mockFetchContent).toHaveBeenCalledWith("src/app.py");
  });

  it("is hidden when closed", () => {
    act(() => store.getState().setView(mockView));
    const { container } = render(<CodeViewer fetchContent={mockFetchContent} />);
    expect(container.firstChild).toBeNull();
  });

  it("highlights lines in range", async () => {
    const content = Array.from({ length: 30 }, (_, i) => "line " + (i + 1)).join("\n");
    mockFetchContent.mockResolvedValue(content);
    act(() => {
      store.getState().setView(mockView);
      store.getState().openCodeViewer("src/routes.py");
    });
    const { container } = render(<CodeViewer fetchContent={mockFetchContent} />);
    await waitFor(() => {
      expect(screen.getByText("line 1")).toBeInTheDocument();
    });
    const lineDivs = container.querySelectorAll("code > div");
    const highlighted = Array.from(lineDivs).filter(
      (div) => (div as HTMLElement).style.background === "rgba(245, 149, 32, 0.1)",
    );
    const transparent = Array.from(lineDivs).filter(
      (div) => (div as HTMLElement).style.background === "transparent",
    );
    expect(highlighted.length).toBe(16); // lines 10-25
    expect(transparent.length).toBeGreaterThan(0);
  });

  it("supports expand and collapse", async () => {
    mockFetchContent.mockResolvedValue("code");
    act(() => {
      store.getState().setView(mockView);
      store.getState().openCodeViewer("src/app.py");
    });
    render(<CodeViewer fetchContent={mockFetchContent} />);
    await waitFor(() => {
      expect(screen.getByText("app.py")).toBeInTheDocument();
    });
    const expandBtn = screen.getByLabelText("Expand code viewer");
    fireEvent.click(expandBtn);
    expect(store.getState().codeViewerExpanded).toBe(true);
    const collapseBtn = screen.getByLabelText("Collapse code viewer");
    fireEvent.click(collapseBtn);
    expect(store.getState().codeViewerExpanded).toBe(false);
  });

  it("closes via close button", async () => {
    mockFetchContent.mockResolvedValue("code");
    act(() => {
      store.getState().setView(mockView);
      store.getState().openCodeViewer("src/app.py");
    });
    render(<CodeViewer fetchContent={mockFetchContent} />);
    await waitFor(() => {
      expect(screen.getByText("app.py")).toBeInTheDocument();
    });
    const closeBtn = screen.getByLabelText("Close code viewer");
    fireEvent.click(closeBtn);
    expect(store.getState().codeViewerOpen).toBe(false);
  });

  it("detects languages from file paths", () => {
    expect(getLanguageFromPath("src/app.py")).toBe("python");
    expect(getLanguageFromPath("src/index.ts")).toBe("typescript");
    expect(getLanguageFromPath("src/App.tsx")).toBe("typescript");
    expect(getLanguageFromPath("main.go")).toBe("go");
    expect(getLanguageFromPath("lib.rs")).toBe("rust");
    expect(getLanguageFromPath("Makefile")).toBe("text");
    expect(getLanguageFromPath("styles.css")).toBe("css");
  });

  it("shows loading state", () => {
    mockFetchContent.mockReturnValue(new Promise(() => {}));
    act(() => {
      store.getState().setView(mockView);
      store.getState().openCodeViewer("src/app.py");
    });
    render(<CodeViewer fetchContent={mockFetchContent} />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-label", "Loading code");
  });

  it("shows error state with retry", async () => {
    mockFetchContent.mockRejectedValue(new Error("Network error"));
    act(() => {
      store.getState().setView(mockView);
      store.getState().openCodeViewer("src/app.py");
    });
    render(<CodeViewer fetchContent={mockFetchContent} />);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });
    expect(screen.getByText("Network error")).toBeInTheDocument();
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });
});
