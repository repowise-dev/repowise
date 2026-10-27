import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, within, waitFor } from "@testing-library/react";
import type { CrossRepoBlastRadius, GraphEvidence, SystemEdge, SystemGraph, SystemNode } from "@repowise-dev/types";
import { SystemMap } from "../../src/workspace/system-map/system-map";
import { Segmented, SystemMapFilters } from "../../src/workspace/system-map/system-map-filters";
import { SystemMapLegend } from "../../src/workspace/system-map/system-map-legend";
import { SystemMapEvidenceNotice } from "../../src/workspace/system-map/system-map-evidence-notice";
import { SystemMapDrawer, selectionRepo } from "../../src/workspace/system-map/system-map-drawer";
import { SystemMapFindings } from "../../src/workspace/system-map/system-map-findings";
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
  it("offers one chip per family present, with counts, Build off, and toggles a family", () => {
    const onToggleFamily = vi.fn();
    render(
      <SystemMapFilters
        familyCounts={{ runtime: { edges: 18, links: 40 }, build: { edges: 26, links: 26 } }}
        kindCounts={{ runtime: { http: 7, event: 9, grpc: 2 }, build: { package: 26 } }}
        families={new Set(["runtime", "data"])}
        onToggleFamily={onToggleFamily}
      />,
    );
    const runtime = screen.getByRole("button", { name: /runtime/i });
    expect(runtime).toHaveAttribute("aria-pressed", "true");
    expect(runtime).toHaveTextContent("18");
    expect(runtime.getAttribute("title")).toMatch(/HTTP 7, gRPC 2, Event 9/);
    const build = screen.getByRole("button", { name: /build/i });
    expect(build).toHaveAttribute("aria-pressed", "false");
    expect(build).toHaveTextContent("26");
    // Data is drawn by default but absent here, so it gets no dead chip.
    expect(screen.queryByRole("button", { name: /data/i })).not.toBeInTheDocument();
    fireEvent.click(build);
    expect(onToggleFamily).toHaveBeenCalledWith("build");
  });
});

