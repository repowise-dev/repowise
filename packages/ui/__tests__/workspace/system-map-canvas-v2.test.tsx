import type React from "react";
import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import type { SystemEdge, SystemGraph, SystemNode } from "@repowise-dev/types";
import { SystemMap } from "../../src/workspace/system-map/system-map";
import { SystemMapGroupNode } from "../../src/workspace/system-map/system-map-group-node";
import { SystemMapLensBadge } from "../../src/workspace/system-map/system-map-lens-badge";
import { bandPairs, BandPanel } from "../../src/workspace/system-map/system-map-aggregate-panel";
import { breadcrumbTrail } from "../../src/workspace/system-map/system-map-breadcrumb";
import { buildLevelView } from "../../src/workspace/system-map/levels";
import { scopeCaption, truncationSentence, hiddenFamiliesSentence } from "../../src/workspace/system-map/scope-caption";
import { resolveSelection } from "../../src/workspace/system-map/selection-fix";
import { toFlowElements } from "../../src/workspace/system-map/flow-elements";
import { bandSentence, groupSourceLabel, healthCounts, healthSummaryText } from "../../src/workspace/system-map/aggregate-facts";
import { ESTATE_VIEW, openContainer } from "../../src/workspace/system-map/view-state";
import { ReactFlowProvider } from "@xyflow/react";

beforeAll(() => {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", RO);
});

function node(id: string, group?: string): SystemNode {
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
    ...(group ? { group } : {}),
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

/**
 * Two declared groups and a library only packages reach: storefront calls
 * ordering three times, ordering calls itself inside, and the library sits
 * in the tray at the estate.
 */
const grouped: SystemGraph = {
  version: 1,
  generated_at: "2026-10-24T00:00:00Z",
  nodes: [
    node("webapp", "tag:storefront"),
    node("mobile", "tag:storefront"),
    node("ordering.api", "tag:ordering"),
    node("ordering.worker", "tag:ordering"),
    node("shared.lib"),
  ],
  edges: [
    edge("webapp", "ordering.api", { weight: 2 }),
    edge("mobile", "ordering.api", { kind: "event", strategy: "gateway_route" }),
    edge("ordering.worker", "ordering.api", { kind: "event" }),
    edge("ordering.api", "shared.lib", { id: "ordering.api->shared.lib:pkg", kind: "package" }),
  ],
  groups: [
    { id: "tag:storefront", label: "Storefront", source: "tag", member_count: 2 },
    { id: "tag:ordering", label: "Ordering", source: "tag", member_count: 2 },
  ],
};

describe("estate -> open group -> back", () => {
  it("draws group cards and a band at the estate, opens a group in place, and Escape backs out", async () => {
    const { container } = render(<SystemMap graph={grouped} estateLabel="eShop" />);
    expect(await screen.findByText(/drawing 2 groups and a tray of 1 service/i)).toBeInTheDocument();
    expect(screen.getByText(/1 band carrying 3 links/i)).toBeInTheDocument();
    // No breadcrumb at the estate: it appears once the map is somewhere narrower.
    expect(screen.queryByRole("navigation", { name: "Graph location" })).toBeNull();

    // Storefront's card offers Open; the band prints its count.
    const opens = await screen.findAllByText("Open ›");
    expect(opens.length).toBeGreaterThanOrEqual(2);
    fireEvent.click(container.querySelector('[data-id="group:tag:ordering"] button')!);

    expect(await screen.findByText(/drawing 1 group, 2 services and a tray/i)).toBeInTheDocument();
    const crumbs = screen.getByRole("navigation", { name: "Graph location" });
    expect(crumbs).toHaveTextContent("eShop");
    expect(crumbs).toHaveTextContent("Ordering");

    await act(async () => {
      fireEvent.keyDown(container.querySelector(".react-flow")!, { key: "Escape" });
    });
    expect(await screen.findByText(/drawing 2 groups and a tray/i)).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Graph location" })).toBeNull();
  });

  it("opens a group with Enter on its focused card, and reports the level to a controlling host", async () => {
    const onViewChange = vi.fn();
    const { container } = render(<SystemMap graph={grouped} view={ESTATE_VIEW} onViewChange={onViewChange} />);
    await screen.findByText(/drawing 2 groups/i);
    const card = await vi.waitFor(() => {
      const el = container.querySelector('.react-flow__node[data-id="group:tag:storefront"]');
      if (!el) throw new Error("not drawn yet");
      return el;
    });
    fireEvent.keyDown(card, { key: "Enter" });
    expect(onViewChange).toHaveBeenCalledWith({ ...ESTATE_VIEW, open: ["tag:storefront"] });
  });

  it("shows a hidden family when the edge filter chip is pressed, and names it in the caption until then", async () => {
    render(<SystemMap graph={grouped} />);
    expect(await screen.findByText(/hidden by the edge filter: 1 build relationship \(package dependencies\)/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /build/i }));
    expect(screen.queryByText(/hidden by the edge filter/i)).toBeNull();
  });

  it("opens the group holding a service selected from outside the map", async () => {
    const onViewChange = vi.fn();
    render(
      <SystemMap
        graph={grouped}
        view={ESTATE_VIEW}
        onViewChange={onViewChange}
        selection={{ type: "node", id: "webapp" }}
        onSelectionChange={() => {}}
      />,
    );
    await screen.findByRole("region", { name: "webapp" });
    expect(onViewChange).toHaveBeenCalledWith({ ...ESTATE_VIEW, open: ["tag:storefront"] });
  });

  it("opens the group again when the same hidden service is selected a second time", async () => {
    const onViewChange = vi.fn();
    const props = { graph: grouped, view: ESTATE_VIEW, onViewChange, onSelectionChange: () => {} };
    const { rerender } = render(<SystemMap {...props} selection={{ type: "node", id: "webapp" }} />);
    await screen.findByRole("region", { name: "webapp" });
    rerender(<SystemMap {...props} selection={null} />);
    rerender(<SystemMap {...props} selection={{ type: "node", id: "webapp" }} />);
    await screen.findByRole("region", { name: "webapp" });
    expect(onViewChange).toHaveBeenCalledTimes(2);
  });
});

