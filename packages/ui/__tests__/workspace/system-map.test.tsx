import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { CrossRepoBlastRadius, SystemEdge, SystemGraph, SystemNode } from "@repowise-dev/types";
import { SystemMap } from "../../src/workspace/system-map/system-map";
import { SystemMapFilters } from "../../src/workspace/system-map/system-map-filters";
import { SystemMapLegend } from "../../src/workspace/system-map/system-map-legend";
import { SystemMapInspector } from "../../src/workspace/system-map/system-map-inspector";
import { SystemMapBlastPanel } from "../../src/workspace/system-map/system-map-blast-panel";
import { SystemMapBreakingPanel } from "../../src/workspace/system-map/system-map-breaking-panel";
import type { BreakingChange, BreakingChangeReport } from "@repowise-dev/types";

// jsdom has no layout engine → stub ResizeObserver so React Flow can mount.
beforeAll(() => {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", RO);
});

function node(id: string, over: Partial<SystemNode> = {}): SystemNode {
  return {
    id,
    repo: id.split("::")[0] ?? id,
    service_path: null,
    name: id,
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
    confidence: 0.9,
    weight: 1,
    structural: true,
    contract_refs: [],
    ...over,
  };
}

function graph(nodes: SystemNode[], edges: SystemEdge[]): SystemGraph {
  return { version: 1, generated_at: "2026-06-19T00:00:00Z", nodes, edges, diagnostics: {} as never };
}

