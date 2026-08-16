import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
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

  it("shows co-change evidence as the file pair it is, not as a contract link", () => {
    const behavioral = graph(
      [node("web"), node("api")],
      [
        edge("web", "api", {
          kind: "co_change",
          structural: false,
          weight: 1,
          contract_refs: ["app/services/overview.py~src/lib/api/types.ts"],
        }),
      ],
    );
    render(
      <SystemMapInspector
        selection={{ type: "edge", id: "web->api" }}
        graph={behavioral}
        onClose={() => {}}
        onSelectNode={() => {}}
        onOpenContract={vi.fn()}
      />,
    );
    // Both sides of the pair are shown, split on the "~" separator.
    expect(screen.getByText("app/services/overview.py")).toBeInTheDocument();
    expect(screen.getByText(/src\/lib\/api\/types\.ts/)).toBeInTheDocument();
    expect(screen.getByText(/co-changed files/i)).toBeInTheDocument();
    // A file pair cannot be resolved as a contract, so nothing offers to try.
    expect(screen.queryByTitle(/contracts page/i)).not.toBeInTheDocument();
    expect(screen.queryByText("app/services/overview.py~src/lib/api/types.ts")).not.toBeInTheDocument();
  });

  it("still offers the Contracts drill-down for structural evidence", () => {
    render(
      <SystemMapInspector
        selection={{ type: "edge", id: "web->api" }}
        graph={g}
        onClose={() => {}}
        onSelectNode={() => {}}
        onOpenContract={vi.fn()}
      />,
    );
    expect(screen.getByTitle(/contracts page/i)).toBeInTheDocument();
  });
});

describe("SystemMap chrome placement", () => {
  const g = graph(
    [node("web", { kind: "frontend" }), node("api", { provider_count: 2 })],
    [edge("web", "api", { contract_refs: ["http:GET /v1/users"] })],
  );

  it("puts the inspector in a rail beside the canvas, not on it", async () => {
    const { container } = render(
      <SystemMap graph={g} selection={{ type: "node", id: "api" }} onSelectionChange={() => {}} />,
    );
    const rail = await screen.findByRole("complementary");
    // The inspector renders inside the rail…
    expect(within(rail).getByText("2 contracts")).toBeInTheDocument();
    // …and the rail is a peer of the canvas, not a child of it.
    const flow = container.querySelector(".react-flow")!;
    expect(flow.contains(rail)).toBe(false);
  });

  it("keeps host panels off the canvas too", async () => {
    const { container } = render(
      <SystemMap
        graph={g}
        selection={{ type: "node", id: "api" }}
        onSelectionChange={() => {}}
        rail={<div>host panel</div>}
      />,
    );
    const rail = await screen.findByRole("complementary");
    const flow = container.querySelector(".react-flow")! as HTMLElement;
    // The host's panel and the inspector share the rail rather than stacking on
    // the same canvas corner, which is what they used to do.
    expect(within(rail).getByText("host panel")).toBeInTheDocument();
    expect(within(flow).queryByText("host panel")).toBeNull();
    expect(within(flow).queryByText("2 contracts")).toBeNull();
  });

  it("notifies the current onSelectionChange, not the one it mounted with", async () => {
    const stale = vi.fn();
    const fresh = vi.fn();
    const { rerender } = render(
      <SystemMap graph={g} selection={{ type: "node", id: "api" }} onSelectionChange={stale} />,
    );
    await screen.findByRole("complementary");

    rerender(<SystemMap graph={g} selection={{ type: "node", id: "api" }} onSelectionChange={fresh} />);

    // "Depended on by" lists web; selecting it goes through a handler that is
    // memoised once for the component's lifetime.
    fireEvent.click(screen.getByText("web"));

    expect(fresh).toHaveBeenCalledWith({ type: "node", id: "web" });
    expect(stale).not.toHaveBeenCalled();
  });

  it("resolves a toggle against the current selection, not the mounted one", async () => {
    const onSelectionChange = vi.fn();
    const { rerender } = render(
      <SystemMap graph={g} selection={null} onSelectionChange={onSelectionChange} rail={<div>open</div>} />,
    );
    await screen.findByRole("complementary");

    // Selection arrives from the host after mount (e.g. from the URL).
    rerender(
      <SystemMap
        graph={g}
        selection={{ type: "node", id: "api" }}
        onSelectionChange={onSelectionChange}
        rail={<div>open</div>}
      />,
    );

    // Closing reads the live selection through the same lifetime-memoised path.
    fireEvent.click(screen.getByLabelText("Close inspector"));
    expect(onSelectionChange).toHaveBeenCalledWith(null);
  });

  it("collapses the rail column when nothing is selected and no panel is open", () => {
    const { container } = render(<SystemMap graph={g} rail={null} />);
    expect(container.querySelector("aside")).toBeNull();
  });

  it("resolves a collapsed-view selection against the graph actually drawn", async () => {
    const withServices = graph(
      [
        node("api::svc/a", { repo: "api", provider_count: 2 }),
        node("api::svc/b", { repo: "api", provider_count: 3 }),
        node("web", { repo: "web", kind: "frontend" }),
      ],
      [edge("web", "api::svc/a")],
    );
    render(
      <SystemMap
        graph={withServices}
        // A collapsed edge id: it exists only once services are merged. The
        // uncollapsed graph spells the same edge "web->api::svc/a".
        selection={{ type: "edge", id: "web->api::http" }}
        onSelectionChange={() => {}}
      />,
    );
    // Service view: the id is not in the drawn graph, so there is no inspector.
    expect(screen.queryByText(/http relationship/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Service view"));

    // Repo view: the same id now resolves.
    expect(await screen.findByText(/http relationship/i)).toBeInTheDocument();
  });

  it("shows the merged counts in repo view rather than a same-named service's", async () => {
    const withServices = graph(
      [
        node("api", { repo: "api", provider_count: 2 }),
        node("api::svc/b", { repo: "api", provider_count: 3 }),
        node("web", { repo: "web", kind: "frontend" }),
      ],
      [edge("web", "api")],
    );
    render(
      <SystemMap
        graph={withServices}
        selection={{ type: "node", id: "api" }}
        onSelectionChange={() => {}}
      />,
    );
    expect(await screen.findByText("2 contracts")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Service view"));

    // 2 + 3, not 2: the repo node is the merge, not the same-named service.
    expect(await screen.findByText("5 contracts")).toBeInTheDocument();
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
