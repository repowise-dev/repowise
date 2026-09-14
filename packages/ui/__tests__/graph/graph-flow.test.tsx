import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { forwardRef, useImperativeHandle } from "react";
import { GraphFlow } from "../../src/graph/graph-flow.js";

// Stub the Sigma canvas: it dynamically imports "sigma", which needs WebGL2
// and rejects under jsdom. These tests assert toolbar / notice / picker
// behavior, none of which lives inside the canvas.
const { focusNodeSpy, nodeCameraSpy, setEntryCameraSpy } = vi.hoisted(() => ({
  focusNodeSpy: vi.fn(),
  nodeCameraSpy: vi.fn(() => ({ x: 0.4, y: 0.6, ratio: 0.08 })),
  setEntryCameraSpy: vi.fn(),
}));
vi.mock("../../src/graph/sigma/sigma-canvas.js", () => ({
  SigmaCanvas: forwardRef(function MockSigmaCanvas(_props, ref) {
    useImperativeHandle(ref, () => ({
      focusNode: focusNodeSpy,
      fitView: () => {},
      zoomIn: () => {},
      zoomOut: () => {},
      nodeCamera: nodeCameraSpy,
      setEntryCamera: setEntryCameraSpy,
    }));
    return <div data-testid="sigma-canvas" />;
  }),
}));

afterEach(() => {
  vi.useRealTimers();
  focusNodeSpy.mockClear();
  nodeCameraSpy.mockClear();
  setEntryCameraSpy.mockClear();
});

// Fixture file node carrying every required GraphNode field.
const fileNode = (id: string, language: string) => ({
  node_id: id,
  node_type: "file",
  language,
  symbol_count: 1,
  pagerank: 0,
  betweenness: 0,
  community_id: 0,
  is_test: false,
  is_entry_point: false,
  has_doc: false,
});

