import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, act, within } from "@testing-library/react";
import type { SystemEdge, SystemGraph, SystemNode } from "@repowise-dev/types";
import { SystemMap } from "../../src/workspace/system-map/system-map";
import { buildSearchIndex, pickToView, routeHits, searchMap, type SystemMapRouteLink } from "../../src/workspace/system-map/search-index";
import { shortcutFor } from "../../src/workspace/system-map/shortcuts";
import { nextSort, serviceRows, sortServiceRows } from "../../src/workspace/system-map/service-rows";
import { levelAnnouncement } from "../../src/workspace/system-map/announce";
import { buildLevelView } from "../../src/workspace/system-map/levels";
import { DEFAULT_NAV_COPY } from "../../src/workspace/system-map/nav-copy";
import { ESTATE_VIEW, parseMapView, sameMapView, toLevelViewState, writeMapView, type MapViewState } from "../../src/workspace/system-map/view-state";

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
    confidence: 1,
    weight: 1,
    structural: true,
    contract_refs: [],
    ...over,
  };
}

const grouped: SystemGraph = {
  version: 1,
  generated_at: "2026-10-24T00:00:00Z",
  nodes: [
    node("webapp", { group: "tag:storefront", provider_count: 1, consumer_count: 4 }),
    node("mobile", { group: "tag:storefront" }),
    node("ordering.api", { group: "tag:ordering", provider_count: 6 }),
    node("ordering.worker", { group: "tag:ordering" }),
    node("shared.lib"),
  ],
  edges: [
    edge("webapp", "ordering.api", { weight: 2 }),
    edge("mobile", "ordering.api", { kind: "event" }),
    edge("ordering.worker", "ordering.api", { kind: "event" }),
    edge("ordering.api", "shared.lib", { id: "pkg", kind: "package", weight: 5 }),
  ],
  groups: [
    { id: "tag:storefront", label: "Storefront", source: "tag", member_count: 2 },
    { id: "tag:ordering", label: "Ordering", source: "tag", member_count: 2 },
  ],
};

const flat: SystemGraph = { ...grouped, nodes: grouped.nodes.map(({ group: _g, ...n }) => n), groups: [] };

describe("search ranking", () => {
  const index = buildSearchIndex(grouped);

  it("ranks the closest service name first, services before groups, and needs two characters", () => {
    const hits = searchMap(index, "ordering");
    expect(hits[0]).toMatchObject({ kind: "service", id: "ordering.api" });
    expect(hits.map((h) => h.kind)).toEqual(["service", "service", "group"]);
    expect(hits.at(-1)).toMatchObject({ kind: "group", id: "tag:ordering", label: "Ordering" });
    expect(searchMap(index, "o")).toEqual([]);
    expect(searchMap(index, "  ")).toEqual([]);
  });

  it("finds a group by its label and a service by a typo", () => {
    expect(searchMap(index, "storefront")[0]).toMatchObject({ kind: "group", id: "tag:storefront" });
    expect(searchMap(index, "webap")[0]).toMatchObject({ kind: "service", id: "webapp" });
  });

  it("caps each section", () => {
    const many = { nodes: Array.from({ length: 20 }, (_, i) => node(`svc-${i}`)), groups: [] };
    expect(searchMap(buildSearchIndex(many), "svc", 4)).toHaveLength(4);
  });
});

describe("route hits", () => {
  const link = (contract_id: string, provider_repo: string, consumer_repo: string): SystemMapRouteLink => ({
    contract_id,
    contract_type: "http",
    provider_repo,
    provider_file: "a.cs",
    consumer_repo,
    consumer_file: "b.cs",
  });
  const resolve = buildSearchIndex(grouped).resolve;

  it("groups links per route and provider, names the top caller, and orders by links", () => {
    const hits = routeHits(
      [
        link("GET /orders", "ordering.api", "webapp"),
        link("GET /orders", "ordering.api", "webapp"),
        link("GET /orders", "ordering.api", "mobile"),
        link("GET /status", "ordering.api", "mobile"),
      ],
      resolve,
    );
    expect(hits).toEqual([
      expect.objectContaining({ kind: "route", label: "GET /orders", provider: "ordering.api", consumer: "webapp", otherConsumers: 1 }),
      expect.objectContaining({ label: "GET /status", consumer: "mobile", otherConsumers: 0 }),
    ]);
  });

  it("drops a caller in the provider's own service and a provider not on the map", () => {
    const hits = routeHits([link("GET /self", "ordering.api", "ordering.api"), link("GET /gone", "elsewhere", "webapp")], resolve);
    expect(hits).toEqual([expect.objectContaining({ label: "GET /self", consumer: null })]);
  });
});

