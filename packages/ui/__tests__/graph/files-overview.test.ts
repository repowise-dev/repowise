import { describe, it, expect } from "vitest";
import type { GraphLink, GraphNode } from "@repowise-dev/types/graph";
import { fileGraphToGraphology } from "../../src/graph/sigma/graphology-adapter";
import {
  filterOverviewData,
  getOverviewMeta,
  layoutFilesOverview,
  overviewFileLabel,
} from "../../src/graph/sigma/files-overview";

function node(id: string, community: number): GraphNode {
  return {
    node_id: id,
    node_type: "file",
    language: "python",
    symbol_count: 1,
    pagerank: 0,
    betweenness: 0,
    community_id: community,
    is_test: false,
    is_entry_point: false,
    has_doc: false,
  };
}

const dep = (source: string, target: string): GraphLink => ({
  source,
  target,
  imported_names: ["x"],
  edge_type: "imports",
});

describe("filterOverviewData", () => {
  const nodes = [
    node("a.py", 0),
    node("b.py", 0),
    node("orphan.py", 0),
    node("cochange.py", 0),
    node("external:rich.console", 0),
    node("framework:django", 0),
  ];
  const links = [
    dep("a.py", "b.py"),
    dep("a.py", "external:rich.console"),
    dep("b.py", "framework:django"),
    { source: "cochange.py", target: "a.py", imported_names: [], edge_type: "co_changes" },
  ];

  it("leaves out third-party nodes and files with no dependency edge, counting both", () => {
    const out = filterOverviewData({ nodes, links }, { showExternal: false });
    expect(out.nodes.map((n) => n.node_id)).toEqual(["a.py", "b.py"]);
    expect(out.hiddenExternal).toBe(2);
    // orphan.py has no edge; cochange.py only a co-change, which is not one.
    expect(out.hiddenUnlinked).toBe(2);
    expect(out.links).toHaveLength(1);
  });

  it("draws third-party nodes when asked, and then hides none", () => {
    const out = filterOverviewData({ nodes, links }, { showExternal: true });
    expect(out.hiddenExternal).toBe(0);
    expect(out.nodes.map((n) => n.node_id)).toContain("framework:django");
  });

  it("always draws a pinned file, third-party or unlinked, with its edges", () => {
    const ext = filterOverviewData({ nodes, links }, { showExternal: false, pinned: "external:rich.console" });
    expect(ext.nodes.map((n) => n.node_id)).toContain("external:rich.console");
    expect(ext.shownExternal).toBe(1);
    expect(ext.hiddenExternal).toBe(1);
    const lone = filterOverviewData({ nodes, links }, { showExternal: false, pinned: "cochange.py" });
    expect(lone.nodes.map((n) => n.node_id)).toContain("cochange.py");
    expect(lone.links.some((l) => l.source === "cochange.py")).toBe(true);
  });

  it("keeps the edges between drawn files of every kind, for the toggles", () => {
    const withCoChange = [...links, { source: "b.py", target: "a.py", imported_names: [], edge_type: "co_changes" }];
    const out = filterOverviewData({ nodes, links: withCoChange }, { showExternal: false });
    expect(out.links.some((l) => l.edge_type === "co_changes")).toBe(true);
  });
});

describe("overviewFileLabel", () => {
  it("gives a generic filename its folder and leaves a telling one alone", () => {
    expect(overviewFileLabel("pkg/graph/__init__.py")).toBe("graph/__init__.py");
    expect(overviewFileLabel("src/app/repos/page.tsx")).toBe("repos/page.tsx");
    expect(overviewFileLabel("pkg/graph/use-sigma.ts")).toBe("use-sigma.ts");
  });
});

/** Three communities of `size` files, each a chain, plus `cross` edges from
 *  community 0 to 1 and from 1 to 2, and one stray in its own community. */
function repo(size: number, cross: number) {
  const nodes: GraphNode[] = [];
  const links: GraphLink[] = [];
  for (let c = 0; c < 3; c++) {
    for (let i = 0; i < size; i++) {
      nodes.push(node(`c${c}/f${i}.py`, c));
      if (i > 0) links.push(dep(`c${c}/f${i}.py`, `c${c}/f${i - 1}.py`));
    }
  }
  for (let i = 0; i < cross; i++) {
    links.push(dep(`c0/f${i}.py`, `c1/f${i}.py`));
    links.push(dep(`c1/f${i}.py`, `c2/f${i}.py`));
  }
  nodes.push(node("stray/only.py", 9));
  links.push(dep("stray/only.py", "c2/f0.py"));
  links.push(dep("stray/only.py", "c2/f1.py"));
  return { nodes, links };
}

describe("layoutFilesOverview", () => {
  it("records clusters and one band per connected community pair", () => {
    const graph = fileGraphToGraphology(repo(20, 6));
    const meta = layoutFilesOverview(graph);
    expect(meta.clusters.map((c) => c.communityId).sort()).toEqual([0, 1, 2]);
    const pairs = meta.bundles.map((b) => `${b.a}-${b.b}:${b.count}`).sort();
    expect(pairs).toEqual(["0-1:6", "1-2:6"]);
    expect(getOverviewMeta(graph)).toBe(meta);
  });

  it("anchors each band on the clusters' rims, not their centres", () => {
    const graph = fileGraphToGraphology(repo(20, 6));
    const meta = layoutFilesOverview(graph);
    for (const b of meta.bundles) {
      const from = meta.clusters.find((c) => c.communityId === b.a)!;
      const to = meta.clusters.find((c) => c.communityId === b.b)!;
      expect(Math.hypot(b.x0 - from.cx, b.y0 - from.cy)).toBeCloseTo(from.r, 5);
      expect(Math.hypot(b.x1 - to.cx, b.y1 - to.cy)).toBeCloseTo(to.r, 5);
    }
  });

  it("seats a stray file on the rim of the cluster it links to", () => {
    const graph = fileGraphToGraphology(repo(20, 6));
    const meta = layoutFilesOverview(graph);
    const host = meta.clusters.find((c) => c.communityId === 2)!;
    const { x, y } = graph.getNodeAttributes("stray/only.py");
    expect(Math.hypot(x - host.cx, y - host.cy)).toBeCloseTo(host.r * 1.15, 5);
    // Placement only: it keeps its own community and colour.
    expect(graph.getNodeAttribute("stray/only.py", "communityId")).toBe(9);
  });

  it("names a few ranked files at overview zoom and gives every file a short label", () => {
    const graph = fileGraphToGraphology(repo(20, 6));
    layoutFilesOverview(graph);
    const forced = graph.filterNodes((_n, a) => !!a.forceLabel);
    expect(forced.length).toBeGreaterThan(0);
    expect(forced.length).toBeLessThanOrEqual(8 + 3);
    expect(graph.getNodeAttribute("c0/f3.py", "label")).toBe("f3.py");
  });

  it("is idempotent: a second call neither moves nodes nor rebuilds the meta", () => {
    const graph = fileGraphToGraphology(repo(20, 6));
    const first = layoutFilesOverview(graph);
    const before = graph.getNodeAttributes("c1/f5.py");
    const second = layoutFilesOverview(graph);
    expect(second).toBe(first);
    expect(graph.getNodeAttributes("c1/f5.py").x).toBe(before.x);
  });
});