// Minimal prop set — no graphs supplied, so the canvas renders its empty state
// while the toolbar (and its color-mode control) still mounts.
const baseProps = {
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

  // Uses "language" as the controlled value because it is the one that is not
  // the default. Rendered in the file scope: the constellation colours hubs by
  // family whatever colorMode says, so the control is not offered there.
  it("reflects a controlled colorMode and reports changes without self-updating", () => {
    const onColorModeChange = vi.fn();
    render(
      <GraphFlow
        {...baseProps}
        initialViewMode="full"
        colorMode="language"
        onColorModeChange={onColorModeChange}
      />,
    );

    // Controlled value wins: Language is active, Community (the default) is not.
    expect(screen.getByRole("button", { name: "Language" }).getAttribute("aria-pressed")).toBe("true");
    expect(
      screen.getByRole("button", { name: "Community" }).getAttribute("aria-pressed"),
    ).toBe("false");

    // Clicking another mode reports out but does NOT change the displayed mode —
    // the host owns the value and hasn't pushed a new prop yet.
    fireEvent.click(screen.getByRole("button", { name: "Community" }));
    expect(onColorModeChange).toHaveBeenCalledWith("community");
    expect(screen.getByRole("button", { name: "Language" }).getAttribute("aria-pressed")).toBe("true");
    expect(
      screen.getByRole("button", { name: "Community" }).getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("tracks its own colorMode when uncontrolled (seeded by initialColorMode)", () => {
    render(
      <GraphFlow {...baseProps} initialViewMode="full" initialColorMode="language" />,
    );

    expect(
      screen.getByRole("button", { name: "Language" }).getAttribute("aria-pressed"),
    ).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: "Community" }));
    expect(screen.getByRole("button", { name: "Community" }).getAttribute("aria-pressed")).toBe("true");
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
    // The segment carries its own total now, so the accessible name is
    // "Dead 3" rather than "Dead".
    fireEvent.click(screen.getByRole("radio", { name: /^Dead/ }));
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
    fireEvent.click(screen.getByRole("radio", { name: /^Dead/ }));
    expect(screen.getByText("No dead files in this repo")).toBeTruthy();
  });

  it("highlights the FILES an execution flow runs through, not its symbols", () => {
    // `calls` edges only ever join symbol nodes, so a trace is a list of
    // `file::symbol` ids while this canvas draws files. The trace head focused
    // must therefore be the containing file, or the picker silently does
    // nothing on every repo.
    vi.useFakeTimers();
    render(
      <GraphFlow
        {...baseProps}
        initialViewMode="full"
        fullGraph={{
          nodes: [fileNode("app.py", "python"), fileNode("core.py", "python")],
          links: [],
        }}
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

    fireEvent.click(screen.getByRole("button", { name: "Trace" }));
    fireEvent.click(screen.getByRole("button", { name: "Execution flows" }));
    expect(screen.getByText("Execution Flows")).toBeTruthy();

    fireEvent.click(screen.getByText("main"));
    act(() => {
      vi.advanceTimersByTime(900);
    });
    expect(focusNodeSpy).toHaveBeenCalledWith("app.py");
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

    fireEvent.click(screen.getByRole("button", { name: "Trace" }));
    fireEvent.click(screen.getByRole("button", { name: "Execution flows" }));
    fireEvent.click(screen.getByText("main"));

    // The focus timer fires while the full graph is still loading — the
    // trace head isn't in the (empty) graph yet, so nothing is focused.
    act(() => {
      vi.advanceTimersByTime(800);
    });
    expect(focusNodeSpy).not.toHaveBeenCalled();

    // The full graph lands: the deferred focus fires once for the trace head's
    // containing FILE (the trace itself names symbols, which this canvas has
    // no nodes for).
    const nodes = flows.flows[0]!.trace.map((id) =>
      fileNode(id.split("::")[0]!, "python"),
    );
    rerender(
      <GraphFlow
        {...baseProps}
        initialViewMode="full"
        executionFlows={flows}
        fullGraph={{ nodes, links: [] }}
      />,
    );
    expect(focusNodeSpy).toHaveBeenCalledWith("app.py");
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

  it("shows hierarchical layout as unavailable above the ELK cap, with the reason", () => {
    const nodes = Array.from({ length: 501 }, (_, i) =>
      fileNode(`f${i}.ts`, "typescript"),
    );
    render(
      <GraphFlow
        {...baseProps}
        initialViewMode="full"
        fullGraph={{ nodes, links: [] }}
      />,
    );

    const button = screen.getByRole("button", { name: "Hierarchical" });

    // Unavailable up front rather than live-then-refusing. ELK's 500-node cap
    // sits below the graph loader's 1,500-node floor, and "load more" only
    // raises it — so on any repo past the cap this control could never act,
    // and used to say so only after you pressed it.
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(button.getAttribute("title")).toContain(
      "Hierarchical layout needs 500 nodes or fewer",
    );

    // The reason must not offer a remedy that cannot work. The module filter,
    // the community filter and search all dim rather than remove, so none of
    // them changes `graph.order`, which is the number this cap is measured
    // against — an earlier version told the reader to use exactly those.
    expect(button.getAttribute("title")).not.toMatch(
      /module filter|Modules scope|narrow the view/i,
    );

    // Clicking a disabled control changes nothing.
    fireEvent.click(button);
    expect(button.getAttribute("aria-pressed")).toBe("false");
  });

  it("leaves hierarchical layout available when the graph fits under the cap", () => {
    const nodes = Array.from({ length: 40 }, (_, i) =>
      fileNode(`f${i}.ts`, "typescript"),
    );
    render(
      <GraphFlow
        {...baseProps}
        initialViewMode="full"
        fullGraph={{ nodes, links: [] }}
      />,
    );

    const button = screen.getByRole("button", { name: "Hierarchical" });
    expect(button.hasAttribute("disabled")).toBe(false);
    fireEvent.click(button);
    expect(button.getAttribute("aria-pressed")).toBe("true");
  });
});

describe("GraphFlow drill-down", () => {
  const sliceNode = (id: string, isBoundary = false) => ({
    ...fileNode(id, "typescript"),
    community_id: 3,
    ...(isBoundary ? { is_boundary: true } : {}),
  });

  const slice = {
    community_id: 3,
    member_count: 2,
    nodes: [sliceNode("src/a.ts"), sliceNode("src/b.ts"), sliceNode("vendor/z.ts", true)],
    links: [{ source: "src/a.ts", target: "src/b.ts", imported_names: ["x"] }],
  };

  const communities = [
    { community_id: 3, label: "auth-cluster", cohesion: 0.4, member_count: 2, top_file: "src/a.ts" },
  ];

  it("draws the entered community's own slice instead of the capped full graph", () => {
    render(
      <GraphFlow
        {...baseProps}
        viewMode="full"
        activeCommunity={3}
        communitySlice={slice as never}
        communities={communities as never}
        fullGraph={{ nodes: [fileNode("other/far.ts", "python")], links: [] } as never}
      />,
    );
    // The slice rendered, so the canvas is not the empty state and the
    // breadcrumb names where we are.
    expect(screen.getByTestId("sigma-canvas")).toBeTruthy();
    const crumbs = screen.getByLabelText("Graph location");
    expect(crumbs.textContent).toContain("auth-cluster");
  });

  it("reports leaving the community when the breadcrumb root is clicked", () => {
    const onActiveCommunityChange = vi.fn();
    const onViewModeChange = vi.fn();
    render(
      <GraphFlow
        {...baseProps}
        viewMode="full"
        activeCommunity={3}
        communitySlice={slice as never}
        communities={communities as never}
        repoName="repowise"
        onActiveCommunityChange={onActiveCommunityChange}
        onViewModeChange={onViewModeChange}
      />,
    );
    fireEvent.click(screen.getByTitle("Back to repowise"));
    expect(onActiveCommunityChange).toHaveBeenCalledWith(null);
    // Up from a community is the constellation it came from, not "all files".
    expect(onViewModeChange).toHaveBeenCalledWith("architecture");
  });

  it("shows no breadcrumb when nothing has been drilled into", () => {
    render(
      <GraphFlow
        {...baseProps}
        viewMode="full"
        activeCommunity={null}
        communities={communities as never}
        fullGraph={{ nodes: [fileNode("src/a.ts", "typescript")], links: [] } as never}
      />,
    );
    expect(screen.queryByLabelText("Graph location")).toBeNull();
  });

  it("ignores a community carried into the constellation scope", () => {
    // `?community=` only means something on a file-level scope; half-applying
    // it would draw a slice under a "Communities" switcher.
    render(
      <GraphFlow
        {...baseProps}
        viewMode="architecture"
        activeCommunity={3}
        communitySlice={slice as never}
        communities={communities as never}
      />,
    );
    expect(screen.queryByLabelText("Graph location")).toBeNull();
  });
});

describe("GraphFlow module filter", () => {
  const nodes = [
    fileNode("packages/ui/src/a.ts", "typescript"),
    fileNode("packages/ui/src/b.ts", "typescript"),
    fileNode("packages/core/src/c.py", "python"),
  ];

  it("reports every module group, not only the selected one", () => {
    const onModuleGroupsChange = vi.fn();
    render(
      <GraphFlow
        {...baseProps}
        viewMode="full"
        activeModule="packages/ui"
        onModuleGroupsChange={onModuleGroupsChange}
        fullGraph={{ nodes, links: [] } as never}
      />,
    );
    // Derived from the unfiltered payload: selecting a module must not collapse
    // the menu you selected it from.
    const groups = onModuleGroupsChange.mock.calls.at(-1)?.[0] as { id: string }[];
    expect(groups.map((g) => g.id).sort()).toEqual(["packages/core", "packages/ui"]);
  });
});

describe("GraphFlow drill-down honesty", () => {
  const member = (id: string) => ({ ...fileNode(id, "typescript"), community_id: 3 });
  const stub = (id: string) => ({ ...member(id), is_boundary: true });

  const communities = [
    { community_id: 3, label: "auth-cluster", cohesion: 0.4, member_count: 500, top_file: "src/a.ts" },
  ];

  it("says so when the slice is capped, and counts the stubs separately", () => {
    render(
      <GraphFlow
        {...baseProps}
        viewMode="full"
        activeCommunity={3}
        communities={communities as never}
        communitySlice={{
          community_id: 3,
          member_count: 500,
          truncated: true,
          nodes: [member("src/a.ts"), member("src/b.ts"), stub("vendor/z.ts")],
          links: [],
        } as never}
      />,
    );
    // "The files in auth-cluster" over 2 of 500 would be the lie; the repo-wide
    // truncation banner is deliberately off in this scope.
    expect(
      screen.getByText(
        "Showing the 2 most connected of 500 files in this group, plus 1 faded file outside it that they reach.",
      ),
    ).toBeTruthy();
  });

  it("withdraws the All / Hot / Dead filter, which could not reach a slice", () => {
    // The slice endpoint returns the whole community whatever the signal says,
    // so inside one the pill lit, the URL changed, an overlay fetch fired and
    // the canvas did not move.
    render(
      <GraphFlow
        {...baseProps}
        viewMode="full"
        activeCommunity={3}
        communities={communities as never}
        communitySlice={{
          community_id: 3,
          member_count: 2,
          truncated: false,
          nodes: [member("src/a.ts"), member("src/b.ts")],
          links: [],
        } as never}
      />,
    );
    expect(screen.queryByRole("radio", { name: /^Dead/ })).toBeNull();
    expect(screen.queryByRole("radio", { name: /^Hot/ })).toBeNull();
  });

  it("names the community once in prose, not three times on one screen", () => {
    render(
      <GraphFlow
        {...baseProps}
        viewMode="full"
        activeCommunity={3}
        communities={communities as never}
        communitySlice={{
          community_id: 3,
          member_count: 2,
          truncated: false,
          nodes: [member("src/a.ts"), member("src/b.ts")],
          links: [],
        } as never}
      />,
    );
    // The breadcrumb names it and carries the way out; the description used to
    // repeat the name and then explain the faded ring a second time, beside a
    // banner already counting it. The double-click instruction is withheld
    // because this host wired no `onNodeViewDocs` for it to reach.
    expect(
      screen.getByText("How the files in this group depend on each other."),
    ).toBeTruthy();
  });

  it("only promises the double-click when the host wired somewhere to go", () => {
    render(
      <GraphFlow
        {...baseProps}
        viewMode="full"
        activeCommunity={3}
        communities={communities as never}
        onNodeViewDocs={vi.fn()}
        communitySlice={{
          community_id: 3,
          member_count: 2,
          truncated: false,
          nodes: [member("src/a.ts"), member("src/b.ts")],
          links: [],
        } as never}
      />,
    );
    expect(
      screen.getByText(
        "How the files in this group depend on each other. Double-click a file to open it.",
      ),
    ).toBeTruthy();
  });

  it("does not claim truncation when the whole community is drawn", () => {
    render(
      <GraphFlow
        {...baseProps}
        viewMode="full"
        activeCommunity={3}
        communities={communities as never}
        communitySlice={{
          community_id: 3,
          member_count: 2,
          truncated: false,
          nodes: [member("src/a.ts"), member("src/b.ts")],
          links: [],
        } as never}
      />,
    );
    expect(screen.getByText("Showing all 2 files in this group.")).toBeTruthy();
  });

  it("drops the held frame when the slice fetch fails instead of pinning it", () => {
    // SWR leaves `data` undefined and `isLoading` false between retries. Holding
    // on "no slice yet" alone left the constellation drawn under a breadcrumb
    // asserting a scoped file graph, with no error and no empty state.
    const { rerender } = render(
      <GraphFlow
        {...baseProps}
        viewMode="architecture"
        constellationGraph={
          { nodes: [{ community_id: 3, label: "auth-cluster", member_count: 2, avg_pagerank: 0.1 }], edges: [] } as never
        }
        communities={communities as never}
      />,
    );
    rerender(
      <GraphFlow
        {...baseProps}
        viewMode="full"
        activeCommunity={3}
        communitySlice={undefined}
        isLoadingCommunitySlice={false}
        communities={communities as never}
      />,
    );
    expect(screen.getByText("No graph data")).toBeTruthy();
  });

  it("drops the dead/hot captions inside a community the signal never filtered", () => {
    render(
      <GraphFlow
        {...baseProps}
        viewMode="dead"
        activeCommunity={3}
        communities={communities as never}
        deadCodeGraph={{ nodes: [], links: [], dead_total: 9 } as never}
        communitySlice={{
          community_id: 3,
          member_count: 2,
          truncated: false,
          nodes: [member("src/a.ts"), member("src/b.ts")],
          links: [],
        } as never}
      />,
    );
    expect(screen.queryByText(/dead files/)).toBeNull();
  });
});

describe("GraphFlow hook order", () => {
  it("keeps the same hooks on the loading pass and the loaded one", () => {
    // The loading branch returns a skeleton before the render body, so a hook
    // declared below it does not run on that pass. React then sees the hook
    // count change on the next render and takes the whole canvas down with
    // "change in the order of Hooks called by GraphFlow" — recoverable only by
    // a page refresh.
    const errors: unknown[][] = [];
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((...args: unknown[]) => {
        errors.push(args);
      });
    try {
      const { rerender } = render(
        <GraphFlow {...baseProps} viewMode="full" isLoadingFullGraph={true} />,
      );
      rerender(
        <GraphFlow
          {...baseProps}
          viewMode="full"
          isLoadingFullGraph={false}
          fullGraph={
            {
              nodes: [fileNode("src/a.ts", "typescript")],
              links: [],
            } as never
          }
        />,
      );
      const hookComplaint = errors.find((e) =>
        String(e[0] ?? "").includes("order of Hooks"),
      );
      expect(hookComplaint).toBeUndefined();
    } finally {
      spy.mockRestore();
    }
  });
});