describe("SystemMap empty states", () => {
  it("shows the no-services state for an empty graph", () => {
    render(<SystemMap graph={graph([], [])} />);
    expect(screen.getByText(/no services to map/i)).toBeInTheDocument();
  });

  it("shows the no-relationships state when nodes exist but no edges", async () => {
    render(<SystemMap graph={graph([node("a"), node("b")], [])} />);
    // Layout runs async (ELK) before the empty-state resolves.
    expect(await screen.findByText(/no cross-repo relationships detected/i)).toBeInTheDocument();
  });

  it("surfaces an error", () => {
    render(<SystemMap graph={null} error={new Error("boom")} />);
    expect(screen.getByText(/couldn't load the system map/i)).toBeInTheDocument();
    expect(screen.getByText(/boom/i)).toBeInTheDocument();
  });
});

describe("SystemMapFilters", () => {
  it("only offers edge kinds present in the graph and toggles them", () => {
    const onToggleKind = vi.fn();
    render(
      <SystemMapFilters
        availableKinds={new Set(["http", "co_change"])}
        visibleKinds={new Set(["http", "co_change"])}
        onToggleKind={onToggleKind}
        collapsed={false}
        onToggleCollapsed={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "HTTP" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Co-change" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "gRPC" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "HTTP" }));
    expect(onToggleKind).toHaveBeenCalledWith("http");
  });

  it("toggles the collapse view", () => {
    const onToggleCollapsed = vi.fn();
    render(
      <SystemMapFilters
        availableKinds={new Set(["http"])}
        visibleKinds={new Set(["http"])}
        onToggleKind={() => {}}
        collapsed={false}
        onToggleCollapsed={onToggleCollapsed}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /service view/i }));
    expect(onToggleCollapsed).toHaveBeenCalled();
  });
});

describe("SystemMapLegend", () => {
  it("explains every edge kind, the match-type dashes, and the health scale", () => {
    render(<SystemMapLegend />);
    expect(screen.getByText("HTTP")).toBeInTheDocument();
    expect(screen.getByText("Co-change")).toBeInTheDocument();
    expect(screen.getByText(/exact \/ manual/i)).toBeInTheDocument();
    expect(screen.getByText(/at risk/i)).toBeInTheDocument();
  });
});

describe("SystemMapInspector", () => {
  const g = graph(
    [
      node("web", { kind: "frontend", consumer_count: 2, contract_types: ["http"] }),
      node("api", { provider_count: 2, contract_types: ["http"], is_orphan_provider: true }),
    ],
    [edge("web", "api", { contract_refs: ["http:GET /v1/users"] })],
  );

  it("renders a selected service with its counts and connections", () => {
    const onSelectNode = vi.fn();
    render(
      <SystemMapInspector
        selection={{ type: "node", id: "api" }}
        graph={g}
        onClose={() => {}}
        onSelectNode={onSelectNode}
      />,
    );
    expect(screen.getByText("api")).toBeInTheDocument();
    expect(screen.getByText("2 contracts")).toBeInTheDocument();
    expect(screen.getByText(/orphan provider/i)).toBeInTheDocument();
    // "Depended on by" lists web → clicking selects it
    fireEvent.click(screen.getByText("web"));
    expect(onSelectNode).toHaveBeenCalledWith("web");
  });

  it("renders a selected edge and opens its contract evidence", () => {
    const onOpenContract = vi.fn();
    render(
      <SystemMapInspector
        selection={{ type: "edge", id: "web->api" }}
        graph={g}
        onClose={() => {}}
        onSelectNode={() => {}}
        onOpenContract={onOpenContract}
      />,
    );
    expect(screen.getByText(/http relationship/i)).toBeInTheDocument();
    expect(screen.getByText("90%")).toBeInTheDocument(); // confidence
    fireEvent.click(screen.getByText("http:GET /v1/users"));
    expect(onOpenContract).toHaveBeenCalledWith("http:GET /v1/users");
  });

  it("renders nothing when there is no selection", () => {
    const { container } = render(
      <SystemMapInspector selection={null} graph={g} onClose={() => {}} onSelectNode={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("SystemMapBlastPanel", () => {
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

  it("renders nothing without a result", () => {
    const { container } = render(
      <SystemMapBlastPanel result={null} onSelectTarget={() => {}} onClear={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists impacted services split by structural vs behavioral", () => {
    render(
      <SystemMapBlastPanel
        result={result({
          impacted: [
            { id: "api", repo: "api", name: "api", kind: "service", distance: 1, score: 0.5, structural: true, edge_kinds: ["http"] },
            { id: "ops", repo: "ops", name: "ops", kind: "service", distance: 1, score: 0.2, structural: false, edge_kinds: ["co_change"] },
          ],
          impacted_repos: ["api", "ops"],
          structural_count: 1,
          behavioral_count: 1,
          total_impacted: 2,
        })}
        onSelectTarget={() => {}}
        onClear={() => {}}
      />,
    );
    expect(screen.getByText(/will break/i)).toBeInTheDocument();
    expect(screen.getByText(/may drift/i)).toBeInTheDocument();
    expect(screen.getByText("api")).toBeInTheDocument();
    expect(screen.getByText(/2 impacted across 2 other repo/i)).toBeInTheDocument();
  });

  it("re-targets when an impacted service is clicked", () => {
    const onSelectTarget = vi.fn();
    render(
      <SystemMapBlastPanel
        result={result({
          impacted: [
            { id: "api", repo: "api", name: "api", kind: "service", distance: 1, score: 0.5, structural: true, edge_kinds: ["http"] },
          ],
          total_impacted: 1,
        })}
        onSelectTarget={onSelectTarget}
        onClear={() => {}}
      />,
    );
    fireEvent.click(screen.getByText("api"));
    expect(onSelectTarget).toHaveBeenCalledWith("api");
  });

  it("shows the no-downstream state honestly", () => {
    render(
      <SystemMapBlastPanel result={result()} onSelectTarget={() => {}} onClear={() => {}} />,
    );
    expect(screen.getByText(/nothing downstream/i)).toBeInTheDocument();
  });
});

describe("SystemMapBreakingPanel", () => {
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
      detail: "http::GET::/users was removed",
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
      impacted_repos: ["web"],
      impacted_services: ["web"],
      total_impacted_consumers: 1,
    };
  }

  it("renders nothing without a report", () => {
    const { container } = render(<SystemMapBreakingPanel report={null} onClear={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists a changed provider with both code sides", () => {
    render(<SystemMapBreakingPanel report={report([change()])} onClear={() => {}} />);
    expect(screen.getByText("http::GET::/users")).toBeInTheDocument();
    expect(screen.getByText(/was removed/i)).toBeInTheDocument();
    expect(screen.getByText(/routes\.py/)).toBeInTheDocument(); // provider side
    expect(screen.getByText(/client\.ts/)).toBeInTheDocument(); // consumer side
    expect(screen.getByText(/1 breaking, 0 warning/i)).toBeInTheDocument();
  });

  it("focuses a node when a consumer is clicked", () => {
    const onSelectNode = vi.fn();
    render(<SystemMapBreakingPanel report={report([change()])} onSelectNode={onSelectNode} onClear={() => {}} />);
    fireEvent.click(screen.getByText(/client\.ts/));
    expect(onSelectNode).toHaveBeenCalledWith("web");
  });

  it("shows the clean state when there are no changes", () => {
    render(<SystemMapBreakingPanel report={report([])} onClear={() => {}} />);
    expect(screen.getByText(/no breaking changes/i)).toBeInTheDocument();
  });
});
