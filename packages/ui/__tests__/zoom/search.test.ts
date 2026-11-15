import { describe, it, expect } from "vitest";
import { buildSearchIndex, nameWords, searchIndex } from "../../src/zoom/search";
import type { ZoomKind, ZoomMap, ZoomNode } from "../../src/zoom/types";
import { syntheticMap } from "./synthetic-map";

const LEVEL: Record<ZoomKind, number> = { system: 0, layer: 1, group: 2, folder: 3, file: 4 };

function n(
  id: string,
  parent: string | null,
  kind: ZoomKind,
  name: string,
  extra: Partial<ZoomNode> = {},
): ZoomNode {
  return {
    id,
    parent_id: parent,
    level: LEVEL[kind],
    kind,
    name,
    path: kind === "file" || kind === "folder" ? `src/${name}` : "",
    children: [],
    importance: 0,
    sibling_rank: 0,
    metrics: {
      file_count: 1,
      descendant_count: 0,
      hotspot_count: 0,
      dead_count: 0,
      entry_point_count: 0,
      on_flow_count: 0,
    },
    summary: "",
    language: null,
    page_id: "",
    health_score: null,
    is_entry_point: false,
    is_hotspot: false,
    is_dead: false,
    is_test: false,
    on_flow: false,
    ...extra,
  };
}

const MAP: ZoomMap = {
  root_id: "sys",
  project_name: "repo",
  total_files: 3,
  max_depth: 4,
  truncated: false,
  nodes: [
    n("sys", null, "system", "repo"),
    n("L", "sys", "layer", "Core Engine", { container_ids: ["pkg:core"] }),
    n("G", "L", "group", "core", { container_ids: ["pkg:core"] }),
    n("D", "G", "folder", "health"),
    n("F1", "D", "file", "core.py"),
    n("F2", "D", "file", "code_health.py"),
    n("F3", "D", "file", "zoomDetailPanel.tsx"),
  ],
  relations: [],
  containers: [
    {
      id: "pkg:core",
      name: "repowise-core",
      path: "packages/core",
      kind: "library",
      technologies: [],
      file_count: 3,
    },
    { id: "pkg:none", name: "orphan", path: "x", kind: "library", technologies: [], file_count: 0 },
  ],
  externals: [
    {
      id: "ext:sqlalchemy",
      name: "sqlalchemy",
      display_name: "SQLAlchemy",
      kind: "db",
      category: "service",
      io_kind: "db",
      ecosystem: "pypi",
      file_count: 2,
      edge_count: 5,
    },
  ],
  external_relations: [
    // Counts aggregate upward: the layer always has the most edges.
    { parent_id: "sys", source_id: "L", target_id: "ext:sqlalchemy", label: "uses", edge_count: 5, coupling: "loose" },
    { parent_id: "L", source_id: "G", target_id: "ext:sqlalchemy", label: "uses", edge_count: 5, coupling: "loose" },
    { parent_id: "G", source_id: "D", target_id: "ext:sqlalchemy", label: "uses", edge_count: 5, coupling: "loose" },
    { parent_id: "D", source_id: "F1", target_id: "ext:sqlalchemy", label: "uses", edge_count: 5, coupling: "loose" },
  ],
};

const index = buildSearchIndex(MAP);
const keys = (q: string) => searchIndex(index, q).map((e) => e.key);

describe("nameWords", () => {
  it("splits separators and camelCase humps", () => {
    expect(nameWords("zoomDetail-panel.tsx")).toEqual(["zoom", "detail", "panel", "tsx"]);
    expect(nameWords("Code Health")).toEqual(["code", "health"]);
  });
});

describe("searchIndex", () => {
  it("ranks exact, then prefix, then word start, then substring", () => {
    // "core": the group is the exact name, the layer and core.py are prefixes,
    // and "repowise-core" has a word starting with it.
    expect(keys("core")).toEqual(["G", "L", "F1", "pkg:core"]);
    expect(keys("health")[0]).toBe("D");
    expect(keys("health")).toContain("F2"); // a word start inside code_health.py
    expect(keys("detail")).toEqual(["F3"]); // a camelCase word start
  });

  it("puts the coarser kind first within one tier", () => {
    const kinds = searchIndex(index, "co").map((e) => e.kind);
    expect(kinds.indexOf("layer")).toBeLessThan(kinds.indexOf("file"));
  });

  it("lands a container on its coarsest card and an external on the folder that imports it most", () => {
    const [container] = searchIndex(index, "repowise-core");
    expect(container).toMatchObject({ kind: "container", targetId: "L" });
    const [ext] = searchIndex(index, "sqlalch");
    expect(ext).toMatchObject({ kind: "external", name: "SQLAlchemy", targetId: "D" });
    // A container with no card on this map is not offered.
    expect(keys("orphan")).toEqual([]);
  });

  it("carries the breadcrumb without the root", () => {
    const [file] = searchIndex(index, "code_health");
    expect(file!.crumbs).toEqual(["Core Engine", "core", "health"]);
  });

  it("ignores one-letter queries and caps the result count", () => {
    expect(keys("c")).toEqual([]);
    expect(searchIndex(index, "py", 2)).toHaveLength(2);
  });

  it("searches a repository-sized map in well under 2 ms per query", () => {
    const big = buildSearchIndex(syntheticMap());
    expect(big.entries.length).toBeGreaterThan(7000);
    const queries = ["fi", "file", "folder f:0", "0/1", "zz", "group", "f:3:1"];
    for (const q of queries) searchIndex(big, q); // warm up
    const runs = 20;
    const t0 = performance.now();
    for (let i = 0; i < runs; i++) for (const q of queries) searchIndex(big, q);
    const perQuery = (performance.now() - t0) / (runs * queries.length);
    expect(perQuery).toBeLessThan(2);
  });
});
