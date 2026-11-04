import { describe, it, expect } from "vitest";
import type { SystemEdge, SystemGraph, SystemNode } from "@repowise-dev/types";
import {
  edgeKindStyle,
  edgeLabel,
  isGatewayRouted,
  matchTypeDash,
  matchTypeLabel,
  EDGE_KIND_ORDER,
} from "../../src/workspace/system-map/edge-kinds";
import { nodeKindStyle, NODE_KIND_ORDER } from "../../src/workspace/system-map/node-kinds";
import { resolveEdgeOverlay, resolveNodeOverlay } from "../../src/workspace/system-map/types";
import {
  buildBlastRadiusOverlay,
  impactBadgeTone,
} from "../../src/workspace/system-map/blast-radius";
import { buildBreakingChangeOverlay } from "../../src/workspace/system-map/breaking-changes";
import { buildConformanceOverlay } from "../../src/workspace/system-map/conformance";
import { buildDsm } from "../../src/workspace/dsm/dsm";
import type {
  BreakingChange,
  BreakingChangeReport,
  ConformanceReport,
  CrossRepoBlastRadius,
} from "@repowise-dev/types";

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

describe("edge-kind registry", () => {
  it("covers every kind in the union and display order", () => {
    expect(EDGE_KIND_ORDER).toHaveLength(7);
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

  it("names a gateway-routed link in words, keeping its transport kind", () => {
    const routed = { kind: "http" as const, strategy: "gateway_route" };
    expect(isGatewayRouted(routed)).toBe(true);
    expect(edgeLabel(routed)).toBe("HTTP via gateway");
    expect(edgeKindStyle(routed.kind).label).toBe("HTTP");
    expect(isGatewayRouted({ strategy: "service_mapped" })).toBe(false);
    expect(edgeLabel({ kind: "http", strategy: null })).toBe("HTTP");
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

describe("buildBlastRadiusOverlay", () => {
  function result(over: Partial<CrossRepoBlastRadius> = {}): CrossRepoBlastRadius {
    return {
      targets: ["db"],
      target_repos: ["db"],
      impacted: [],
      impacted_repos: [],
      structural_count: 0,
      behavioral_count: 0,
      max_distance: 0,
      total_impacted: 0,
      unresolved_targets: [],
      ...over,
    };
  }

  const g = graph(
    [node("db", "db"), node("api", "api"), node("web", "web"), node("idle", "idle")],
    [edge("api", "db"), edge("web", "api"), edge("web", "idle")],
  );

  it("highlights the focus set and dims everything else", () => {
    const overlay = buildBlastRadiusOverlay(
      g,
      result({
        impacted: [
          { id: "api", repo: "api", name: "api", kind: "service", distance: 1, score: 0.5, structural: true, edge_kinds: ["http"] },
          { id: "web", repo: "web", name: "web", kind: "service", distance: 2, score: 0.2, structural: true, edge_kinds: ["http"] },
        ],
      }),
    );
    expect([...(overlay.highlightNodeIds ?? [])].sort()).toEqual(["api", "db", "web"]);
    // The unrelated node is dimmed.
    expect(overlay.dimNodeIds?.has("idle")).toBe(true);
    expect(overlay.dimNodeIds?.has("api")).toBe(false);
    // Edges fully inside focus are highlighted; the rest dimmed.
    expect(overlay.highlightEdgeIds?.has("api->db")).toBe(true);
    expect(overlay.highlightEdgeIds?.has("web->api")).toBe(true);
    expect(overlay.dimEdgeIds?.has("web->idle")).toBe(true);
  });

  it("badges the source and grades impacted nodes by intensity", () => {
    const overlay = buildBlastRadiusOverlay(
      g,
      result({
        impacted: [
          { id: "api", repo: "api", name: "api", kind: "service", distance: 1, score: 0.5, structural: true, edge_kinds: ["http"] },
        ],
      }),
    );
    expect(overlay.nodeBadges?.db).toEqual({ label: "source", tone: "info" });
    expect(overlay.nodeBadges?.api).toEqual({ label: "d1", tone: "danger" });
  });

  it("returns an empty overlay when nothing is in focus", () => {
    const overlay = buildBlastRadiusOverlay(g, result({ targets: [], impacted: [] }));
    expect(overlay).toEqual({});
  });

  it("grades intensity: structural-near danger, structural-far info, behavioral info", () => {
    expect(impactBadgeTone(1, true)).toBe("danger");
    expect(impactBadgeTone(2, true)).toBe("warning");
    expect(impactBadgeTone(3, true)).toBe("info");
    expect(impactBadgeTone(1, false)).toBe("info");
  });
});

describe("buildBreakingChangeOverlay", () => {
  function change(over: Partial<BreakingChange> = {}): BreakingChange {
    return {
      kind: "removed_endpoint",
      severity: "breaking",
      contract_id: "http::GET::/users",
      contract_type: "http",
      provider_repo: "api",
      provider_file: "routes.py",
      provider_symbol: "h",
      provider_service: null,
      provider_node_id: "api",
      detail: "removed",
      impacted_consumers: [
        {
          repo: "web",
          service: null,
          node_id: "web",
          file: "client.ts",
          symbol: "fetch",
          match_type: "exact",
          confidence: 0.9,
        },
      ],
      ...over,
    };
  }
  function report(changes: BreakingChange[]): BreakingChangeReport {
    return {
      version: 1,
      generated_at: "t",
      changes,
      total: changes.length,
      breaking_count: changes.filter((c) => c.severity === "breaking").length,
      warning_count: changes.filter((c) => c.severity === "warning").length,
      impacted_repos: [],
      impacted_services: [],
      total_impacted_consumers: 0,
    };
  }

  const g = graph(
    [node("api", "api"), node("web", "web"), node("idle", "idle")],
    [edge("web", "api"), edge("idle", "api")],
  );

  it("badges the changed provider and exposed consumers, highlighting the seam", () => {
    const overlay = buildBreakingChangeOverlay(g, report([change()]));
    expect(overlay.nodeBadges?.api).toEqual({ label: "1 breaking", tone: "danger" });
    expect(overlay.nodeBadges?.web).toEqual({ label: "exposed", tone: "warning" });
    // Only the consumer→provider edge in the report is highlighted.
    expect(overlay.highlightEdgeIds?.has("web->api")).toBe(true);
    expect(overlay.highlightEdgeIds?.has("idle->api")).toBe(false);
    expect(overlay.edgeBadges?.["web->api"]).toEqual({ label: "incompatible", tone: "danger" });
    // Additive overlay: nothing is dimmed.
    expect(overlay.dimNodeIds).toBeUndefined();
  });

  it("a warning-only provider reads as a warning badge", () => {
    const overlay = buildBreakingChangeOverlay(
      g,
      report([change({ severity: "warning", kind: "schema_comparison_uncertain" })]),
    );
    expect(overlay.nodeBadges?.api).toEqual({ label: "1 change", tone: "warning" });
    expect(overlay.nodeBadges?.web).toBeUndefined();
    expect(overlay.highlightEdgeIds).toBeUndefined();
  });

  it("returns an empty overlay when there are no changes", () => {
    expect(buildBreakingChangeOverlay(g, report([]))).toEqual({});
    expect(buildBreakingChangeOverlay(g, null)).toEqual({});
  });
});

function conformanceReport(over: Partial<ConformanceReport> = {}): ConformanceReport {
  const violations = over.violations ?? [];
  const cycles = over.cycles ?? [];
  return {
    version: 1,
    generated_at: "t",
    rules_evaluated: 1,
    violations,
    cycles,
    violation_count: violations.length,
    cycle_count: cycles.length,
    total_cycles: cycles.length,
    violating_repos: [],
    ...over,
  };
}

describe("buildConformanceOverlay", () => {
  const g = graph(
    [node("frontend", "frontend"), node("db", "db"), node("api", "api")],
    [edge("frontend", "db"), edge("db", "frontend"), edge("frontend", "api")],
  );

  it("badges violating edges danger and highlights both endpoints", () => {
    const overlay = buildConformanceOverlay(
      g,
      conformanceReport({
        violations: [
          {
            rule_source: "frontend",
            rule_target: "db",
            rule_description: "",
            source: "frontend",
            source_name: "frontend",
            target: "db",
            target_name: "db",
            edge_id: "frontend->db",
            edge_kind: "http",
            severity: "violation",
          },
        ],
      }),
    );
    expect(overlay.edgeBadges?.["frontend->db"]).toEqual({ label: "violation", tone: "danger" });
    expect(overlay.nodeBadges?.frontend).toEqual({ label: "violation", tone: "danger" });
    expect(overlay.nodeBadges?.db).toEqual({ label: "violation", tone: "danger" });
    // Additive: nothing dimmed.
    expect(overlay.dimNodeIds).toBeUndefined();
  });

  it("badges cycle edges warning and never overrides a violation badge", () => {
    const overlay = buildConformanceOverlay(
      g,
      conformanceReport({
        violations: [
          {
            rule_source: "frontend",
            rule_target: "db",
            rule_description: "",
            source: "frontend",
            source_name: "frontend",
            target: "db",
            target_name: "db",
            edge_id: "frontend->db",
            edge_kind: "http",
            severity: "violation",
          },
        ],
        cycles: [{ nodes: ["frontend", "db"], edge_ids: ["frontend->db", "db->frontend"], length: 2 }],
      }),
    );
    // The shared edge keeps its violation badge (danger), not "cycle".
    expect(overlay.edgeBadges?.["frontend->db"]).toEqual({ label: "violation", tone: "danger" });
    // The other cycle edge gets the cycle badge.
    expect(overlay.edgeBadges?.["db->frontend"]).toEqual({ label: "cycle", tone: "warning" });
  });

  it("returns an empty overlay with no findings", () => {
    expect(buildConformanceOverlay(g, conformanceReport())).toEqual({});
    expect(buildConformanceOverlay(g, null)).toEqual({});
  });
});

describe("buildDsm", () => {
  const g = graph(
    [node("api", "api"), node("db", "db"), node("web", "web")],
    [edge("web", "api"), edge("api", "db", { kind: "grpc" })],
  );

  it("places a present, kind-tagged cell for each dependency", () => {
    const dsm = buildDsm(g);
    // axis sorted by repo: api, db, web
    expect(dsm.axis).toEqual(["api", "db", "web"]);
    const cell = (from: string, to: string) =>
      dsm.cells[dsm.axis.indexOf(from)]![dsm.axis.indexOf(to)]!;
    expect(cell("web", "api").present).toBe(true);
    expect(cell("api", "db").present).toBe(true);
    expect(cell("api", "db").kind).toBe("grpc");
    // No reverse dependency.
    expect(cell("db", "api").present).toBe(false);
  });

  it("flags violation and cycle cells from the report", () => {
    const dsm = buildDsm(
      g,
      conformanceReport({
        violations: [
          {
            rule_source: "web",
            rule_target: "api",
            rule_description: "",
            source: "web",
            source_name: "web",
            target: "api",
            target_name: "api",
            edge_id: "web->api",
            edge_kind: "http",
            severity: "violation",
          },
        ],
        cycles: [{ nodes: ["api", "db"], edge_ids: ["api->db"], length: 2 }],
      }),
    );
    const cell = (from: string, to: string) =>
      dsm.cells[dsm.axis.indexOf(from)]![dsm.axis.indexOf(to)]!;
    expect(cell("web", "api").violation).toBe(true);
    expect(cell("api", "db").cycle).toBe(true);
  });

  it("returns an empty matrix for an empty graph", () => {
    expect(buildDsm(null)).toEqual({ axis: [], labels: [], cells: [] });
  });
});

