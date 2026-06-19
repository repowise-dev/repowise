import { describe, it, expect } from "vitest";
import type { SystemEdge, SystemGraph, SystemNode } from "@repowise-dev/types";
import { collapseToRepos } from "../../src/workspace/system-map/collapse";
import { applyView } from "../../src/workspace/system-map/layout";
import {
  edgeKindStyle,
  matchTypeDash,
  matchTypeLabel,
  EDGE_KIND_ORDER,
} from "../../src/workspace/system-map/edge-kinds";
import { nodeKindStyle, NODE_KIND_ORDER } from "../../src/workspace/system-map/node-kinds";
import { resolveEdgeOverlay, resolveNodeOverlay } from "../../src/workspace/system-map/types";

function node(id: string, repo: string, over: Partial<SystemNode> = {}): SystemNode {
  return {
    id,
    repo,
    service_path: id.includes("::") ? id.split("::")[1]! : null,
    name: id.split("/").pop() ?? id,
    kind: "service",
    provider_count: 0,
    consumer_count: 0,
    contract_types: [],
    is_orphan_provider: false,
    is_orphan_consumer: false,
    is_isolated: false,
    ...over,
  };
}

function edge(source: string, target: string, over: Partial<SystemEdge> = {}): SystemEdge {
  return {
    id: `${source}->${target}`,
    source,
    target,
    kind: "http",
    match_type: "exact",
    confidence: 1,
    weight: 1,
    structural: true,
    contract_refs: [],
    ...over,
  };
}

function graph(nodes: SystemNode[], edges: SystemEdge[]): SystemGraph {
  return { version: 1, generated_at: "2026-06-19T00:00:00Z", nodes, edges, diagnostics: {} as never };
}

describe("collapseToRepos", () => {
  it("merges services of a repo into one node and sums their counts", () => {
    const g = graph(
      [
        node("api::svc/a", "api", { provider_count: 2, contract_types: ["http"] }),
        node("api::svc/b", "api", { consumer_count: 3, contract_types: ["grpc"] }),
        node("web", "web", { kind: "frontend" }),
      ],
      [edge("web", "api::svc/a")],
    );
    const collapsed = collapseToRepos(g);
    expect(collapsed.nodes.map((n) => n.id).sort()).toEqual(["api", "web"]);
    const api = collapsed.nodes.find((n) => n.id === "api")!;
    expect(api.provider_count).toBe(2);
    expect(api.consumer_count).toBe(3);
    expect(api.contract_types).toEqual(["grpc", "http"]);
    expect(api.service_path).toBeNull();
  });

  it("drops intra-repo edges and merges parallel cross-repo edges", () => {
    const g = graph(
      [node("api::a", "api"), node("api::b", "api"), node("web", "web")],
      [
        edge("api::a", "api::b"), // intra-repo → dropped
        edge("web", "api::a", { weight: 1, confidence: 0.6, match_type: "candidate" }),
        edge("web", "api::b", { weight: 2, confidence: 0.9, match_type: "exact" }),
      ],
    );
    const collapsed = collapseToRepos(g);
    expect(collapsed.edges).toHaveLength(1);
    const e = collapsed.edges[0]!;
    expect(e.source).toBe("web");
    expect(e.target).toBe("api");
    expect(e.weight).toBe(3); // 1 + 2
    expect(e.confidence).toBe(0.9); // max
    expect(e.match_type).toBe("exact"); // strongest
  });

  it("keeps different edge kinds between the same repos distinct", () => {
    const g = graph(
      [node("web", "web"), node("api", "api")],
      [edge("web", "api", { kind: "http" }), edge("web", "api", { kind: "co_change", structural: false })],
    );
    const collapsed = collapseToRepos(g);
    expect(collapsed.edges).toHaveLength(2);
    expect(new Set(collapsed.edges.map((e) => e.kind))).toEqual(new Set(["http", "co_change"]));
  });

  it("flags a repo with no surviving edges as isolated", () => {
    const g = graph([node("api::a", "api"), node("api::b", "api"), node("lonely", "lonely")], [edge("api::a", "api::b")]);
    const collapsed = collapseToRepos(g);
    expect(collapsed.nodes.find((n) => n.id === "lonely")!.is_isolated).toBe(true);
    // api had only an intra-repo edge → also isolated after collapse
    expect(collapsed.nodes.find((n) => n.id === "api")!.is_isolated).toBe(true);
  });

  it("preserves a single-kind repo's kind, neutralizes a mixed one", () => {
    const g = graph(
      [node("libs::x", "libs", { kind: "library" }), node("libs::y", "libs", { kind: "library" })],
      [],
    );
    expect(collapseToRepos(g).nodes[0]!.kind).toBe("library");
    const mixed = graph(
      [node("app::x", "app", { kind: "frontend" }), node("app::y", "app", { kind: "worker" })],
      [],
    );
    expect(collapseToRepos(mixed).nodes[0]!.kind).toBe("service");
  });
});

