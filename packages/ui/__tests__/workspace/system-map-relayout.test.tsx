/**
 * Guards how often the System Map runs ELK. The layered layout is synchronous
 * main-thread work with a fixed ~23ms floor even on the real 7-node workspace
 * graph, so paying it for a filter toggle is both a stall and a layout jump.
 *
 * Lives in its own file because it wraps the layout module to count calls,
 * which the other system-map tests import unmocked.
 */

import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { SystemEdge, SystemGraph, SystemNode } from "@repowise-dev/types";

const computePositions = vi.hoisted(() => vi.fn());

vi.mock("../../src/workspace/system-map/layout", async (importOriginal) => {
  const actual =
    await importOriginal<
      typeof import("../../src/workspace/system-map/layout")
    >();
  return {
    ...actual,
    // Wraps, rather than replaces, the real layout: the assertions are about
    // how often it runs and on what, not about what ELK returns.
    computeSystemMapPositions: (graph: SystemGraph) => {
      computePositions(graph);
      return actual.computeSystemMapPositions(graph);
    },
  };
});

const { SystemMap } = await import("../../src/workspace/system-map/system-map");

beforeAll(() => {
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal("ResizeObserver", RO);
});

beforeEach(() => computePositions.mockClear());

function node(id: string, repo: string): SystemNode {
  return {
    id,
    repo,
    service_path: id.includes("::") ? (id.split("::")[1] ?? null) : null,
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

function edge(
  source: string,
  target: string,
  over: Partial<SystemEdge> = {},
): SystemEdge {
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

const graph: SystemGraph = {
  version: 1,
  generated_at: "2026-06-19T00:00:00Z",
  nodes: [
    node("api::svc/a", "api"),
    node("api::svc/b", "api"),
    node("web", "web"),
  ],
  edges: [
    edge("web", "api::svc/a"),
    edge("api::svc/b", "web", {
      id: "api::svc/b->web:co",
      kind: "co_change",
      structural: false,
    }),
  ],
  diagnostics: {} as never,
};

describe("System Map relayout", () => {
  it("does not re-run ELK when an edge-kind filter is toggled", async () => {
    render(<SystemMap graph={graph} />);
    await screen.findByTitle(/toggle http edges/i);
    expect(computePositions).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTitle(/toggle co-change edges/i));
    await screen.findByTitle(/toggle http edges/i);
    fireEvent.click(screen.getByTitle(/toggle http edges/i));
    await screen.findByTitle(/toggle http edges/i);

    // Two filter toggles, still one layout: node placement describes the
    // system, not the current lens.
    expect(computePositions).toHaveBeenCalledTimes(1);
  });

  it("re-runs ELK when collapse changes the node set, then reuses the result", async () => {
    render(<SystemMap graph={graph} />);
    await screen.findByText("Service view");
    expect(computePositions).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByText("Service view"));
    await screen.findByText("Repo view");
    // Collapsing genuinely changes the shape, so it earns a layout.
    expect(computePositions).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByText("Repo view"));
    await screen.findByText("Service view");
    fireEvent.click(screen.getByText("Service view"));
    await screen.findByText("Repo view");
    // Both shapes are already cached for this mount.
    expect(computePositions).toHaveBeenCalledTimes(2);
  });

  it("lays out from every edge, not only the visible ones", async () => {
    render(<SystemMap graph={graph} />);
    await screen.findByTitle(/toggle http edges/i);
    fireEvent.click(screen.getByTitle(/toggle co-change edges/i));
    await screen.findByTitle(/toggle http edges/i);

    const laidOut = computePositions.mock.calls[0]?.[0] as SystemGraph;
    expect(laidOut.edges.map((e) => e.id).sort()).toEqual(
      ["api::svc/b->web:co", "web->api::svc/a"].sort(),
    );
  });
});
