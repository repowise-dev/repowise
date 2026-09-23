import { describe, it, expect } from "vitest";
import type { SystemEdge, SystemGraph, SystemNode, WorkspaceContractLinkEntry } from "@repowise-dev/types/workspace";
import {
  SYSTEM_MAP_PROMPT_MAX_ROWS,
  buildBlastRadiusAiPrompt,
  buildCycleAiPrompt,
  buildEdgeAiPrompt,
  buildServiceAiPrompt,
} from "../../src/workspace/system-map/system-map-ai-prompt";
import {
  edgeLinks,
  edgeSentence,
  healthMark,
  serviceDiagnostics,
  serviceResolver,
  summarizeServiceContracts,
  weightLabel,
} from "../../src/workspace/system-map/system-map-model";

function node(id: string, repo: string, service_path: string | null = null): SystemNode {
  return {
    id,
    repo,
    service_path,
    name: service_path ? service_path.split("/").pop()! : repo,
    kind: "service",
    provider_count: 0,
    consumer_count: 0,
    contract_types: [],
    is_orphan_provider: false,
    is_orphan_consumer: false,
    is_isolated: false,
  };
}

function edge(source: string, target: string, over: Partial<SystemEdge> = {}): SystemEdge {
  return {
    id: `${source}->${target}:http`,
    source,
    target,
    kind: "http",
    match_type: "exact",
    confidence: 0.85,
    weight: 1,
    structural: true,
    contract_refs: [],
    ...over,
  };
}

function link(over: Partial<WorkspaceContractLinkEntry>): WorkspaceContractLinkEntry {
  return {
    contract_id: "http::GET::/users",
    contract_type: "http",
    match_type: "exact",
    confidence: 0.85,
    provider_repo: "api",
    provider_file: "routes.py",
    provider_symbol: "",
    consumer_repo: "mono",
    consumer_file: "packages/web/client.ts",
    consumer_symbol: "",
    provider_service: null,
    consumer_service: "packages/web",
    provider_symbol_id: null,
    consumer_symbol_id: null,
    ...over,
  };
}

const graph: SystemGraph = {
  version: 1,
  generated_at: "t",
  nodes: [node("api", "api"), node("mono", "mono"), node("mono::packages/web", "mono", "packages/web")],
  edges: [edge("mono::packages/web", "api")],
  diagnostics: {
    total_providers: 0,
    total_consumers: 0,
    total_links: 0,
    weak_link_count: 0,
    repo_breakdown: [],
    unmatched_consumers: [
      { repo: "mono", file_path: "packages/web/a.ts", contract_id: "http::GET::/x", contract_type: "http", reason: "no_provider" },
      { repo: "mono", file_path: "scripts/b.ts", contract_id: "http::GET::/y", contract_type: "http", reason: "external_host" },
    ],
    unmatched_by_reason: {},
    orphan_providers: [{ repo: "api", file_path: "routes.py", contract_id: "http::GET::/z", contract_type: "http" }],
    providers_by_layer: {},
    consumers_by_layer: {},
    http_consumers_unresolved: 0,
    http_consumer_coverage: null,
  },
};

describe("system map model", () => {
  it("reads health in the canonical three bands", () => {
    expect(healthMark(85)).toMatchObject({ value: "8.5", label: "Healthy" });
    expect(healthMark(67.4)).toMatchObject({ value: "6.7", label: "Warning" });
    expect(healthMark(30)).toMatchObject({ label: "Alert" });
  });

  it("puts a file in its deepest service, else the repo root, and in its repo when collapsed", () => {
    const resolve = serviceResolver(graph, false);
    expect(resolve("mono", "packages/web/src/x.ts")).toBe("mono::packages/web");
    expect(resolve("mono", "packages/website/x.ts")).toBe("mono");
    expect(serviceResolver(graph, true)("mono", "packages/web/x.ts")).toBe("mono");
  });

  it("counts a service's contracts by role and type, most linked first", () => {
    const summary = summarizeServiceContracts(
      {
        contracts: [
          { contract_id: "http::GET::/a", contract_type: "http", role: "consumer", repo: "mono", file_path: "packages/web/c.ts" },
          { contract_id: "http::GET::/users", contract_type: "http", role: "consumer", repo: "mono", file_path: "packages/web/client.ts" },
          { contract_id: "code::x", contract_type: "code", role: "provider", repo: "mono", file_path: "packages/web/i.ts" },
          { contract_id: "http::GET::/other", contract_type: "http", role: "provider", repo: "mono", file_path: "server/r.py" },
        ],
        links: [link({})],
        total: 4,
      },
      "mono::packages/web",
      serviceResolver(graph, false),
    );
    expect(summary.consumes).toEqual({ http: 2 });
    expect(summary.provides).toEqual({ code: 1 });
    expect(summary.rows[0]).toMatchObject({ contract_id: "http::GET::/users", link_count: 1 });
  });

  it("attributes unmatched consumers and unused providers to the owning service", () => {
    const resolve = serviceResolver(graph, false);
    expect(serviceDiagnostics(graph.diagnostics, "mono::packages/web", resolve)).toEqual({
      unmatched: 1,
      unmatchedByReason: { no_provider: 1 },
      unusedProviders: 0,
    });
    expect(serviceDiagnostics(graph.diagnostics, "api", resolve).unusedProviders).toBe(1);
  });

  it("joins an edge to the links it aggregates, consumer to provider", () => {
    const e = graph.edges[0]!;
    const links = edgeLinks(
      e,
      [link({}), link({ consumer_file: "server/r.py" }), link({ contract_type: "code" }), link({})],
      serviceResolver(graph, false),
    );
    // Wrong service, wrong type and a duplicate are all dropped.
    expect(links).toHaveLength(1);
  });

  it("names the weight's unit and says the relationship as a sentence", () => {
    expect(weightLabel({ kind: "http", weight: 180 })).toBe("180 endpoints called");
    expect(weightLabel({ kind: "co_change", weight: 1 })).toBe("1 file pair changed together");
    expect(edgeSentence({ kind: "db" }, "backend", "core")).toBe("backend uses database tables core defines.");
  });
});