describe("caption", () => {
  it("states the draw budget's truncation in words", () => {
    const many: SystemGraph = {
      ...grouped,
      groups: [],
      nodes: Array.from({ length: 12 }, (_, i) => node(`s${i}`)),
      edges: Array.from({ length: 11 }, (_, i) => edge(`s${i}`, `s${i + 1}`)),
    };
    const view = buildLevelView(many, {}, { nodes: 5, edges: 3 });
    const sentence = truncationSentence(view)!;
    expect(sentence).toMatch(/^Over the draw budget of 5 cards and 3 lines: /);
    expect(sentence).toMatch(/services are folded into "\+N more" cards/);
    expect(sentence).toMatch(/weakest lines \(\d+ links\) are not drawn/);
    expect(scopeCaption(view, (id) => id)).toContain(sentence);
    expect(truncationSentence(buildLevelView(many))).toBeNull();
  });

  it("names every hidden family with its count, and nothing when all present are drawn", () => {
    const g: SystemGraph = { ...grouped, edges: [...grouped.edges, edge("webapp", "mobile", { id: "co", kind: "co_change", structural: false })] };
    expect(hiddenFamiliesSentence(buildLevelView(g))).toBe(
      "Hidden by the edge filter: 1 Build relationship (package dependencies) and 1 History relationship (files changed together).",
    );
    expect(hiddenFamiliesSentence(buildLevelView(g, { families: new Set(["runtime", "build", "history"]) }))).toBeNull();
  });
});

describe("selection against the drawn level", () => {
  const edgesById = new Map(grouped.edges.map((e) => [e.id, e]));

  it("maps a service onto its card, or opens the group that holds it", () => {
    const estate = buildLevelView(grouped);
    expect(resolveSelection({ type: "node", id: "webapp" }, estate, edgesById, ESTATE_VIEW).fix).toEqual({
      ...ESTATE_VIEW,
      open: ["tag:storefront"],
    });
    const opened = openContainer(ESTATE_VIEW, "tag:storefront");
    const view = buildLevelView(grouped, { open: ["tag:storefront"] });
    expect(resolveSelection({ type: "node", id: "webapp" }, view, edgesById, opened)).toEqual({
      drawnId: "webapp",
      drawnKind: "node",
      fix: null,
    });
  });

  it("asks for a hidden family to be shown for an edge of it", () => {
    const fix = resolveSelection({ type: "edge", id: "ordering.api->shared.lib:pkg" }, buildLevelView(grouped), edgesById, ESTATE_VIEW).fix;
    expect(fix?.families).toEqual(["runtime", "data", "build"]);
  });
});

