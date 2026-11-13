import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import {
  CodeGraphTree,
  buildCodeGraphTree,
  collectDirectoryIds,
  summarizeFileImports,
} from "../../src/graph/code-graph-tree.js";
import type { GraphExport, GraphNode, GraphLink } from "@repowise-dev/types/graph";

function node(id: string, overrides: Partial<GraphNode> = {}): GraphNode {
  return {
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
    ...overrides,
  };
}

function link(
  source: string,
  target: string,
  imported_names: string[],
  edge_type: string | undefined = "imports",
): GraphLink {
  return edge_type === undefined
    ? { source, target, imported_names }
    : { source, target, imported_names, edge_type };
}

// A payload with a directory, a root-level file, an external target, and a
// co_changes edge that must not be mistaken for an import.
const payload: GraphExport = {
  nodes: [
    node("src/app.ts"),
    node("src/util/format.ts"),
    node("src/util/parse.ts", { is_test: true }),
    node("README.md"),
    node("external:react", { node_type: "external" }),
  ],
  links: [
    link("src/app.ts", "src/util/format.ts", ["formatNumber", "truncatePath"]),
    link("src/app.ts", "external:react", ["useState"]),
    link("src/util/parse.ts", "src/util/format.ts", ["formatNumber"]),
    link("src/app.ts", "README.md", [], "co_changes"),
  ],
};

describe("buildCodeGraphTree", () => {
  it("nests files under their directories and sorts directories first", () => {
    const tree = buildCodeGraphTree(payload.nodes, payload.links);
    expect(tree.map((t) => t.name)).toEqual(["src", "README.md"]);
    const src = tree[0]!;
    expect(src.fileCount).toBe(3);
    // `src/app.ts` is a file; `src/util` is a directory, so the directory leads.
    expect(src.children.map((c) => c.name)).toEqual(["util", "app.ts"]);
    expect(src.children[0]!.children.map((c) => c.name)).toEqual(["format.ts", "parse.ts"]);
  });

  it("drops external nodes as rows but keeps them as import targets", () => {
    const tree = buildCodeGraphTree(payload.nodes, payload.links);
    const ids = collectDirectoryIds(tree);
    expect(ids).toEqual(["src", "src/util"]);
    const flat = JSON.stringify(tree);
    expect(flat).not.toContain('"id":"external:react"');
    expect(flat).toContain("external:react");
  });

  it("attaches each file's imported names, sorted, and ignores co_changes", () => {
    const tree = buildCodeGraphTree(payload.nodes, payload.links);
    const src = tree[0]!;
    const app = src.children.find((c) => c.name === "app.ts")!;
    expect(app.file?.imports.map((i) => i.target)).toEqual([
      "external:react",
      "src/util/format.ts",
    ]);
    const format = app.file!.imports.find((i) => i.target === "src/util/format.ts")!;
    expect(format.names).toEqual(["formatNumber", "truncatePath"]);
    // `co_changes` is history, not code: it must not appear as a dependency,
    // and the file's own list is empty, not a one-row list of README.md.
    expect(app.file?.imports.some((i) => i.target === "README.md")).toBe(false);
  });

  it("summarizes counts over distinct names across edges", () => {
    const tree = buildCodeGraphTree(payload.nodes, payload.links);
    const src = tree[0]!;
    const app = src.children.find((c) => c.name === "app.ts")!;
    expect(summarizeFileImports(app.file!.imports)).toEqual({ fileCount: 2, nameCount: 3 });
  });

  it("merges parallel edges between the same pair instead of listing the target twice", () => {
    const graph: GraphExport = {
      nodes: [node("a.ts"), node("b.ts")],
      links: [link("a.ts", "b.ts", ["one"]), link("a.ts", "b.ts", ["two"])],
    };
    const tree = buildCodeGraphTree(graph.nodes, graph.links);
    const a = tree.find((t) => t.name === "a.ts")!;
    expect(a.file?.imports).toHaveLength(1);
    expect(a.file?.imports[0]!.names).toEqual(["one", "two"]);
  });

  it("admits an edge with no edge_type and skips an edge whose endpoint is not a node", () => {
    const graph: GraphExport = {
      nodes: [node("a.ts"), node("b.ts")],
      links: [
        { source: "a.ts", target: "b.ts", imported_names: ["x"] },
        link("a.ts", "gone.ts", ["y"]),
      ],
    };
    const tree = buildCodeGraphTree(graph.nodes, graph.links);
    const a = tree.find((t) => t.name === "a.ts")!;
    expect(a.file?.imports.map((i) => i.target)).toEqual(["b.ts"]);
    expect(a.file?.imports[0]!.edge_type).toBeNull();
  });
});

