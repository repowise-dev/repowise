import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { useArchitectureStore } from "../../src/c4/store/use-architecture-store";
import { CodeViewer, getLanguageFromPath } from "../../src/c4/panels/CodeViewer";
import { createMockView } from "./fixtures";

const base = createMockView();
const mockView = createMockView({
  nodes: base.nodes.map((n) =>
    n.id === "src/routes.py" ? { ...n, line_range: [10, 25] } : n,
  ),
});

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