describe("aggregate facts", () => {
  it("names where a group came from", () => {
    expect(groupSourceLabel("tag:storefront", "tag", 4)).toBe("tag: storefront");
    expect(groupSourceLabel("name:ordering", "name", 3)).toBe("name: ordering.*");
    expect(groupSourceLabel("linked:a", "linked", 6)).toBe("linked: 6 services");
    expect(groupSourceLabel("x", null, 2)).toBeNull();
  });

  it("marks health exceptions and keeps healthy quiet", () => {
    expect(healthSummaryText(healthCounts([9.5, 9.1]))).toBe("All healthy");
    expect(healthSummaryText(healthCounts([9.5, 2, 5, 6]))).toBe("1 Alert, 2 Warning");
    expect(healthSummaryText(healthCounts([null]))).toBe("No health scores");
  });

  it("puts a band in one sentence with its kind mix and gateway share", () => {
    const view = buildLevelView(grouped);
    const band = view.edges.find((e) => e.type === "band")!;
    expect(band.type === "band" && bandSentence("Storefront", "Ordering", band.summary)).toBe(
      "Storefront depends on Ordering: 3 links. HTTP 2, Event 1, 1 of them through a gateway.",
    );
  });
});

describe("cards and rail", () => {
  it("renders a group card with its source, counts, health exception and Open", () => {
    const view = buildLevelView(grouped);
    const health = new Map([["webapp", { score: 30 }], ["mobile", { score: 95 }]]);
    const flow = toFlowElements({
      view,
      positions: new Map(view.nodes.map((n) => [n.id, { x: 0, y: 0, width: 240, height: 104 }])),
      nodesById: new Map(grouped.nodes.map((n) => [n.id, n])),
      edgesById: new Map(grouped.edges.map((e) => [e.id, e])),
      healthByRepo: health,
    });
    const card = flow.nodes.find((n) => n.id === "group:tag:storefront")!;
    const Node = SystemMapGroupNode as unknown as (p: { data: unknown; selected: boolean }) => React.ReactElement;
    render(
      <ReactFlowProvider>
        <Node data={card.data} selected={false} />
      </ReactFlowProvider>,
    );
    expect(screen.getByText("Storefront")).toBeInTheDocument();
    expect(screen.getByText("tag: storefront")).toBeInTheDocument();
    expect(screen.getByText(/2 services · 2 repos/)).toBeInTheDocument();
    expect(screen.getByText("Alert")).toBeInTheDocument();
    expect(screen.getByText(/in 0 · out 3/)).toBeInTheDocument();
    expect(screen.getByText("Open ›")).toBeInTheDocument();
    // The band joining the two groups carries the count the card states.
    expect(flow.edges.find((e) => e.type === "systemBand")?.ariaLabel).toMatch(/3 links/);
  });

  it("lists a band's service pairs, strongest first, and selects one", () => {
    const view = buildLevelView(grouped);
    const band = view.edges.find((e) => e.type === "band")!;
    if (band.type !== "band") throw new Error("expected a band");
    const edgesById = new Map(grouped.edges.map((e) => [e.id, e]));
    expect(bandPairs(band, edgesById).map((p) => `${p.source}>${p.target}:${p.summary.links}`)).toEqual([
      "webapp>ordering.api:2",
      "mobile>ordering.api:1",
    ]);
    const onSelect = vi.fn();
    render(
      <BandPanel
        band={band}
        sourceLabel="Storefront"
        targetLabel="Ordering"
        nodesById={new Map(grouped.nodes.map((n) => [n.id, n]))}
        edgesById={edgesById}
        onSelect={onSelect}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /webapp → ordering.api/ }));
    expect(onSelect).toHaveBeenCalledWith({ type: "edge", id: "webapp->ordering.api" });
  });

  it("draws a lens badge in ink with a word, never in a health hue", () => {
    const { container } = render(<SystemMapLensBadge badge={{ label: "3 breaking", tone: "danger" }} />);
    const badge = container.firstElementChild as HTMLElement;
    expect(badge).toHaveTextContent("3 breaking");
    expect(badge.style.color).toBe("var(--color-text-primary)");
    expect(badge.outerHTML).not.toMatch(/color-error|color-warning/);
  });

  it("builds the breadcrumb chain Estate / Group / Service", () => {
    const focus = { ...ESTATE_VIEW, focus: "ordering.api" };
    expect(breadcrumbTrail(grouped, "focus", focus).map((c) => c.label)).toEqual(["Ordering", "ordering.api"]);
    expect(breadcrumbTrail(grouped, "focus", focus)[0]!.target).toEqual({ ...ESTATE_VIEW, open: ["tag:ordering"] });
    expect(breadcrumbTrail(grouped, "estate", ESTATE_VIEW)).toEqual([]);
  });
});
