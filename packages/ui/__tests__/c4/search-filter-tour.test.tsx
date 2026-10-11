import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { useArchitectureStore } from "../../src/c4/store/use-architecture-store";
import { SearchBar } from "../../src/c4/panels/SearchBar";
import { FilterPanel } from "../../src/c4/panels/FilterPanel";
import { NodeTypeCategoryFilters } from "../../src/c4/panels/NodeTypeCategoryFilters";
import { LearnPanel } from "../../src/c4/panels/LearnPanel";
import { PersonaSelector } from "../../src/c4/panels/PersonaSelector";
import { fuzzyMatch, scoreField } from "../../src/c4/utils/fuzzy-match";
import { createMockView } from "./fixtures";

vi.mock("lucide-react", async (importOriginal) => ({
  // Partial mock: kind-icons.ts pulls many glyphs; only the ones these
  // tests assert on are replaced with testid stubs.
  ...(await importOriginal<typeof import("lucide-react")>()),
  Search: ({ size }: { size?: number }) => <span data-testid="icon-search">S</span>,
  Filter: ({ size }: { size?: number }) => <span data-testid="icon-filter">F</span>,
  Compass: ({ size }: { size?: number }) => <span data-testid="icon-compass">C</span>,
  X: ({ size }: { size?: number }) => <span data-testid="icon-x">X</span>,
  ChevronLeft: ({ size }: { size?: number }) => <span data-testid="icon-chevron-left">{"<"}</span>,
  ChevronRight: ({ size }: { size?: number }) => <span data-testid="icon-chevron-right">{">"}</span>,
  Info: ({ size }: { size?: number }) => <span data-testid="icon-info">I</span>,
  FolderOpen: ({ size }: { size?: number }) => <span data-testid="icon-folder">FO</span>,
}));

const mockView = createMockView();

const store = useArchitectureStore;

beforeEach(() => {
  act(() => store.setState(store.getInitialState()));
});

describe("fuzzyMatch utility", () => {
  it("returns ranked results for matching query and empty for non-matching", () => {
    const results = fuzzyMatch("app", mockView.nodes);
    expect(results.length).toBeGreaterThan(0);
    expect(results[0]!.name).toBe("app.py");
    expect(results[0]!.score).toBeGreaterThan(0);

    for (let i = 1; i < results.length; i++) {
      expect(results[i]!.score).toBeLessThanOrEqual(results[i - 1]!.score);
    }

    const noResults = fuzzyMatch("xyz123nope", mockView.nodes);
    expect(noResults).toHaveLength(0);
  });
});

describe("SearchBar", () => {
  it("shows dropdown with results when typing a query", () => {
    act(() => store.getState().setView(mockView));
    render(<SearchBar />);
    const input = screen.getByPlaceholderText("Search nodes... (press /)");
    fireEvent.change(input, { target: { value: "app" } });
    expect(screen.getByText("app.py")).toBeInTheDocument();
    expect(screen.getByText("file")).toBeInTheDocument();
  });

  it("navigates to node on result click and clears search", () => {
    act(() => store.getState().setView(mockView));
    render(<SearchBar />);
    const input = screen.getByPlaceholderText("Search nodes... (press /)");
    fireEvent.change(input, { target: { value: "models" } });
    const result = screen.getByText("models.py");
    fireEvent.click(result);
    expect(store.getState().selectedNodeId).toBe("src/models.py");
    expect(store.getState().searchQuery).toBe("");
  });

  it("focuses input on arch:focus-search event", () => {
    act(() => store.getState().setView(mockView));
    render(<SearchBar />);
    const input = screen.getByPlaceholderText("Search nodes... (press /)");
    act(() => {
      window.dispatchEvent(new CustomEvent("arch:focus-search"));
    });
    expect(document.activeElement).toBe(input);
  });
});

