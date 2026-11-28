// @vitest-environment jsdom

/**
 * The tree tab's wiring: which payload it reads, what it says when the payload
 * is capped, and that the stepped "load more" reaches the fetch rather than
 * only the caption.
 *
 * Deliberately mounted through the same `useGraph` hook the Map's file scope
 * uses, so a regression that gives the tree its own endpoint or its own key
 * fails here rather than quietly doubling the traffic.
 */

import * as React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GraphExportResponse } from "@/lib/api/types";

const mocks = vi.hoisted(() => ({
  useGraph: vi.fn(),
  mutate: vi.fn(),
}));

vi.mock("@/lib/hooks/use-graph", () => ({
  useGraph: mocks.useGraph,
}));

vi.mock("next/link", () => ({
  default: ({ children, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a {...props}>{children}</a>
  ),
}));

import { CodeGraphTreeView } from "./code-graph-tree-view";
import { LOAD_MORE_STEP } from "@repowise-dev/ui/graph/graph-truncation-banner";

afterEach(cleanup);

const node = (id: string) => ({
  node_id: id,
  node_type: "file",
  language: "typescript",
  symbol_count: 1,
  pagerank: 0,
  betweenness: 0,
  community_id: 0,
  is_test: false,
  is_entry_point: false,
  has_doc: false,
});

const payload: GraphExportResponse = {
  nodes: [node("src/app.ts"), node("src/util.ts")],
  links: [
    {
      source: "src/app.ts",
      target: "src/util.ts",
      imported_names: ["truncatePath"],
      edge_type: "imports",
    },
  ],
};

function hookResult(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    graph: undefined,
    error: undefined,
    isLoading: false,
    mutate: mocks.mutate,
    ...overrides,
  };
}

describe("CodeGraphTreeView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useGraph.mockReturnValue(hookResult());
  });

  it("reads the same graph hook as the file-scope canvas, with no limit on first load", () => {
    render(<CodeGraphTreeView repoId="r1" scope="tree" onScopeChange={vi.fn()} />);
    expect(mocks.useGraph).toHaveBeenCalledWith("r1", undefined);
  });

  it("shows a skeleton while the first payload is in flight", () => {
    mocks.useGraph.mockReturnValue(hookResult({ isLoading: true }));
    render(<CodeGraphTreeView repoId="r1" scope="tree" onScopeChange={vi.fn()} />);
    expect(screen.getByLabelText("Loading the code graph tree")).toBeTruthy();
    expect(screen.queryByText("No files to outline")).toBeNull();
  });

  it("opens a file in place and names the types it imports", () => {
    mocks.useGraph.mockReturnValue(hookResult({ graph: payload }));
    render(<CodeGraphTreeView repoId="r1" scope="tree" onScopeChange={vi.fn()} />);
    fireEvent.click(screen.getByText("src"));
    fireEvent.click(screen.getByText("app.ts"));
    expect(screen.getByText("truncatePath")).toBeTruthy();
    expect(screen.getByText("src/util.ts")).toBeTruthy();
  });

  it("states the cap and re-fetches with a stepped limit when more is asked for", () => {
    mocks.useGraph.mockReturnValue(
      hookResult({
        graph: {
          ...payload,
          truncated: true,
          total_node_count: 3194,
        },
      }),
    );
    render(<CodeGraphTreeView repoId="r1" scope="tree" onScopeChange={vi.fn()} />);
    // The server's own numbers, not a count of what rendered.
    expect(screen.getByText(/most-connected files of/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Load .* more/ }));
    // Before any step has been taken the in-effect cap is read off the payload,
    // which is the same contract the canvas's banner runs on: `limit` must be
    // the cap that produced the drawn nodes, not a constant. The fixture is two
    // nodes, so the next rung is one step above two.
    expect(mocks.useGraph).toHaveBeenLastCalledWith(
      "r1",
      LOAD_MORE_STEP + payload.nodes.length,
    );
  });

  it("drops the banner once the payload is whole", () => {
    mocks.useGraph.mockReturnValue(
      hookResult({ graph: { ...payload, truncated: false, total_node_count: 2 } }),
    );
    render(<CodeGraphTreeView repoId="r1" scope="tree" onScopeChange={vi.fn()} />);
    expect(screen.queryByText(/most-connected files of/)).toBeNull();
  });

  it("reports a failed payload with a retry that revalidates", () => {
    mocks.useGraph.mockReturnValue(hookResult({ error: new Error("boom") }));
    render(<CodeGraphTreeView repoId="r1" scope="tree" onScopeChange={vi.fn()} />);
    expect(screen.getByText("Couldn't load the code graph")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
    expect(mocks.mutate).toHaveBeenCalledOnce();
  });

  it("carries the scope switcher so the way back to the canvas is on this tab", () => {
    const onScopeChange = vi.fn();
    mocks.useGraph.mockReturnValue(hookResult({ graph: payload }));
    render(<CodeGraphTreeView repoId="r1" scope="tree" onScopeChange={onScopeChange} />);
    const tree = screen.getByRole("radio", { name: "Tree" });
    expect(tree.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("radio", { name: "Files" }));
    expect(onScopeChange).toHaveBeenCalledWith("files");
  });
});
