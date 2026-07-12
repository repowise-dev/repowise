import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { forwardRef, useImperativeHandle } from "react";
import { GraphFlow } from "../../src/graph/graph-flow.js";

// Stub the Sigma canvas: it dynamically imports "sigma", which needs WebGL2
// and rejects under jsdom. These tests assert toolbar / notice / picker
// behavior, none of which lives inside the canvas.
const { focusNodeSpy } = vi.hoisted(() => ({ focusNodeSpy: vi.fn() }));
vi.mock("../../src/graph/sigma/sigma-canvas.js", () => ({
  SigmaCanvas: forwardRef(function MockSigmaCanvas(_props, ref) {
    useImperativeHandle(ref, () => ({
      focusNode: focusNodeSpy,
      fitView: () => {},
      zoomIn: () => {},
      zoomOut: () => {},
    }));
    return <div data-testid="sigma-canvas" />;
  }),
}));

afterEach(() => {
  vi.useRealTimers();
  focusNodeSpy.mockClear();
});

// Minimal prop set — no graphs supplied, so the canvas renders its empty state
// while the toolbar (and its color-mode control) still mounts.
const baseProps = {
  moduleGraph: undefined,
  isLoadingModuleGraph: false,
  fullGraph: undefined,
  isLoadingFullGraph: false,
  architectureGraph: undefined,
  isLoadingArchitectureGraph: false,
  deadCodeGraph: undefined,
  isLoadingDeadCodeGraph: false,
  hotFilesGraph: undefined,
  isLoadingHotFilesGraph: false,
} as const;