describe("CodeGraphTree", () => {
  it("keeps every directory shut until it is opened", () => {
    render(<CodeGraphTree graph={payload} />);
    expect(screen.queryByText("format.ts")).toBeNull();
    fireEvent.click(screen.getByText("src"));
    expect(screen.queryByText("util")).not.toBeNull();
    // One rung at a time: opening `src` does not open `src/util`.
    expect(screen.queryByText("format.ts")).toBeNull();
  });

  it("shows a file's imported types in place when the file row is opened", () => {
    render(<CodeGraphTree graph={payload} />);
    fireEvent.click(screen.getByText("src"));
    fireEvent.click(screen.getByText("app.ts"));
    expect(screen.getByText("src/util/format.ts")).toBeTruthy();
    expect(screen.getByText("formatNumber, truncatePath")).toBeTruthy();
    // The counts sentence is on the row, before it is opened or not.
    expect(screen.getByText("useState")).toBeTruthy();
  });

  it("collapses a row that is already open", () => {
    render(<CodeGraphTree graph={payload} />);
    fireEvent.click(screen.getByText("src"));
    expect(screen.queryByText("app.ts")).not.toBeNull();
    fireEvent.click(screen.getByText("src"));
    expect(screen.queryByText("app.ts")).toBeNull();
  });

  it("keeps imported names off the screen while a file row is shut", () => {
    render(<CodeGraphTree graph={payload} />);
    fireEvent.click(screen.getByText("src"));
    expect(screen.queryByText("formatNumber, truncatePath")).toBeNull();
    fireEvent.click(screen.getByText("app.ts"));
    expect(screen.queryByText("formatNumber, truncatePath")).not.toBeNull();
    fireEvent.click(screen.getByText("app.ts"));
    expect(screen.queryByText("formatNumber, truncatePath")).toBeNull();
  });

  it("links a file row and its import targets through fileHref, not external ones", () => {
    const fileHref = (path: string) => `/repos/1/files/${path}`;
    render(<CodeGraphTree graph={payload} fileHref={fileHref} />);
    fireEvent.click(screen.getByText("src"));
    const row = screen.getByText("app.ts").closest("div")!;
    const fileLink = within(row).getByLabelText("Open src/app.ts");
    expect(fileLink.getAttribute("href")).toBe("/repos/1/files/src/app.ts");

    fireEvent.click(screen.getByText("app.ts"));
    // A real target is linkable; `external:react` has no page behind it.
    const targetLink = screen.getByLabelText("Open src/util/format.ts");
    expect(targetLink.getAttribute("href")).toBe("/repos/1/files/src/util/format.ts");
    expect(screen.queryByLabelText("Open external:react")).toBeNull();
  });

  it("expands and collapses every directory from the two controls", () => {
    render(<CodeGraphTree graph={payload} />);
    fireEvent.click(screen.getByText("Expand all"));
    expect(screen.queryByText("format.ts")).not.toBeNull();
    fireEvent.click(screen.getByText("Collapse all"));
    expect(screen.queryByText("format.ts")).toBeNull();
  });

  it("says it is loading rather than reporting an empty graph", () => {
    render(<CodeGraphTree graph={undefined} isLoading />);
    expect(screen.getByLabelText("Loading the code graph tree")).toBeTruthy();
    expect(screen.queryByText("No files to outline")).toBeNull();
  });

  it("reports an empty payload as an empty state, not as a blank panel", () => {
    render(<CodeGraphTree graph={{ nodes: [], links: [] }} />);
    expect(screen.getByText("No files to outline")).toBeTruthy();
  });

  it("says when an edge carries no names rather than printing a bare path", () => {
    const graph: GraphExport = {
      nodes: [node("a.ts"), node("b.ts")],
      links: [link("a.ts", "b.ts", [])],
    };
    render(<CodeGraphTree graph={graph} />);
    fireEvent.click(screen.getByText("a.ts"));
    expect(
      screen.getByText(/The edge records no names, so we know these two files are joined/),
    ).toBeTruthy();
  });
});
