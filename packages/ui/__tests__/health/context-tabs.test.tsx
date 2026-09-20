/**
 * The "All" tab in ContextTabs.
 *
 * total (repository_total) and facets.context are counted over two
 * different scopes: the former never narrows, the latter is cross-filtered by
 * every other active selection. Badging "All" with the sum of the context
 * facet keeps the row internally consistent -- the number on the button a
 * user presses has to match what pressing it returns. The fallback to
 * total exists only for a server that predates context scoping and sends
 * no facets.context at all.
 */
import { beforeAll, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ContextTabs } from "../../src/health/performance/filters.js";
import type { PerformanceFacets } from "@repowise-dev/types/health";

// jsdom has no layout engine; ViewTabs' active-tab scroll is a no-op here.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

const scopedCounts = [
  { value: "production", total: 22 },
  { value: "tooling", total: 5 },
  { value: "test", total: 12 },
  { value: "unknown", total: 1 },
];

describe("ContextTabs -- the All badge", () => {
  it("equals the sum of the context facet when another filter has narrowed it", () => {
    render(
      <ContextTabs
        value="all"
        facets={{ context: scopedCounts } as unknown as PerformanceFacets}
        total={743}
        collapsed={false}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.queryByText("743")).not.toBeInTheDocument();
  });

  it("still adds tooling into production's badge when collapsed", () => {
    render(
      <ContextTabs
        value="all"
        facets={{ context: scopedCounts } as unknown as PerformanceFacets}
        total={743}
        collapsed
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("40")).toBeInTheDocument();
    expect(screen.getByText("Production & tooling")).toBeInTheDocument();
    expect(screen.getByText("27")).toBeInTheDocument();
  });

  it("falls back to repositoryTotal when the server sends no facets.context", () => {
    render(
      <ContextTabs
        value="all"
        facets={{} as unknown as PerformanceFacets}
        total={743}
        collapsed={false}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("743")).toBeInTheDocument();
  });

  it("reads zero, not the unscoped total, when the facet is present but empty", () => {
    render(
      <ContextTabs
        value="all"
        facets={{ context: [] } as unknown as PerformanceFacets}
        total={743}
        collapsed={false}
        onChange={() => {}}
      />,
    );
    expect(screen.getByRole("tab", { name: /All 0/ })).toBeInTheDocument();
    expect(screen.queryByText("743")).not.toBeInTheDocument();
  });
});
