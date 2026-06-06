import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { useArchitectureStore } from "../../src/c4/store/use-architecture-store";
import { Sidebar } from "../../src/c4/panels/Sidebar";
import { ProjectOverview } from "../../src/c4/panels/ProjectOverview";
import { ArchNodeInfo } from "../../src/c4/panels/ArchNodeInfo";
import { FileExplorer } from "../../src/c4/panels/FileExplorer";
import { createMockView } from "./fixtures";

vi.mock("../../src/dashboard/health-score-ring", () => ({
  HealthScoreRing: ({ score }: { score: number }) => (
    <div data-testid="health-score-ring">{score}</div>
  ),
}));

const mockView = createMockView();

const store = useArchitectureStore;

beforeEach(() => {
  act(() => store.setState(store.getInitialState()));
});

describe("Sidebar", () => {
  it("renders when view is loaded", () => {
    act(() => store.getState().setView(mockView));
    render(<Sidebar />);
    expect(screen.getByLabelText("Knowledge Graph sidebar")).toBeInTheDocument();
  });

  it("does not render when view is null", () => {
    const { container } = render(<Sidebar />);
    expect(container.firstChild).toBeNull();
  });

  it("switches between Info and Files tabs", () => {
    act(() => store.getState().setView(mockView));
    render(<Sidebar />);

    expect(screen.getByRole("tab", { name: "Info tab" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Files tab" })).toHaveAttribute("aria-selected", "false");

    fireEvent.click(screen.getByRole("tab", { name: "Files tab" }));

    expect(screen.getByRole("tab", { name: "Info tab" })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tab", { name: "Files tab" })).toHaveAttribute("aria-selected", "true");
  });

  it("shows ProjectOverview when no node is selected", () => {
    act(() => store.getState().setView(mockView));
    render(<Sidebar />);
    expect(screen.getByText("test-project")).toBeInTheDocument();
    expect(screen.getByText("A test project")).toBeInTheDocument();
  });

  it("shows ArchNodeInfo when a node is selected", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().selectNode("src/app.py");
    });
    render(<Sidebar />);
    expect(screen.getByText("app.py")).toBeInTheDocument();
    expect(screen.getByText("Main application entry point")).toBeInTheDocument();
  });
});

describe("ProjectOverview", () => {
  it("shows correct stats from mockView", () => {
    act(() => store.getState().setView(mockView));
    render(<ProjectOverview />);
    expect(screen.getByText("Nodes")).toBeInTheDocument();
    expect(screen.getByText("Edges")).toBeInTheDocument();
    expect(screen.getByText("Layers")).toBeInTheDocument();
    expect(screen.getAllByText("Languages").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Stats")).toBeInTheDocument();
  });

  it("renders language pills", () => {
    act(() => store.getState().setView(mockView));
    render(<ProjectOverview />);
    expect(screen.getByText("python")).toBeInTheDocument();
  });

  it("renders framework pills", () => {
    act(() => store.getState().setView(mockView));
    render(<ProjectOverview />);
    expect(screen.getByText("fastapi")).toBeInTheDocument();
  });

  it("shows Start tour button when tour exists", () => {
    act(() => store.getState().setView(mockView));
    render(<ProjectOverview />);
    expect(screen.getByText("Start tour (3 steps)")).toBeInTheDocument();
  });

  it("hides tour button when tour is empty", () => {
    const viewNoTour = { ...mockView, tour: [] };
    act(() => store.getState().setView(viewNoTour));
    render(<ProjectOverview />);
    expect(screen.queryByText(/Start tour/)).not.toBeInTheDocument();
  });

  it("hides tour button when tour is already active", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().startTour();
    });
    render(<ProjectOverview />);
    expect(screen.queryByText(/Start tour/)).not.toBeInTheDocument();
  });

  it("shows most connected nodes sorted by degree", () => {
    act(() => store.getState().setView(mockView));
    render(<ProjectOverview />);
    expect(screen.getByLabelText("Select models.py")).toBeInTheDocument();
    expect(screen.getByLabelText("Select app.py")).toBeInTheDocument();
  });

  it("clicking most connected node calls selectNode", () => {
    act(() => store.getState().setView(mockView));
    render(<ProjectOverview />);
    fireEvent.click(screen.getByLabelText("Select models.py"));
    expect(store.getState().selectedNodeId).toBe("src/models.py");
  });
});