describe("GraphFlow shell", () => {
  it("renders the empty state when no nodes are layouted", () => {
    render(<GraphFlow {...baseProps} />);
    expect(screen.getByText("No graph data")).toBeTruthy();
  });

  it("reflects a controlled colorMode and reports changes without self-updating", () => {
    const onColorModeChange = vi.fn();
    render(
      <GraphFlow
        {...baseProps}
        colorMode="risk"
        onColorModeChange={onColorModeChange}
      />,
    );

    // Controlled value wins: Risk is active, Community (the default) is not.
    expect(screen.getByRole("button", { name: "Risk" }).getAttribute("aria-pressed")).toBe("true");
    expect(
      screen.getByRole("button", { name: "Community" }).getAttribute("aria-pressed"),
    ).toBe("false");

    // Clicking another mode reports out but does NOT change the displayed mode —
    // the host owns the value and hasn't pushed a new prop yet.
    fireEvent.click(screen.getByRole("button", { name: "Language" }));
    expect(onColorModeChange).toHaveBeenCalledWith("language");
    expect(screen.getByRole("button", { name: "Risk" }).getAttribute("aria-pressed")).toBe("true");
    expect(
      screen.getByRole("button", { name: "Language" }).getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("tracks its own colorMode when uncontrolled (seeded by initialColorMode)", () => {
    render(<GraphFlow {...baseProps} initialColorMode="language" />);

    expect(
      screen.getByRole("button", { name: "Language" }).getAttribute("aria-pressed"),
    ).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: "Risk" }));
    expect(screen.getByRole("button", { name: "Risk" }).getAttribute("aria-pressed")).toBe("true");
    expect(
      screen.getByRole("button", { name: "Language" }).getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("offers an exclusive All / Hot / Dead node filter", () => {
    render(<GraphFlow {...baseProps} initialViewMode="full" />);

    expect(screen.getByRole("radio", { name: "All" }).getAttribute("aria-checked")).toBe("true");

    fireEvent.click(screen.getByRole("radio", { name: "Dead" }));
    expect(screen.getByRole("radio", { name: "Dead" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("radio", { name: "Hot" }).getAttribute("aria-checked")).toBe("false");
    expect(screen.getByRole("radio", { name: "All" }).getAttribute("aria-checked")).toBe("false");

    // Selecting Hot replaces Dead — the two can never be active together.
    fireEvent.click(screen.getByRole("radio", { name: "Hot" }));
    expect(screen.getByRole("radio", { name: "Hot" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("radio", { name: "Dead" }).getAttribute("aria-checked")).toBe("false");
  });

  it("says when dead files exist but fell outside the loaded view", () => {
    render(
      <GraphFlow
        {...baseProps}
        initialViewMode="full"
        fullGraph={{ nodes: [], links: [], truncated: true, dead_total: 3 }}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Dead" }));
    expect(screen.getByText("Dead files are outside the loaded view")).toBeTruthy();
  });

  it("says when the repo simply has no dead files", () => {
    render(
      <GraphFlow
        {...baseProps}
        initialViewMode="full"
        fullGraph={{ nodes: [], links: [], dead_total: 0 }}
      />,
    );
    fireEvent.click(screen.getByRole("radio", { name: "Dead" }));
    expect(screen.getByText("No dead files in this repo")).toBeTruthy();
  });

  it("opens the flow picker on the module overview and jumps to the full graph on selection", () => {
    const onViewModeChange = vi.fn();
    render(
      <GraphFlow
        {...baseProps}
        initialViewMode="module"
        onViewModeChange={onViewModeChange}
        executionFlows={{
          total_entry_points: 1,
          flows: [
            {
              entry_point: "app.py::main",
              entry_point_name: "main",
              entry_point_score: 1,
              trace: ["app.py::main", "core.py::run"],
              depth: 1,
              crosses_community: false,
              communities_visited: [0],
            },
          ],
        }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Execution flows" }));
    expect(screen.getByText("Execution Flows")).toBeTruthy();

    // Selecting a flow from the module overview switches to the file-level
    // graph so the trace can actually be highlighted.
    fireEvent.click(screen.getByText("main"));
    expect(onViewModeChange).toHaveBeenCalledWith("full");
  });

  it("focuses the flow trace head once the graph gains it, exactly once", () => {
    vi.useFakeTimers();
    const flows = {
      total_entry_points: 1,
      flows: [
        {
          entry_point: "app.py::main",
          entry_point_name: "main",
          entry_point_score: 1,
          trace: ["app.py::main", "core.py::run"],
          depth: 1,
          crosses_community: false,
          communities_visited: [0],
        },
      ],
    };
    const { rerender } = render(
      <GraphFlow {...baseProps} initialViewMode="full" executionFlows={flows} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Execution flows" }));
    fireEvent.click(screen.getByText("main"));

    // The focus timer fires while the full graph is still loading — the
    // trace head isn't in the (empty) graph yet, so nothing is focused.
    act(() => {
      vi.advanceTimersByTime(800);
    });
    expect(focusNodeSpy).not.toHaveBeenCalled();

    // The full graph lands: the deferred focus fires once for the trace head.
    const nodes = flows.flows[0]!.trace.map((id) => ({
      node_id: id,
      label: id,
      language: "python",
    }));
    rerender(
      <GraphFlow
        {...baseProps}
        initialViewMode="full"
        executionFlows={flows}
        fullGraph={{ nodes, links: [] }}
      />,
    );
    expect(focusNodeSpy).toHaveBeenCalledWith("app.py::main");
    expect(focusNodeSpy).toHaveBeenCalledTimes(1);

    // Later graph changes must not re-steer the camera for the same flow.
    rerender(
      <GraphFlow
        {...baseProps}
        initialViewMode="full"
        executionFlows={flows}
        fullGraph={{ nodes: [...nodes], links: [] }}
      />,
    );
    expect(focusNodeSpy).toHaveBeenCalledTimes(1);
  });

  it("refuses hierarchical layout above the ELK cap and explains why", () => {
    const nodes = Array.from({ length: 501 }, (_, i) => ({
      node_id: `f${i}.ts`,
      label: `f${i}.ts`,
      language: "typescript",
    }));
    render(
      <GraphFlow
        {...baseProps}
        initialViewMode="full"
        fullGraph={{ nodes, links: [] }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Hierarchical" }));

    // The mode must not activate — force stays on — and the notice says why.
    expect(
      screen.getByRole("button", { name: "Hierarchical" }).getAttribute("aria-pressed"),
    ).toBe("false");
    expect(screen.getByRole("status").textContent).toContain(
      "Hierarchical layout is limited to 500 nodes",
    );
  });
});
