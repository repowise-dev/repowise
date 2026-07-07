import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FileGraphTab } from "../../src/files/file-graph-tab.js";
import type { FileDetailGraph, FileGraphNeighbor } from "@repowise-dev/types/files";

function makeNeighbor(overrides: Partial<FileGraphNeighbor> = {}): FileGraphNeighbor {
  return {
    node_id: "src/app.ts",
    node_type: "file",
    language: "typescript",
    edge_type: "imports",
    imported_names: [],
    ...overrides,
  };
}

function makeGraph(overrides: Partial<FileDetailGraph> = {}): FileDetailGraph {
  return {
    language: "typescript",
    is_entry_point: false,
    is_test: false,
    symbol_count: 3,
    pagerank: 0.001,
    pagerank_percentile: 42,
    in_degree: 1,
    out_degree: 1,
    community_id: 0,
    community_label: null,
    dependents: [],
    dependencies: [],
    ...overrides,
  };
}

const fileHref = (path: string) => `/files/${path}`;
const symbolHref = (symbolId: string) => `/symbols/${symbolId}`;

describe("FileGraphTab", () => {
  it("links a regular file neighbor to its file page", () => {
    const graph = makeGraph({
      dependencies: [makeNeighbor({ node_id: "src/util.ts" })],
    });
    render(
      <FileGraphTab
        graph={graph}
        filePath="src/app.ts"
        linkPrefix="/repos/1"
        fileHref={fileHref}
        symbolHref={symbolHref}
      />,
    );
    const link = screen.getByText("src/util.ts").closest("a");
    expect(link).not.toBeNull();
    expect(link?.getAttribute("href")).toBe("/files/src/util.ts");
  });

  it("does not linkify an external-system neighbor as a file", () => {
    const graph = makeGraph({
      dependencies: [makeNeighbor({ node_id: "external:fastapi", node_type: "file" })],
    });
    render(
      <FileGraphTab
        graph={graph}
        filePath="src/app.ts"
        linkPrefix="/repos/1"
        fileHref={fileHref}
        symbolHref={symbolHref}
      />,
    );
    const label = screen.getByText("external:fastapi");
    expect(label.closest("a")).toBeNull();
    expect(label.getAttribute("title")).toContain("external dependency");
  });

  it("still links a symbol neighbor to its symbol page", () => {
    const graph = makeGraph({
      dependencies: [makeNeighbor({ node_id: "src/app.ts::handler", node_type: "symbol" })],
    });
    render(
      <FileGraphTab
        graph={graph}
        filePath="src/app.ts"
        linkPrefix="/repos/1"
        fileHref={fileHref}
        symbolHref={symbolHref}
      />,
    );
    const link = screen.getByText("src/app.ts::handler").closest("a");
    expect(link?.getAttribute("href")).toBe("/symbols/src/app.ts::handler");
  });
});