describe("a pick is a level change", () => {
  it("focuses a service, opens a group, and compares a route's caller with its provider", () => {
    const from: MapViewState = { ...ESTATE_VIEW, between: { a: "x", b: "y" } };
    expect(pickToView(from, { kind: "service", id: "webapp", label: "webapp", detail: "" })).toMatchObject({ focus: "webapp", between: null });
    expect(pickToView({ ...ESTATE_VIEW, focus: "webapp" }, { kind: "group", id: "tag:ordering", label: "Ordering", detail: "" })).toMatchObject({
      open: ["tag:ordering"],
      focus: null,
    });
    const route = { kind: "route" as const, id: "r", label: "GET /orders", contractType: "http", provider: "ordering.api", otherConsumers: 0 };
    expect(pickToView(ESTATE_VIEW, { ...route, consumer: "webapp" }).between).toEqual({ a: "webapp", b: "ordering.api" });
    expect(pickToView(ESTATE_VIEW, { ...route, consumer: null })).toMatchObject({ focus: "ordering.api", between: null });
  });
});

describe("search on the map", () => {
  it("steps through results with the arrows and focuses the picked service", async () => {
    const onViewChange = vi.fn();
    render(<SystemMap graph={grouped} view={ESTATE_VIEW} onViewChange={onViewChange} />);
    const box = screen.getByRole("combobox", { name: /search services, groups and routes/i });
    fireEvent.change(box, { target: { value: "ordering" } });
    const first = await screen.findByRole("option", { name: /ordering\.api/ });
    expect(first).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(screen.getByRole("option", { name: /ordering\.worker/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(box, { key: "ArrowUp" });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onViewChange).toHaveBeenLastCalledWith({ ...ESTATE_VIEW, focus: "ordering.api", between: null });
    expect(box).toHaveValue("");
  });

  it("lists routes from the loader and compares a picked route's caller and provider", async () => {
    const onViewChange = vi.fn();
    const loadRoutes = vi.fn(async () => [
      { contract_id: "GET /orders", contract_type: "http", provider_repo: "ordering.api", provider_file: "a", consumer_repo: "webapp", consumer_file: "b" },
    ]);
    render(<SystemMap graph={grouped} view={ESTATE_VIEW} onViewChange={onViewChange} loadRoutes={loadRoutes} />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "orders" } });
    const option = await screen.findByRole("option", { name: /GET \/orders/ });
    expect(within(option).getByText("webapp calls it")).toBeInTheDocument();
    expect(loadRoutes).toHaveBeenCalledWith("orders");
    fireEvent.click(option);
    expect(onViewChange).toHaveBeenLastCalledWith(expect.objectContaining({ between: { a: "webapp", b: "ordering.api" } }));
  });
});

describe("a route pick and the routes panel", () => {
  it("opens Between for the route's caller and provider with the routes panel filtered to the route", async () => {
    const load = vi.fn(async () => [
      { contract_id: "GET /orders", contract_type: "http", provider_repo: "ordering.api", provider_file: "a", consumer_repo: "webapp", consumer_file: "b" },
    ]);
    const loadLinksBetween = vi.fn(async (a: string, b: string) => ({ a, b, links: [], total: 0, totals: [] }));
    let view: MapViewState = ESTATE_VIEW;
    const onViewChange = (next: MapViewState) => {
      view = next;
    };
    const { rerender } = render(
      <SystemMap graph={flat} view={view} onViewChange={onViewChange} loadRoutes={load} loadLinksBetween={loadLinksBetween} />,
    );
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "orders" } });
    fireEvent.click(await screen.findByRole("option", { name: /GET \/orders/ }));
    expect(view.between).toEqual({ a: "webapp", b: "ordering.api" });
    rerender(<SystemMap graph={flat} view={view} onViewChange={onViewChange} loadRoutes={load} loadLinksBetween={loadLinksBetween} />);
    expect(await screen.findByRole("searchbox", { name: "Filter routes" })).toHaveValue("GET /orders");
  });
});

