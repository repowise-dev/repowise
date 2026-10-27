import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { GraphLegend } from "../../src/graph/graph-legend";

const base = {
  nodeCount: 94,
  edgeCount: 553,
  colorMode: "community" as const,
  viewMode: "full" as const,
};

describe("GraphLegend inside a community", () => {
  const scoped = {
    ...base,
    scopeLabel: "repowise/extractors",
    scopeCommunityId: 4,
    sliceCounts: { members: 54, boundary: 40 },
    visibleEdgeCount: 312,
    visibleEdgeTypes: new Set(["crossCommunity", "internal"]),
    onEdgeTypeToggle: vi.fn(),
  };

  it("keys the community that is drawn, not eight the API happened to list first", () => {
    // The whole-repo key sourced its swatches from `useCommunities`, in API
    // order, so the community you drilled into was usually absent from its own
    // view.
    render(
      <GraphLegend
        {...scoped}
        communityLabels={new Map([[1, "packages/web"], [2, "packages/cli"]])}
      />,
    );
    expect(screen.getByText("repowise/extractors")).toBeTruthy();
    expect(screen.queryByText("packages/web")).toBeNull();
  });

  it("drops the whole-repo community filter, which dimmed the slice you came to read", () => {
    // `Deselect all` ran over every drawn node, so one click blanked the
    // subject; and toggling a community that is not on the canvas changed
    // nothing while leaving its swatch filled.
    render(
      <GraphLegend
        {...scoped}
        onToggleAllCommunities={vi.fn()}
        onCommunityToggle={vi.fn()}
        communityLabels={new Map([[1, "packages/web"]])}
      />,
    );
    expect(screen.queryByText("Deselect all")).toBeNull();
    expect(screen.queryByText("Select all")).toBeNull();
  });

  it("counts the members, the ring and the edges actually drawn", () => {
    render(<GraphLegend {...scoped} />);
    // Not "94 nodes · 553 edges": that count folded the boundary stubs into
    // the file count and included the edges the filter was hiding.
    expect(
      screen.getByText(/54 files · 40 outside · 312 edges shown/),
    ).toBeTruthy();
  });

  it("gives the faded ring a row, instead of explaining it only in prose", () => {
    render(<GraphLegend {...scoped} />);
    expect(screen.getByText("Outside this group")).toBeTruthy();
  });

  it("words the edge kinds for the scope it is describing", () => {
    render(<GraphLegend {...scoped} />);
    expect(screen.getByText("Within this group")).toBeTruthy();
    expect(screen.getByText("Leaving this group")).toBeTruthy();
    expect(screen.queryByText("Cross-community")).toBeNull();
  });
});

describe("GraphLegend at repo scope", () => {
  it("keys no edge kind that is never drawn", () => {
    // `classifyEdge` returned "import" only for an edge whose endpoint was
    // missing from the graph, and the adapter drops those before drawing.
    render(
      <GraphLegend
        {...base}
        visibleEdgeTypes={new Set(["crossCommunity", "internal"])}
        onEdgeTypeToggle={vi.fn()}
      />,
    );
    expect(screen.queryByText("Imports")).toBeNull();
    expect(screen.getByText("Cross-community")).toBeTruthy();
  });

  it("carries the node filter beside the count that filter changes", () => {
    render(
      <GraphLegend {...base} nodeFilter={<button type="button">All</button>} />,
    );
    expect(screen.getByRole("button", { name: "All" })).toBeTruthy();
  });

  it("reports the edges it can see rather than the ones it holds", () => {
    render(<GraphLegend {...base} visibleEdgeCount={312} />);
    expect(screen.getByText(/94 nodes · 312 edges/)).toBeTruthy();
  });
});

describe("GraphLegend before the canvas has anything to key", () => {
  it("offers no Deselect all when no listed community is drawn", () => {
    // `communityLabels` is the repo-wide summary and resolves before an async
    // graph build finishes. Filtering rows by what is drawn made the list
    // empty — and `[]` is truthy, so the group rendered its label and a lone
    // "Deselect all" over zero swatches. Clicking it dimmed every node while
    // `[].every(...)` kept the label reading "Deselect all".
    render(
      <GraphLegend
        {...base}
        communityLabels={new Map([[1, "packages/web"]])}
        drawnCommunityIds={new Set()}
        onToggleAllCommunities={vi.fn()}
        onCommunityToggle={vi.fn()}
      />,
    );
    expect(screen.queryByText("Deselect all")).toBeNull();
    expect(screen.queryByText("packages/web")).toBeNull();
    expect(screen.queryByText("Community")).toBeNull();
  });

  it("keys only the communities the canvas is drawing", () => {
    render(
      <GraphLegend
        {...base}
        communityLabels={new Map([[1, "packages/web"], [2, "packages/cli"]])}
        drawnCommunityIds={new Set([2])}
        onCommunityToggle={vi.fn()}
      />,
    );
    expect(screen.getByText("packages/cli")).toBeTruthy();
    expect(screen.queryByText("packages/web")).toBeNull();
  });
});

