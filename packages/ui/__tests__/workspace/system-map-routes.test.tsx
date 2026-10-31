import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import type {
  ServiceLinkEntry,
  ServiceLinksBetween,
  SystemEdge,
  SystemGraph,
  SystemNode,
} from "@repowise-dev/types";
import { SystemMap } from "../../src/workspace/system-map/system-map";
import { SystemMapRoutesPanel } from "../../src/workspace/system-map/system-map-routes-panel";
import { SystemMapBetweenControl } from "../../src/workspace/system-map/system-map-between-control";
import { servicesBetween } from "../../src/workspace/system-map/between";
import { matchServices, pickerPath } from "../../src/workspace/system-map/between-picker";
import { focusColumnLabels } from "../../src/workspace/system-map/focus-labels";
import { focusColumns, placeFocusColumns } from "../../src/workspace/system-map/focus-columns";
import { DRAW_BUDGET } from "../../src/workspace/system-map/draw-budget";
import { DEFAULT_EDGE_FAMILIES, type EdgeFamily } from "../../src/workspace/system-map/edge-families";
import { planRoutes, routesLede, hiddenRoutesSentence } from "../../src/workspace/system-map/routes-between";
import { EDGE_FAMILY_COPY } from "../../src/workspace/system-map/edge-families";
import { buildPairAiPrompt } from "../../src/workspace/system-map/system-map-pair-prompt";
import { strategyNeedsCheck } from "../../src/workspace/link-provenance";
import { ESTATE_VIEW } from "../../src/workspace/system-map/view-state";

beforeAll(() => {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", RO);
});