describe("FilterPanel", () => {
  it("renders when filterPanelOpen is true and hides when false", () => {
    act(() => {
      store.getState().setView(mockView);
      store.setState({ filterPanelOpen: true });
    });
    const { rerender } = render(<FilterPanel />);
    expect(screen.getByText("Node Types")).toBeInTheDocument();

    act(() => store.setState({ filterPanelOpen: false }));
    rerender(<FilterPanel />);
    expect(screen.queryByText("Node Types")).not.toBeInTheDocument();
  });

  it("unchecking a node type removes it from filters", () => {
    act(() => {
      store.getState().setView(mockView);
      store.setState({ filterPanelOpen: true });
    });
    render(<FilterPanel />);
    const checkbox = screen.getByLabelText("file") as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);
    expect(store.getState().filters.nodeTypes.has("file")).toBe(false);
  });

  it("unchecking a complexity removes it from filters", () => {
    act(() => {
      store.getState().setView(mockView);
      store.setState({ filterPanelOpen: true });
    });
    render(<FilterPanel />);
    const checkbox = screen.getByLabelText("simple") as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);
    expect(store.getState().filters.complexities.has("simple")).toBe(false);
  });

  it("Reset All restores filters", () => {
    act(() => {
      store.getState().setView(mockView);
      store.setState({ filterPanelOpen: true });
    });
    render(<FilterPanel />);
    const checkbox = screen.getByLabelText("file") as HTMLInputElement;
    fireEvent.click(checkbox);
    expect(store.getState().filters.nodeTypes.has("file")).toBe(false);
    fireEvent.click(screen.getByText("Reset All"));
    expect(store.getState().filters.nodeTypes.has("file")).toBe(true);
  });
});

describe("NodeTypeCategoryFilters", () => {
  it("clicking a category pill toggles it off", () => {
    act(() => store.getState().setView(mockView));
    render(<NodeTypeCategoryFilters />);
    const codePill = screen.getByText("code");
    fireEvent.click(codePill);
    expect(store.getState().nodeTypeFilters.code).toBe(false);
  });
});

describe("LearnPanel", () => {
  it("shows Start Tour button and starts tour on click", () => {
    act(() => store.getState().setView(mockView));
    render(<LearnPanel />);
    const btn = screen.getByText("Start Tour");
    fireEvent.click(btn);
    expect(store.getState().tourActive).toBe(true);
    expect(screen.getByText("Entry Point")).toBeInTheDocument();
  });

  it("Next and Prev buttons navigate steps", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().startTour();
    });
    render(<LearnPanel />);
    fireEvent.click(screen.getByText("Next"));
    expect(store.getState().currentTourStep).toBe(1);
    fireEvent.click(screen.getByText("Prev"));
    expect(store.getState().currentTourStep).toBe(0);
  });

  it("tour updates highlighted node ids per step", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().startTour();
    });
    expect(store.getState().tourHighlightedNodeIds.has("src/app.py")).toBe(true);
    act(() => store.getState().nextTourStep());
    expect(store.getState().tourHighlightedNodeIds.has("src/routes.py")).toBe(true);
  });

  it("store tour step actions work correctly", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().startTour();
    });
    act(() => store.getState().nextTourStep());
    expect(store.getState().currentTourStep).toBe(1);
    act(() => store.getState().prevTourStep());
    expect(store.getState().currentTourStep).toBe(0);
  });

  it("shows no-tour message when tour is empty", () => {
    act(() => store.getState().setView({ ...mockView, tour: [] }));
    render(<LearnPanel />);
    expect(screen.getByText("No guided tour available")).toBeInTheDocument();
  });
});

describe("PersonaSelector", () => {
  it("switching persona updates store", () => {
    act(() => store.getState().setView(mockView));
    render(<PersonaSelector />);
    const select = screen.getByDisplayValue("Overview") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "learn" } });
    expect(store.getState().persona).toBe("learn");
  });

  it("learn persona allows LearnPanel to show tour UI", () => {
    act(() => {
      store.getState().setView(mockView);
      store.getState().setPersona("learn");
    });
    render(<LearnPanel />);
    expect(screen.getByText("Start Tour")).toBeInTheDocument();
  });
});