describe("shortcut keys", () => {
  it("maps each key, and leaves typing and handled keys alone", () => {
    expect(shortcutFor({ key: "/" })).toBe("search");
    expect(shortcutFor({ key: "k", ctrlKey: true })).toBe("search");
    expect(shortcutFor({ key: "k", metaKey: true })).toBe("search");
    expect(shortcutFor({ key: "Escape" })).toBe("back");
    expect(shortcutFor({ key: "f" })).toBe("focus");
    expect(shortcutFor({ key: "b" })).toBe("between");
    expect(shortcutFor({ key: "l" })).toBe("list");
    expect(shortcutFor({ key: "?" })).toBe("help");
    expect(shortcutFor({ key: "x" })).toBeNull();
    expect(shortcutFor({ key: "f", ctrlKey: true })).toBeNull();
    expect(shortcutFor({ key: "f", altKey: true })).toBeNull();
    expect(shortcutFor({ key: "Escape", defaultPrevented: true })).toBeNull();
    expect(shortcutFor({ key: "f", target: document.createElement("input") })).toBeNull();
    // Ctrl+K reaches the map from any field; nothing reaches it from inside a dialog.
    expect(shortcutFor({ key: "k", ctrlKey: true, target: document.createElement("input") })).toBe("search");
    const dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    const inside = dialog.appendChild(document.createElement("button"));
    expect(shortcutFor({ key: "Escape", target: inside })).toBeNull();
  });
});

describe("shortcuts on the map", () => {
  const press = (key: string, init: KeyboardEventInit = {}) =>
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init }));
    });

  it("/ and Ctrl+K move to the search box", () => {
    render(<SystemMap graph={grouped} />);
    const box = screen.getByRole("combobox");
    press("/");
    expect(document.activeElement).toBe(box);
    box.blur();
    press("k", { ctrlKey: true });
    expect(document.activeElement).toBe(box);
  });

  it("f focuses the selected service", () => {
    const onViewChange = vi.fn();
    render(<SystemMap graph={flat} view={ESTATE_VIEW} onViewChange={onViewChange} selection={{ type: "node", id: "webapp" }} onSelectionChange={() => {}} />);
    press("f");
    expect(onViewChange).toHaveBeenLastCalledWith({ ...ESTATE_VIEW, focus: "webapp", between: null });
  });

  it("f does nothing without a selected service", () => {
    const onViewChange = vi.fn();
    render(<SystemMap graph={flat} view={ESTATE_VIEW} onViewChange={onViewChange} />);
    press("f");
    expect(onViewChange).not.toHaveBeenCalled();
  });

  it("b opens the Between picker from the selected service and says so; Escape drops the pick, then the picker", () => {
    const onSelectionChange = vi.fn();
    render(<SystemMap graph={flat} selection={{ type: "node", id: "webapp" }} onSelectionChange={onSelectionChange} />);
    press("b");
    expect(screen.getByRole("status")).toHaveTextContent("Pick a second service to compare with webapp");
    expect(screen.getByPlaceholderText("From service...")).toHaveValue("webapp");
    press("Escape");
    expect(screen.getByRole("status")).not.toHaveTextContent("Pick a second service");
    expect(screen.getByPlaceholderText("From service...")).toBeInTheDocument();
    press("Escape");
    expect(screen.queryByPlaceholderText("From service...")).toBeNull();
    expect(onSelectionChange).not.toHaveBeenCalled();
    press("Escape");
    expect(onSelectionChange).toHaveBeenLastCalledWith(null);
  });

  it("l shows the list; Escape steps back out of a level on the map but not in the list", () => {
    const onViewChange = vi.fn();
    const focused = { ...ESTATE_VIEW, focus: "webapp" };
    const { rerender } = render(<SystemMap graph={flat} view={focused} onViewChange={onViewChange} />);
    press("Escape");
    expect(onViewChange).toHaveBeenLastCalledWith({ ...ESTATE_VIEW });
    press("l");
    expect(onViewChange).toHaveBeenLastCalledWith({ ...focused, display: "list" });
    onViewChange.mockClear();
    rerender(<SystemMap graph={flat} view={{ ...focused, display: "list" }} onViewChange={onViewChange} />);
    press("Escape");
    expect(onViewChange).not.toHaveBeenCalled();
    press("l");
    expect(onViewChange).toHaveBeenLastCalledWith({ ...focused, display: "map" });
  });

  it("? opens the shortcut help, and Escape closes it before anything else", () => {
    const onViewChange = vi.fn();
    render(<SystemMap graph={flat} view={{ ...ESTATE_VIEW, focus: "webapp" }} onViewChange={onViewChange} />);
    press("?");
    const dialog = screen.getByRole("dialog", { name: "System map keyboard shortcuts" });
    expect(within(dialog).getByText("Focus the selected service")).toBeInTheDocument();
    press("Escape");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onViewChange).not.toHaveBeenCalled();
  });
});