describe("ArchNodeInfo", () => {
  it("renders type badge and node name", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().selectNode("src/app.py");
    });
    render(<ArchNodeInfo />);
    expect(screen.getByText("file")).toBeInTheDocument();
    expect(screen.getByText("app.py")).toBeInTheDocument();
  });

  it("close button calls selectNode(null)", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().selectNode("src/app.py");
    });
    render(<ArchNodeInfo />);
    fireEvent.click(screen.getByLabelText("Close panel"));
    expect(store.getState().selectedNodeId).toBeNull();
  });

  it("shows incoming and outgoing connections", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().selectNode("src/models.py");
    });
    render(<ArchNodeInfo />);
    expect(screen.getByText("Incoming (2)")).toBeInTheDocument();
    expect(screen.queryByText(/Outgoing/)).not.toBeInTheDocument();
  });

  it("renders tags as pills", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().selectNode("src/app.py");
    });
    render(<ArchNodeInfo />);
    expect(screen.getByText("python")).toBeInTheDocument();
    expect(screen.getByText("entry")).toBeInTheDocument();
  });

  it("shows graph metrics", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().selectNode("src/app.py");
    });
    render(<ArchNodeInfo />);
    expect(screen.getByText("PageRank")).toBeInTheDocument();
    expect(screen.getByText("Betweenness")).toBeInTheDocument();
    expect(screen.getByText("In-degree")).toBeInTheDocument();
    expect(screen.getByText("Out-degree")).toBeInTheDocument();
  });

  it("shows ownership section", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().selectNode("src/app.py");
    });
    render(<ArchNodeInfo />);
    expect(screen.getByText("Ownership")).toBeInTheDocument();
    expect(screen.getByText("dev1 (75%)")).toBeInTheDocument();
  });

  it("renders health section when health prop is provided", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().selectNode("src/app.py");
    });
    render(
      <ArchNodeInfo
        health={{ health_score: 80, hotspot_count: 1, dead_code_count: 0, doc_coverage_pct: 0.9 }}
      />,
    );
    expect(screen.getByTestId("health-score-ring")).toBeInTheDocument();
    expect(screen.getByText("Health")).toBeInTheDocument();
  });

  it("returns null when no node is selected", () => {
    act(() => store.getState().setView(mockView));
    const { container } = render(<ArchNodeInfo />);
    expect(container.innerHTML).toBe("");
  });
});

describe("FileExplorer", () => {
  it("builds tree from node file paths with directories collapsible", () => {
    act(() => store.getState().setView(mockView));
    render(<FileExplorer />);
    expect(screen.getByLabelText("Expand src")).toBeInTheDocument();
  });

  it("expands directories on click", () => {
    act(() => store.getState().setView(mockView));
    render(<FileExplorer />);
    fireEvent.click(screen.getByLabelText("Expand src"));
    expect(screen.getByLabelText("Select app.py")).toBeInTheDocument();
    expect(screen.getByLabelText("Select routes.py")).toBeInTheDocument();
    expect(screen.getByLabelText("Select models.py")).toBeInTheDocument();
  });

  it("clicking file calls selectNode which navigates to its layer", () => {
    act(() => store.getState().setView(mockView));
    render(<FileExplorer />);
    fireEvent.click(screen.getByLabelText("Expand src"));
    act(() => {
      fireEvent.click(screen.getByLabelText("Select app.py"));
    });
    expect(store.getState().selectedNodeId).toBe("src/app.py");
    expect(store.getState().activeLayerId).toBe("layer:api");
  });

  it("highlights selected file with accent background", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().selectNode("src/app.py");
    });
    render(<FileExplorer />);
    fireEvent.click(screen.getByLabelText("Expand src"));
    const button = screen.getByLabelText("Select app.py");
    expect(button.style.background).toContain("rgba(245,149,32,0.2)");
  });

  it("returns null when view is null", () => {
    const { container } = render(<FileExplorer />);
    expect(container.firstChild).toBeNull();
  });
});