describe("applyView", () => {
  const g = graph(
    [node("a", "a"), node("b", "b")],
    [edge("a", "b", { kind: "http" }), edge("a", "b", { id: "cc", kind: "co_change", structural: false })],
  );

  it("filters edges to the visible kinds, keeping all nodes", () => {
    const view = { visibleKinds: new Set(["http"] as const), collapsed: false };
    const out = applyView(g, view);
    expect(out.edges).toHaveLength(1);
    expect(out.edges[0]!.kind).toBe("http");
    expect(out.nodes).toHaveLength(2);
  });

  it("hides everything when no kinds are visible", () => {
    const out = applyView(g, { visibleKinds: new Set(), collapsed: false });
    expect(out.edges).toHaveLength(0);
  });

  it("collapses before filtering", () => {
    const multi = graph(
      [node("api::a", "api"), node("web", "web")],
      [edge("web", "api::a", { kind: "http" })],
    );
    const out = applyView(multi, { visibleKinds: new Set(["http"] as const), collapsed: true });
    expect(out.nodes.map((n) => n.id).sort()).toEqual(["api", "web"]);
    expect(out.edges[0]!.target).toBe("api");
  });
});

describe("edge-kind registry", () => {
  it("covers every kind in the union and display order", () => {
    expect(EDGE_KIND_ORDER).toHaveLength(6);
    for (const kind of EDGE_KIND_ORDER) {
      const s = edgeKindStyle(kind);
      expect(s.label).toBeTruthy();
      expect(s.color).toMatch(/^var\(--/);
    }
  });

  it("marks co-change as the only behavioral kind", () => {
    expect(edgeKindStyle("co_change").category).toBe("behavioral");
    expect(edgeKindStyle("http").category).toBe("structural");
  });

  it("falls back gracefully for an unknown kind", () => {
    expect(edgeKindStyle("quantum").label).toBe("Link");
  });

  it("maps match type to a distinct dash and label", () => {
    expect(matchTypeDash("exact")).toBe("none");
    expect(matchTypeDash("manual")).toBe("none");
    expect(matchTypeDash("candidate")).toBe("6 4");
    expect(matchTypeDash("inferred")).toBe("2 4");
    expect(matchTypeLabel("candidate")).toBe("Candidate");
  });
});

describe("node-kind registry", () => {
  it("maps each kind to a tone and label", () => {
    expect(NODE_KIND_ORDER).toHaveLength(5);
    expect(nodeKindStyle("frontend").tone).toBe("container");
    expect(nodeKindStyle("service").label).toBe("Service");
  });
});

describe("overlay resolution", () => {
  it("returns null when no overlay touches the element", () => {
    expect(resolveNodeOverlay(undefined, "x")).toBeNull();
    expect(resolveNodeOverlay({ highlightNodeIds: new Set(["other"]) }, "x")).toBeNull();
  });

  it("resolves highlight, dim, and badge for a node", () => {
    const state = resolveNodeOverlay(
      { highlightNodeIds: new Set(["x"]), nodeBadges: { x: { label: "3", tone: "danger" } } },
      "x",
    );
    expect(state).toEqual({ highlighted: true, dimmed: false, badge: { label: "3", tone: "danger" } });
  });

  it("resolves edge overlay independently", () => {
    expect(resolveEdgeOverlay({ dimEdgeIds: new Set(["e"]) }, "e")).toEqual({ highlighted: false, dimmed: true });
  });
});