describe("between pick", () => {
  it("completes on the second service picked in the list, and opens the map", () => {
    const onViewChange = vi.fn();
    const onSelectionChange = vi.fn();
    const view = { ...ESTATE_VIEW, display: "list" as const };
    render(
      <SystemMap graph={flat} view={view} onViewChange={onViewChange} selection={{ type: "node", id: "webapp" }} onSelectionChange={onSelectionChange} />,
    );
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "b" }));
    });
    fireEvent.click(within(screen.getByRole("table")).getByText("ordering.api").closest("tr")!);
    expect(onViewChange).toHaveBeenLastCalledWith({ ...view, display: "map", between: { a: "webapp", b: "ordering.api" } });
    expect(onSelectionChange).toHaveBeenLastCalledWith(null);
  });
});

describe("services list", () => {
  it("counts runtime links only, joins health and group, and keeps the contract counts", () => {
    const rows = serviceRows(grouped, new Map([["webapp", { score: 72 }]]));
    const web = rows.find((r) => r.id === "webapp")!;
    expect(web).toMatchObject({ group: "Storefront", health: 7.2, runtimeOut: 2, runtimeIn: 0, provided: 1, consumed: 4 });
    // The package edge is Build, not Runtime.
    expect(rows.find((r) => r.id === "ordering.api")).toMatchObject({ runtimeIn: 4, runtimeOut: 0 });
    expect(rows.find((r) => r.id === "shared.lib")).toMatchObject({ group: null, health: null, runtimeIn: 0 });
  });

  it("sorts by each column, puts missing values last either way, and ties on the name", () => {
    const rows = serviceRows(grouped, new Map([["webapp", { score: 72 }], ["mobile", { score: 41 }]]));
    const ids = (key: Parameters<typeof nextSort>[1], order: "asc" | "desc") => sortServiceRows(rows, { key, order }).map((r) => r.id);
    expect(ids("name", "asc")).toEqual(["mobile", "ordering.api", "ordering.worker", "shared.lib", "webapp"]);
    expect(ids("in", "desc")[0]).toBe("ordering.api");
    expect(ids("health", "desc").slice(0, 2)).toEqual(["webapp", "mobile"]);
    expect(ids("health", "asc").slice(0, 2)).toEqual(["mobile", "webapp"]);
    expect(ids("group", "asc").at(-1)).toBe("shared.lib");
    expect(ids("contracts", "desc")[0]).toBe("ordering.api");
    expect(ids("out", "desc")[0]).toBe("webapp");
  });

  it("flips the same column and starts figures largest first", () => {
    expect(nextSort({ key: "name", order: "asc" }, "name")).toEqual({ key: "name", order: "desc" });
    expect(nextSort({ key: "name", order: "asc" }, "in")).toEqual({ key: "in", order: "desc" });
    expect(nextSort({ key: "in", order: "desc" }, "group")).toEqual({ key: "group", order: "asc" });
  });

  it("toggles from the map to a sortable table and back, and a row selects its service", () => {
    const onViewChange = vi.fn();
    const onSelectionChange = vi.fn();
    const { rerender } = render(<SystemMap graph={grouped} view={ESTATE_VIEW} onViewChange={onViewChange} onSelectionChange={onSelectionChange} />);
    fireEvent.click(screen.getByRole("radio", { name: "List" }));
    expect(onViewChange).toHaveBeenLastCalledWith({ ...ESTATE_VIEW, display: "list" });

    rerender(<SystemMap graph={grouped} view={{ ...ESTATE_VIEW, display: "list" }} onViewChange={onViewChange} onSelectionChange={onSelectionChange} />);
    const table = screen.getByRole("table");
    const firstCell = () => within(table).getAllByRole("row")[1]!;
    expect(firstCell()).toHaveTextContent("mobile");
    fireEvent.click(within(table).getByRole("button", { name: /Runtime in/ }));
    expect(firstCell()).toHaveTextContent("ordering.api");
    fireEvent.click(firstCell());
    expect(onSelectionChange).toHaveBeenLastCalledWith({ type: "node", id: "ordering.api" });

    fireEvent.click(screen.getByRole("radio", { name: "Map" }));
    expect(onViewChange).toHaveBeenLastCalledWith({ ...ESTATE_VIEW, display: "map" });
  });
});