function node(id: string): SystemNode {
  return {
    id,
    repo: id,
    service_path: null,
    name: id,
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
    id: `${source}->${target}${over.kind ? `:${over.kind}` : ""}`,
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

/** webapp calls ordering twice over HTTP and through an event; ordering pulls a package; hooks reaches catalog through webhooks. */
const graph: SystemGraph = {
  version: 1,
  generated_at: "2026-10-24T00:00:00Z",
  nodes: ["webapp", "ordering", "shared", "hooks", "webhooks", "catalog"].map(node),
  edges: [
    edge("webapp", "ordering", { weight: 2 }),
    edge("webapp", "ordering", { kind: "event" }),
    edge("ordering", "shared", { kind: "package" }),
    edge("hooks", "webhooks"),
    edge("webhooks", "catalog", { kind: "event" }),
  ],
};

function httpRow(i: number, direction: ServiceLinkEntry["direction"] = "a_to_b", strategy = "exact_id"): ServiceLinkEntry {
  return {
    kind: "http",
    direction,
    evidence: "contract",
    link: {
      contract_id: `http::GET::/api/orders/${i}`,
      contract_type: "http",
      match_type: "exact",
      strategy: strategy as never,
      confidence: 1,
      provider_repo: "ordering",
      provider_file: "Apis/OrdersApi.cs",
      provider_symbol: "Get",
      consumer_repo: "webapp",
      consumer_file: "Services/OrderingService.cs",
      consumer_symbol: "Fetch",
      provider_service: null,
      consumer_service: null,
      provider_symbol_id: null,
      consumer_symbol_id: null,
    },
  };
}

/** A fake server: 60 HTTP rows, 1 event, 2 package rows, with `kind`, `q` and paging. */
function fakeServer(over: { stale?: boolean } = {}) {
  const all: ServiceLinkEntry[] = [
    ...Array.from({ length: 60 }, (_, i) => httpRow(i, "a_to_b", i === 0 ? "unique_path" : "exact_id")),
    { ...httpRow(99), kind: "event" },
    ...[1, 2].map(
      (i): ServiceLinkEntry => ({
        kind: "package",
        direction: "b_to_a",
        evidence: "package",
        package_dep: { source_repo: "ordering", source_manifest: "Ordering.csproj", target_repo: "webapp", target_package: `pkg${i}`, kind: "nuget" },
      }),
    ),
  ];
  return vi.fn(async (a: string, b: string, opts: { kind?: string; q?: string; limit?: number; offset?: number }): Promise<ServiceLinksBetween> => {
    const filtered = all.filter((r) => !opts.q || JSON.stringify(r).includes(opts.q));
    const totals = new Map<string, number>();
    for (const r of filtered) totals.set(`${r.kind}|${r.direction}`, (totals.get(`${r.kind}|${r.direction}`) ?? 0) + 1);
    const rows = filtered.filter((r) => !opts.kind || r.kind === opts.kind);
    const offset = opts.offset ?? 0;
    return {
      a,
      b,
      ...(over.stale ? { stale_graph: true } : {}),
      links: rows.slice(offset, offset + (opts.limit ?? 50)),
      total: rows.length,
      totals: [...totals].map(([k, count]) => {
        const [kind, direction] = k.split("|") as [string, ServiceLinkEntry["direction"]];
        return { kind, direction, count };
      }),
    };
  });
}

const pair = servicesBetween(graph, "webapp", "ordering", { families: DEFAULT_EDGE_FAMILIES })!;

describe("routes between: pure parts", () => {
  it("keeps kinds in the active families and counts the hidden ones per family", () => {
    const plan = planRoutes(
      [
        { kind: "event", direction: "a_to_b", count: 1 },
        { kind: "http", direction: "a_to_b", count: 3 },
        { kind: "http", direction: "b_to_a", count: 1 },
        { kind: "package", direction: "b_to_a", count: 2 },
      ],
      DEFAULT_EDGE_FAMILIES,
    );
    expect(plan.sections.map((s) => [s.kind, s.total])).toEqual([["http", 4], ["event", 1]]);
    expect(plan.sections[0]!.byDirection).toEqual({ a_to_b: 3, b_to_a: 1 });
    expect(plan.shown).toBe(5);
    expect(plan.hidden).toEqual([{ family: "build", rows: 2 }]);
    expect(hiddenRoutesSentence(plan.hidden, EDGE_FAMILY_COPY)).toBe(
      "Not listed, because the map hides their family: 2 Build rows (package dependencies).",
    );
  });

  it("says each direction, or the paths through one service when there is no direct link", () => {
    const name = (id: string) => id;
    expect(routesLede(pair, name)).toEqual([
      "webapp depends on ordering through 3 links (HTTP 2, Event 1).",
      "Nothing in ordering depends on webapp.",
    ]);
    const indirect = servicesBetween(graph, "hooks", "catalog")!;
    expect(routesLede(indirect, name)).toEqual([
      "No direct link between hooks and catalog in the links shown.",
      "hooks reaches catalog through webhooks (HTTP, then Event).",
    ]);
    const none = servicesBetween(graph, "shared", "catalog")!;
    expect(routesLede(none, name)[1]).toBe("No path joins them through one other service either.");
  });

  it("names only the strategies a reader should check", () => {
    expect(strategyNeedsCheck("unique_path")).toBe(true);
    expect(strategyNeedsCheck("prefix_stripped")).toBe(true);
    expect(strategyNeedsCheck("exact_id")).toBe(false);
    expect(strategyNeedsCheck(null)).toBe(false);
  });

  it("builds a pair prompt from the loaded rows and says what was not loaded", () => {
    const text = buildPairAiPrompt({
      a: { id: "webapp", name: "webapp" },
      b: { id: "ordering", name: "ordering" },
      facts: ["webapp depends on ordering through 3 links (HTTP 2, Event 1)."],
      rows: [httpRow(1)],
      total: 4,
    });
    expect(text).toContain("## Contract between webapp (`webapp`) and ordering (`ordering`)");
    expect(text).toContain("`http::GET::/api/orders/1`: consumer `webapp:Services/OrderingService.cs`");
    expect(text).toContain("3 more routes exist that were not loaded here.");
  });
});

describe("SystemMapRoutesPanel", () => {
  const base = { graph, between: pair, families: DEFAULT_EDGE_FAMILIES, onClose: () => {} };

  it("lists kinds in the active families, paged per kind, and loads more", async () => {
    const load = fakeServer();
    render(<SystemMapRoutesPanel {...base} loadLinksBetween={load} contractHref={(r) => `/c?id=${r.contract_id}`} />);
    expect(await screen.findByText("HTTP · 60")).toBeInTheDocument();
    expect(await screen.findByText("Showing 50 of 60 routes")).toBeInTheDocument();
    expect(screen.getByText("Event · 1")).toBeInTheDocument();
    // Package is in Build, which the map hides: counted, offered, not listed.
    expect(screen.queryByText(/Package ·/)).toBeNull();
    expect(screen.getByText(/2 Build rows \(package dependencies\)/)).toBeInTheDocument();
    // Strategy named only where it needs checking.
    expect(screen.getAllByText("Matched by path only")).toHaveLength(1);
    expect(screen.queryByText("Matched by exact name")).toBeNull();
    expect(screen.getByRole("link", { name: "http::GET::/api/orders/1" })).toHaveAttribute("href", "/c?id=http::GET::/api/orders/1");

    fireEvent.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(screen.queryByText(/Showing 50 of 60/)).toBeNull());
    expect(screen.getAllByRole("link", { name: /http::GET::\/api\/orders\// })).toHaveLength(61); // 60 HTTP rows and the event row
    expect(load).toHaveBeenCalledWith("webapp", "ordering", expect.objectContaining({ kind: "http", offset: 50, limit: 50 }));
    // No row request for the hidden family.
    expect(load.mock.calls.some(([, , o]) => o.kind === "package")).toBe(false);
  });

  it("offers to show a hidden family", async () => {
    const onShowFamily = vi.fn();
    render(<SystemMapRoutesPanel {...base} loadLinksBetween={fakeServer()} onShowFamily={onShowFamily} />);
    fireEvent.click(await screen.findByRole("button", { name: "Show Build" }));
    expect(onShowFamily).toHaveBeenCalledWith("build" satisfies EdgeFamily);
  });

  it("passes the filter to the server and says when nothing matches", async () => {
    const load = fakeServer();
    render(<SystemMapRoutesPanel {...base} loadLinksBetween={load} />);
    await screen.findByText("HTTP · 60");
    fireEvent.change(screen.getByRole("searchbox", { name: "Filter routes" }), { target: { value: "orders/7" } });
    expect(await screen.findByText("HTTP · 1")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox", { name: "Filter routes" }), { target: { value: "nothing-here" } });
    expect(await screen.findByText('No routes match "nothing-here".')).toBeInTheDocument();
    expect(load).toHaveBeenCalledWith("webapp", "ordering", expect.objectContaining({ q: "orders/7" }));
  });

  it("states a stale graph quietly, and an unavailable loader", async () => {
    const { unmount } = render(<SystemMapRoutesPanel {...base} loadLinksBetween={fakeServer({ stale: true })} />);
    expect(await screen.findByRole("note")).toHaveTextContent(/read at different commits/);
    unmount();
    render(<SystemMapRoutesPanel {...base} />);
    expect(screen.getByText("Route detail is not available here.")).toBeInTheDocument();
  });

  it("drops a reply for an earlier filter, and retries a page that failed", async () => {
    const real = fakeServer();
    let failNext = true;
    let releaseStale: (() => void) | null = null;
    const load = vi.fn(async (a: string, b: string, opts: { kind?: string; q?: string; limit?: number; offset?: number }) => {
      // The first unfiltered HTTP page is held back until a filter has landed, then answers for the old query.
      if (opts.kind === "http" && !opts.q && releaseStale === null) {
        await new Promise<void>((resolve) => (releaseStale = resolve));
        return real(a, b, opts);
      }
      if (opts.kind === "event" && failNext) {
        failNext = false;
        throw new Error("down");
      }
      return real(a, b, opts);
    });
    render(<SystemMapRoutesPanel {...base} loadLinksBetween={load} />);
    expect(await screen.findByText("This page of routes could not be loaded.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("http::GET::/api/orders/99")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("searchbox", { name: "Filter routes" }), { target: { value: "orders/7" } });
    expect(await screen.findByText("HTTP · 1")).toBeInTheDocument();
    await act(async () => releaseStale?.());
    // The stale unfiltered page never replaces the filtered rows.
    expect(screen.queryByText("Showing 50 of 60 routes")).toBeNull();
    expect(screen.getAllByText(/http::GET::\/api\/orders\/7/).length).toBeGreaterThan(0);
  });

  it("says when the pair has no routes in the families shown", async () => {
    const empty = vi.fn(async (a: string, b: string): Promise<ServiceLinksBetween> => ({ a, b, links: [], total: 0, totals: [] }));
    render(<SystemMapRoutesPanel {...base} loadLinksBetween={empty} />);
    expect(await screen.findByText("No routes between webapp and ordering in the families shown.")).toBeInTheDocument();
  });
});

describe("focus level", () => {
  it("heads each column with its title and count", () => {
    const layout = focusColumns(graph, "ordering", { families: new Set(["runtime", "data", "build"]), budget: DRAW_BUDGET })!;
    const positions = placeFocusColumns(layout, { size: { width: 208, height: 96 } });
    const labels = focusColumnLabels(layout, positions);
    expect(labels.map((l) => l.label)).toEqual(["Called by · 1", "Depends on · 1"]);
    const first = positions.get("callers:webapp")!;
    expect(labels[0]).toMatchObject({ x: first.x, y: first.y - 28 });
  });

  it("renders three columns, card links in words, and Escape returns to the estate", async () => {
    const onViewChange = vi.fn();
    const { container } = render(
      <SystemMap graph={graph} view={{ ...ESTATE_VIEW, focus: "ordering" }} onViewChange={onViewChange} />,
    );
    expect(await screen.findByText("Called by · 1")).toBeInTheDocument();
    expect(screen.queryByText(/Depends on/)).toBeNull(); // the package link is in a hidden family
    expect(await screen.findByText("3 · HTTP 2, Event 1")).toBeInTheDocument();
    // Columns read left to right, so every focus card joins its connectors at its sides.
    for (const id of ["callers:webapp", "ordering"]) {
      const card = container.querySelector(`.react-flow__node[data-id="${id}"]`)!;
      expect(card.querySelector(".react-flow__handle-left")).not.toBeNull();
      expect(card.querySelector(".react-flow__handle-right")).not.toBeNull();
      expect(card.querySelector(".react-flow__handle-top")).toBeNull();
    }
    await act(async () => {
      fireEvent.keyDown(container.querySelector(".react-flow")!, { key: "Escape" });
    });
    expect(onViewChange).toHaveBeenCalledWith(expect.objectContaining({ focus: null }));
  });

  it("focuses a service on double-click", async () => {
    const onViewChange = vi.fn();
    const { container } = render(<SystemMap graph={graph} view={ESTATE_VIEW} onViewChange={onViewChange} />);
    const card = await vi.waitFor(() => {
      const el = container.querySelector('.react-flow__node[data-id="webapp"]');
      if (!el) throw new Error("not drawn yet");
      return el;
    });
    // Off the focus level cards keep their top and bottom handles.
    expect(card.querySelector(".react-flow__handle-top")).not.toBeNull();
    fireEvent.doubleClick(card);
    expect(onViewChange).toHaveBeenCalledWith(expect.objectContaining({ focus: "webapp" }));
  });
});

describe("Between", () => {
  it("suggests services by name and finds a route within two hops, either way", () => {
    expect(matchServices(graph, "web", 5).map((r) => r.node_id)).toEqual(["webapp", "webhooks"]);
    expect(matchServices(graph, "", 2).map((r) => r.node_id)).toEqual(["ordering", "webapp"]);
    expect(pickerPath(graph, "hooks", "catalog", DEFAULT_EDGE_FAMILIES).path).toEqual(["hooks", "webhooks", "catalog"]);
    expect(pickerPath(graph, "ordering", "webapp", DEFAULT_EDGE_FAMILIES).path).toEqual(["ordering", "webapp"]);
    expect(pickerPath(graph, "shared", "catalog", DEFAULT_EDGE_FAMILIES).path).toEqual([]);
    expect(() => pickerPath(graph, "nope", "catalog", DEFAULT_EDGE_FAMILIES)).toThrow();
  });

  it("opens the Between level from the picker", async () => {
    const onBetween = vi.fn();
    const onOpenChange = vi.fn();
    render(
      <SystemMapBetweenControl graph={graph} families={DEFAULT_EDGE_FAMILIES} open onOpenChange={onOpenChange} from="webapp" onBetween={onBetween} />,
    );
    fireEvent.change(screen.getByPlaceholderText("To service..."), { target: { value: "ordering" } });
    fireEvent.click(screen.getByRole("button", { name: "Show routes" }));
    await waitFor(() => expect(onBetween).toHaveBeenCalledWith("webapp", "ordering"));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("says when no route joins the pair", async () => {
    const onBetween = vi.fn();
    render(
      <SystemMapBetweenControl graph={graph} families={DEFAULT_EDGE_FAMILIES} open onOpenChange={() => {}} from="shared" onBetween={onBetween} />,
    );
    fireEvent.change(screen.getByPlaceholderText("To service..."), { target: { value: "catalog" } });
    fireEvent.click(screen.getByRole("button", { name: "Show routes" }));
    expect(await screen.findByText(/No route joins these services within two hops/)).toBeInTheDocument();
    expect(onBetween).not.toHaveBeenCalled();
  });

  it("shows the routes panel in the rail at the Between level, and the drawer's Focus verb", async () => {
    const onViewChange = vi.fn();
    render(
      <SystemMap
        graph={graph}
        view={{ ...ESTATE_VIEW, between: { a: "webapp", b: "ordering" } }}
        onViewChange={onViewChange}
        loadLinksBetween={fakeServer()}
      />,
    );
    expect(await screen.findByRole("region", { name: "webapp and ordering" })).toBeInTheDocument();
    expect(await screen.findByText("HTTP · 60")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close details" }));
    expect(onViewChange).toHaveBeenCalledWith(expect.objectContaining({ between: null }));
  });

  it("lists an edge's routes in its drawer and offers all routes between the pair", async () => {
    const onViewChange = vi.fn();
    render(
      <SystemMap
        graph={graph}
        selection={{ type: "edge", id: "webapp->ordering" }}
        onSelectionChange={() => {}}
        view={ESTATE_VIEW}
        onViewChange={onViewChange}
        loadLinksBetween={fakeServer()}
      />,
    );
    expect(await screen.findByText("Showing 20 of 60 routes.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "All routes between them" }));
    expect(onViewChange).toHaveBeenCalledWith(expect.objectContaining({ between: { a: "webapp", b: "ordering" } }));
  });
});