describe("system map AI prompts", () => {
  it("service prompt carries location, counts, neighbours, contracts and the MCP tools", () => {
    const text = buildServiceAiPrompt({
      service: { id: "backend", name: "backend", repo: "backend", service_path: null },
      health: { value: "7.2", label: "Warning" },
      role: { label: "Core", reaches: 4, reachedBy: 1 },
      provides: { http: 293, data: 161 },
      consumes: { http: 34 },
      contracts: [{ contract_id: "http::GET::/repos", role: "provider", repo: "backend", file_path: "app/r.py", line: 12, link_count: 3 }],
      contractTotal: 488,
      dependsOn: [{ id: "frontend", name: "frontend", kind: "HTTP", weight: 1, weight_label: "1 endpoint called" }],
      dependedOnBy: [],
      unmatched: 35,
      unmatchedByReason: { external_host: 23 },
      unusedProviders: 293,
      cycles: [["backend", "frontend"]],
      flavor: "claude-code-mcp",
    });
    expect(text).toContain("## Service: backend (`backend`)");
    expect(text).toContain("Provides: 293 http, 161 data");
    expect(text).toContain("`http::GET::/repos` (provider) at `backend:app/r.py:12`, 3 matched links");
    expect(text).toContain("487 more contracts not listed");
    expect(text).toContain("backend -> frontend -> backend");
    expect(text).toContain('get_blast_radius(target="backend")');
    expect(text).not.toContain("—");
  });

  it("edge prompt lists resolved links and caps them", () => {
    const evidence = Array.from({ length: SYSTEM_MAP_PROMPT_MAX_ROWS + 3 }, (_, i) => ({
      contract_id: `http::GET::/r${i}`,
      provider_repo: "backend",
      provider_file: "app/r.py",
      consumer_repo: "frontend",
      consumer_file: "src/client.ts",
    }));
    const text = buildEdgeAiPrompt({
      edge: { id: "frontend->backend:http", kind: "http", match_type: "exact", confidence: 0.85, weight: 180, structural: true },
      source: { id: "frontend", name: "frontend" },
      target: { id: "backend", name: "backend" },
      sentence: "frontend calls backend over HTTP.",
      weightLabel: "180 endpoints called",
      evidence,
      refs: [],
      flavor: "generic",
    });
    expect(text).toContain("frontend calls backend over HTTP.");
    expect(text).toContain("Confidence: 85%");
    expect(text).toContain("provider `backend:app/r.py`, consumer `frontend:src/client.ts`");
    expect(text).toContain("...and 3 more links not listed here.");
    expect(text).not.toContain("get_blast_radius");
  });

  it("edge prompt for co-change asks about a hidden dependency", () => {
    const text = buildEdgeAiPrompt({
      edge: { id: "a->b:co_change", kind: "co_change", match_type: "inferred", confidence: 0.7, weight: 2, structural: false },
      source: { id: "a", name: "a" },
      target: { id: "b", name: "b" },
      sentence: "Files in a and b change in the same commits.",
      weightLabel: "2 file pairs changed together",
      evidence: [],
      refs: [],
      pairs: [["x.py", "y.ts"]],
    });
    expect(text).toContain("`x.py` with `y.ts`");
    expect(text).toContain("hidden dependency");
  });

  it("cycle prompt names the loop and every edge with its evidence", () => {
    const text = buildCycleAiPrompt({
      names: ["backend", "frontend"],
      ids: ["backend", "frontend"],
      edges: [
        { id: "backend->frontend:http", source: "backend", target: "frontend", kind: "http", weight_label: "1 endpoint called", refs: ["http::POST::/api/revalidate/snapshot"] },
        { id: "frontend->backend:http", source: "frontend", target: "backend", kind: "http", weight_label: "180 endpoints called", refs: [] },
      ],
      flavor: "claude-code-mcp",
    });
    expect(text).toContain("## Dependency cycle: backend -> frontend -> backend");
    expect(text).toContain("`http::POST::/api/revalidate/snapshot`");
    expect(text).toContain("get_blast_radius");
  });

  it("blast radius prompt splits structural from co-change", () => {
    const text = buildBlastRadiusAiPrompt({
      target: { id: "backend", name: "backend" },
      impacted: [
        { id: "frontend", name: "frontend", repo: "frontend", distance: 1, score: 0.51, structural: true, edge_kinds: ["http"] },
        { id: "ui", name: "ui", repo: "repowise", distance: 2, score: 0.1, structural: false, edge_kinds: ["co_change"] },
      ],
      includeBehavioral: true,
    });
    expect(text).toContain("1 through structural dependencies, 1 only through co-change");
    expect(text).toContain("## Will break (structural)");
    expect(text).toContain("## May drift (co-change only)");
    expect(text).toContain("**ui** (`ui`, repo repowise): 2 hops, impact 0.10");
  });
});