describe("Map | List in the URL", () => {
  it("reads and writes view=list, and leaves it out by default", () => {
    const p = new URLSearchParams("view=list&focus=webapp");
    const state = parseMapView((k) => p.get(k));
    expect(state.display).toBe("list");
    expect(parseMapView((k) => (k === "view" ? "grid" : null))).toEqual(ESTATE_VIEW);
    const out = new URLSearchParams();
    writeMapView(out, state);
    expect(out.toString()).toBe("focus=webapp&view=list");
    writeMapView(out, ESTATE_VIEW);
    expect(out.toString()).toBe("");
    expect(sameMapView(state, { ...state, display: "map" })).toBe(false);
  });
});

describe("level announcements", () => {
  const announce = (view: MapViewState, extra: Partial<Parameters<typeof levelAnnouncement>[0]> = {}) =>
    levelAnnouncement({
      levelView: buildLevelView(grouped, toLevelViewState(view)),
      view,
      display: "map",
      services: grouped.nodes.length,
      pendingFrom: null,
      name: (id) => id,
      groupLabel: (id) => grouped.groups!.find((g) => g.id === id)?.label ?? id,
      copy: DEFAULT_NAV_COPY,
      ...extra,
    });

  it("names each level in one sentence", () => {
    expect(announce({ ...ESTATE_VIEW, open: ["tag:ordering"] })).toBe("Ordering opened: 2 services");
    expect(announce({ ...ESTATE_VIEW, focus: "ordering.api" })).toBe("Focused on ordering.api: 3 callers, 0 dependencies");
    expect(announce({ ...ESTATE_VIEW, between: { a: "webapp", b: "ordering.api" } })).toBe("Showing the links between webapp and ordering.api");
    expect(announce(ESTATE_VIEW, { display: "list" })).toBe("List view: 5 services");
    expect(announce(ESTATE_VIEW, { pendingFrom: "webapp" })).toBe("Pick a second service to compare with webapp");
  });

  it("is said by the map's live region when the level changes", async () => {
    const { rerender } = render(<SystemMap graph={grouped} view={ESTATE_VIEW} onViewChange={() => {}} />);
    const live = screen.getByRole("status");
    expect(live).toHaveAttribute("aria-live", "polite");
    rerender(<SystemMap graph={grouped} view={{ ...ESTATE_VIEW, open: ["tag:storefront"] }} onViewChange={() => {}} />);
    expect(live).toHaveTextContent("Storefront opened: 2 services");
  });
});