describe("Segmented", () => {
  it("keeps a disabled option visible with its reason", () => {
    const onChange = vi.fn();
    render(
      <Segmented
        label="Map lens"
        value="none"
        options={[
          { value: "none", label: "None" },
          { value: "breaking", label: "Breaking changes", count: "0", disabledReason: "No provider contract changed." },
        ]}
        onChange={onChange}
      />,
    );
    const option = screen.getByRole("radio", { name: /breaking changes/i });
    expect(option).toHaveAttribute("aria-disabled", "true");
    expect(option).toHaveAccessibleDescription("No provider contract changed.");
    fireEvent.click(option);
    expect(onChange).not.toHaveBeenCalled();
    // A tap shows the reason as visible text, not only a tooltip.
    expect(screen.getByText("No provider contract changed.", { selector: "p" })).toBeInTheDocument();
  });

  it("keeps the group in the tab order when the active option is disabled", () => {
    render(
      <Segmented
        label="Map lens"
        value="breaking"
        options={[
          { value: "none", label: "None" },
          { value: "breaking", label: "Breaking changes", disabledReason: "Nothing changed." },
        ]}
        onChange={() => {}}
      />,
    );
    expect(screen.getByRole("radio", { name: "None" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("radio", { name: /breaking changes/i })).toHaveAttribute("tabindex", "-1");
  });
});

describe("SystemMapFindings", () => {
  const g = graph([node("a"), node("b")], [edge("a", "b"), edge("b", "a", { id: "b->a" })]);
  const base = { graph: g, onSelect: () => {}, onLensChange: () => {} };
  const conformance = {
    version: 1,
    generated_at: "t",
    rules_evaluated: 0,
    violations: [],
    cycles: [],
    violation_count: 0,
    cycle_count: 0,
    total_cycles: 0,
    violating_repos: [],
  };
  const breaking = {
    version: 1,
    generated_at: "t",
    changes: [],
    total: 0,
    breaking_count: 0,
    warning_count: 0,
    impacted_repos: [],
    impacted_services: [],
    total_impacted_consumers: 0,
  };

  it("does not call the workspace clean while a report is loading or failed", () => {
    const { rerender } = render(<SystemMapFindings {...base} conformance={null} breaking={breaking} />);
    expect(screen.queryByText(/no dependency cycles/i)).not.toBeInTheDocument();
    expect(screen.getByText(/loading the conformance report/i)).toBeInTheDocument();

    rerender(<SystemMapFindings {...base} conformance={null} conformanceError breaking={breaking} />);
    expect(screen.getByText("Could not load the conformance report.")).toBeInTheDocument();
    expect(screen.queryByText(/no dependency cycles/i)).not.toBeInTheDocument();

    rerender(<SystemMapFindings {...base} conformance={conformance} breaking={breaking} />);
    expect(screen.getByText(/no dependency cycles, rule violations or breaking changes/i)).toBeInTheDocument();
  });

  it("lists a cycle with a prompt to break it", () => {
    render(
      <SystemMapFindings
        {...base}
        conformance={{ ...conformance, cycles: [{ nodes: ["a", "b"], edge_ids: ["a->b", "b->a"], length: 2 }], cycle_count: 1, total_cycles: 1 }}
        breaking={breaking}
      />,
    );
    expect(screen.getByRole("button", { name: "Break the cycle" })).toBeInTheDocument();
    expect(screen.queryByText(/no dependency cycles/i)).not.toBeInTheDocument();
  });

  it("never counts in-service calls as unmatched, even on an older artifact", () => {
    const call = (reason: "no_provider" | "internal_only") => ({
      repo: "a", file_path: "x.ts", contract_id: "GET /x", contract_type: "http", reason,
    });
    // Before in-service calls were split out, they sat in the unmatched list.
    const diagnostics = {
      unmatched_consumers: [call("no_provider"), call("internal_only"), call("internal_only")],
      unmatched_by_reason: { no_provider: 1, internal_only: 2 },
    } as never;
    render(<SystemMapFindings {...base} conformance={conformance} breaking={breaking} diagnostics={diagnostics} />);
    expect(screen.getByText(/1 call to a contract match no provider in the workspace/)).toBeInTheDocument();
    expect(screen.queryByText(/inside their own service/)).not.toBeInTheDocument();
  });
});

describe("SystemMapLegend", () => {
  it("keys only the line styles drawn, plus the three health bands", () => {
    render(<SystemMapLegend matchTypes={new Set(["exact", "inferred"])} />);
    expect(screen.getByText(/structural, exact match/i)).toBeInTheDocument();
    expect(screen.getByText(/co-change from git history/i)).toBeInTheDocument();
    expect(screen.queryByText(/candidate/i)).not.toBeInTheDocument();
    expect(screen.getByText("Healthy 8+")).toBeInTheDocument();
    expect(screen.getByText("Alert below 4")).toBeInTheDocument();
    expect(screen.queryByText(/gateway route/i)).not.toBeInTheDocument();
  });

  it("names gateway-routed lines in words when the graph has any", () => {
    render(<SystemMapLegend matchTypes={new Set(["exact"])} gatewayRouted={2} />);
    expect(screen.getByText(/lines run through a gateway route/i)).toBeInTheDocument();
  });
});

describe("SystemMapEvidenceNotice", () => {
  const evidence = (state: GraphEvidence["state"], summary: string): GraphEvidence => ({
    state,
    determined: state !== "insufficient",
    summary,
    reasons: [],
    services: 3,
    runtime_edges: 0,
    build_edges: 2,
    providers: 4,
    consumers: 1,
  });

  it("renders nothing when the evidence is measured or absent", () => {
    const { container, rerender } = render(<SystemMapEvidenceNotice evidence={evidence("measured", "")} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<SystemMapEvidenceNotice evidence={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("names the state and the reason as one sentence", () => {
    const { rerender } = render(
      <SystemMapEvidenceNotice evidence={evidence("partial", "every detected dependency is a build reference")} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Partial evidence");
    expect(screen.getByText("Every detected dependency is a build reference.")).toBeInTheDocument();
    rerender(
      <SystemMapEvidenceNotice
        evidence={evidence("insufficient", "no service carries a contract")}
        insufficientLabel="Unknown here"
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Unknown here");
  });
});

describe("SystemMapDrawer", () => {
  const g = graph(
    [
      node("web", { kind: "frontend", consumer_count: 2, contract_types: ["http"] }),
      node("api", { provider_count: 2, contract_types: ["http"] }),
    ],
    [edge("web", "api", { weight: 2, contract_refs: ["http::GET::/v1/users", "http::GET::/v1/users"] })],
  );
  const base = { graph: g, onClose: () => {} };

  it("renders a service with its role, neighbours and contracts, and selects a neighbour", () => {
    const onSelect = vi.fn();
    render(
      <SystemMapDrawer
        {...base}
        selection={{ type: "node", id: "api" }}
        onSelect={onSelect}
        roleByNodeId={
          new Map([
            ["api", { id: "api", repo: "api", name: "api", visibility_fan_in: 2, visibility_fan_out: 1, role: "shared" as const }],
          ])
        }
        repoContracts={{
          contracts: [
            { contract_id: "http::GET::/v1/users", contract_type: "http", role: "provider", repo: "api", file_path: "routes.py", line: 4 },
          ],
          links: [],
          total: 1,
        }}
        contractHref={(r) => `/c?contract=${r.contract_id}&repo=${r.repo}&file=${r.file_path ?? ""}`}
      />,
    );
    expect(screen.getByText("Shared")).toBeInTheDocument();
    expect(screen.getByText(/1 service reaches it/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "http::GET::/v1/users" })).toHaveAttribute(
      "href",
      "/c?contract=http::GET::/v1/users&repo=api&file=routes.py",
    );
    fireEvent.click(screen.getByRole("button", { name: /^web/ }));
    expect(onSelect).toHaveBeenCalledWith({ type: "node", id: "web" });
  });

  it("offers the blast radius for a service", () => {
    const onShowBlastRadius = vi.fn();
    render(
      <SystemMapDrawer {...base} selection={{ type: "node", id: "api" }} onSelect={() => {}} onShowBlastRadius={onShowBlastRadius} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show blast radius" }));
    expect(onShowBlastRadius).toHaveBeenCalledWith("api");
  });

  it("renders a relationship as a sentence with its weight unit and deduplicated evidence", () => {
    render(
      <SystemMapDrawer
        {...base}
        selection={{ type: "edge", id: "web->api" }}
        onSelect={() => {}}
        contractHref={(r) => `/c?contract=${r.contract_id}&repo=${r.repo}`}
      />,
    );
    expect(screen.getByText("web calls api over HTTP.")).toBeInTheDocument();
    expect(screen.getByText("2 endpoints called")).toBeInTheDocument();
    expect(screen.getByText("90%")).toBeInTheDocument();
    // Two refs, one contract: listed once, linked to the provider side.
    expect(screen.getAllByRole("link", { name: "http::GET::/v1/users" })).toHaveLength(1);
    expect(screen.getByRole("link", { name: "http::GET::/v1/users" })).toHaveAttribute(
      "href",
      "/c?contract=http::GET::/v1/users&repo=api",
    );
  });

  it("shows co-change evidence as the file pair it is, not as a contract link", () => {
    const behavioral = graph(
      [node("web"), node("api")],
      [
        edge("web", "api", {
          kind: "co_change",
          match_type: "inferred",
          structural: false,
          weight: 1,
          contract_refs: ["app/services/overview.py~src/lib/api/types.ts"],
        }),
      ],
    );
    render(
      <SystemMapDrawer
        graph={behavioral}
        onClose={() => {}}
        onSelect={() => {}}
        selection={{ type: "edge", id: "web->api" }}
        contractHref={() => "/c"}
      />,
    );
    expect(document.body.textContent).toContain("web/app/services/overview.py");
    expect(document.body.textContent).toContain("with api/src/lib/api/types.ts");
    expect(screen.queryByRole("link", { name: /overview\.py/ })).not.toBeInTheDocument();
  });

  it("states a lean edge's evidence count without claiming it kept none", () => {
    const { contract_refs: _refs, ...lean } = edge("web", "api", { weight: 2, ref_count: 2 });
    const g2 = graph([node("web"), node("api")], [lean]);
    render(
      <SystemMapDrawer graph={g2} onClose={() => {}} onSelect={() => {}} selection={{ type: "edge", id: "web->api" }} />,
    );
    expect(screen.getByText("2 references")).toBeInTheDocument();
    expect(screen.getByText(/The graph kept 2 contract ids for this edge/)).toBeInTheDocument();
    expect(screen.queryByText(/kept no evidence/)).not.toBeInTheDocument();
  });

  it("reads a service's diagnostics from the host when the graph has none", () => {
    const { diagnostics: _d, ...lean } = g;
    render(
      <SystemMapDrawer
        {...base}
        graph={lean}
        selection={{ type: "node", id: "web" }}
        onSelect={() => {}}
        diagnostics={{
          unmatched_consumers: [
            { repo: "web", file_path: "client.ts", contract_id: "http::GET::/x", contract_type: "http", reason: "no_provider" },
          ],
        } as never}
      />,
    );
    expect(screen.getByText(/1 call from here match/)).toBeInTheDocument();
  });

  it("renders nothing when there is no selection", () => {
    render(<SystemMapDrawer {...base} selection={null} onSelect={() => {}} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("selectionRepo", () => {
  const g = graph(
    [node("api::svc/a", { repo: "api", service_path: "svc/a" }), node("web")],
    [edge("web", "api::svc/a")],
  );
  it("names a service's repository, and none for a relationship (its routes come from the loader) or an aggregate", () => {
    expect(selectionRepo(g, { type: "node", id: "api::svc/a" })).toBe("api");
    expect(selectionRepo(g, { type: "group", id: "tag:api" })).toBeNull();
    expect(selectionRepo(g, { type: "edge", id: "web->api::svc/a" })).toBeNull();
    expect(selectionRepo(g, null)).toBeNull();
  });
});

describe("SystemMap chrome placement", () => {
  const g = graph(
    [node("web", { kind: "frontend" }), node("api", { provider_count: 2 })],
    [edge("web", "api", { contract_refs: ["http:GET /v1/users"] })],
  );

  it("opens the details in the rail, beside the canvas, for a selection", async () => {
    const { container } = render(
      <SystemMap graph={g} selection={{ type: "node", id: "api" }} onSelectionChange={() => {}} />,
    );
    const dialog = await screen.findByRole("region", { name: "api" });
    expect(dialog.closest("aside")).not.toBeNull();
    expect(within(dialog).getByText(/api provides 2 contracts/i)).toBeInTheDocument();
    const flow = container.querySelector(".react-flow")!;
    expect(flow.contains(dialog)).toBe(false);
  });

  it("reports aggregate, layout and paint to onPhase, in order", async () => {
    const onPhase = vi.fn();
    render(<SystemMap graph={g} onPhase={onPhase} />);
    await waitFor(() => expect(onPhase).toHaveBeenLastCalledWith("paint"));
    expect(onPhase.mock.calls.map(([p]) => p)).toEqual(["aggregate", "layout", "paint"]);
  });

  it("reports no phase when there is no graph to lay out", async () => {
    const onPhase = vi.fn();
    render(<SystemMap graph={null} error={new Error("boom")} onPhase={onPhase} />);
    await new Promise((r) => setTimeout(r, 50));
    expect(onPhase).not.toHaveBeenCalled();
  });

  it("reports layout only once the new graph's layout has run", async () => {
    const onPhase = vi.fn();
    const { rerender } = render(<SystemMap graph={g} onPhase={onPhase} />);
    await waitFor(() => expect(onPhase).toHaveBeenLastCalledWith("paint"));
    onPhase.mockClear();
    const next = graph([...g.nodes, node("db")], [...g.edges, edge("api", "db", { kind: "db" })]);
    rerender(<SystemMap graph={next} onPhase={onPhase} />);
    await waitFor(() => expect(onPhase).toHaveBeenLastCalledWith("paint"));
    expect(onPhase.mock.calls.map(([p]) => p)).toEqual(["aggregate", "layout", "paint"]);
  });

  it("puts the host's toolbar in the section header, not on the canvas", () => {
    const { container } = render(<SystemMap graph={g} toolbar={<div>lens bar</div>} />);
    const flow = container.querySelector(".react-flow")! as HTMLElement;
    expect(screen.getByText("lens bar")).toBeInTheDocument();
    expect(within(flow).queryByText("lens bar")).toBeNull();
  });

  it("notifies the current onSelectionChange, not the one it mounted with", async () => {
    const stale = vi.fn();
    const fresh = vi.fn();
    const { rerender } = render(
      <SystemMap graph={g} selection={{ type: "node", id: "api" }} onSelectionChange={stale} />,
    );
    await screen.findByRole("region", { name: "api" });

    rerender(<SystemMap graph={g} selection={{ type: "node", id: "api" }} onSelectionChange={fresh} />);

    // "Depended on by" lists web; selecting it goes through a lifetime-memoised handler.
    fireEvent.click(within(screen.getByRole("region", { name: "api" })).getByRole("button", { name: /^web/ }));

    expect(fresh).toHaveBeenCalledWith({ type: "node", id: "web" });
    expect(stale).not.toHaveBeenCalled();
  });

  it("closing the rail clears the selection the host holds", async () => {
    const onSelectionChange = vi.fn();
    render(<SystemMap graph={g} selection={{ type: "node", id: "api" }} onSelectionChange={onSelectionChange} />);
    await screen.findByRole("region", { name: "api" });
    fireEvent.click(screen.getByLabelText("Close details"));
    expect(onSelectionChange).toHaveBeenCalledWith(null);
  });

  it("states what it draws", async () => {
    render(<SystemMap graph={g} />);
    expect(await screen.findByText(/drawing 2 services, joined by 1 line between services\./i)).toBeInTheDocument();
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
