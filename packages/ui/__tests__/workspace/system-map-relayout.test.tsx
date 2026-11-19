/**
 * Guards how often the System Map runs ELK. A family toggle changes only the
 * connectors, so it must never pay a layout or move a card; opening the tray
 * changes the drawn cards, so it earns one, and going back reuses the cache.
 *
 * Lives in its own file because it wraps the ELK helper to count calls,
 * which the other system-map tests import unmocked.
 */

import { describe, it, expect, vi, beforeAll, beforeEach } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import type { SystemEdge, SystemGraph, SystemNode } from "@repowise-dev/types";

const layoutCalls = vi.hoisted(() => vi.fn());

vi.mock("../../src/c4/layout/elk-c4-layout", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../src/c4/layout/elk-c4-layout")>();
  return {
    ...actual,
    // Wraps, rather than replaces, the real layout: the assertions are about
    // how often it runs and on what, not about what ELK returns.
    computeC4Layout: (...args: Parameters<typeof actual.computeC4Layout>) => {
      layoutCalls(...args);
      return actual.computeC4Layout(args[0], args[1], { ...args[2], worker: false });
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

beforeEach(() => layoutCalls.mockClear());

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

// web calls api; lib is reached only by a package edge, so it sits in the tray.
const graph: SystemGraph = {
  version: 1,
  generated_at: "2026-06-19T00:00:00Z",
  nodes: [node("api"), node("web"), node("lib")],
  edges: [edge("web", "api"), edge("api", "lib", { id: "api->lib:pkg", kind: "package" })],
};

describe("System Map relayout", () => {
  it("does not re-run ELK when an edge family is toggled", async () => {
    render(<SystemMap graph={graph} />);
    await screen.findByText(/drawing 2 services/i);
    const runs = layoutCalls.mock.calls.length;
    expect(runs).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /build/i }));
    await screen.findByRole("button", { name: /build/i, pressed: true });
    fireEvent.click(screen.getByRole("button", { name: /runtime/i }));
    await screen.findByRole("button", { name: /runtime/i, pressed: false });

    // Two toggles, no new layout: placement describes the system, not the filter.
    expect(layoutCalls.mock.calls.length).toBe(runs);
  });

  it("lays out again when the tray opens, and reuses the estate layout on the way back", async () => {
    const { container } = render(<SystemMap graph={graph} />);
    await screen.findByText(/drawing 2 services/i);
    const estateRuns = layoutCalls.mock.calls.length;

    fireEvent.click(await screen.findByText("Open ›"));
    await screen.findByText(/drawing 3 services/i);
    // The tray's interior, then the root with the tray at its new size.
    await waitFor(() => expect(layoutCalls.mock.calls.length).toBe(estateRuns + 2));
    const openRuns = layoutCalls.mock.calls.length;

    // Escape backs out a level: the tray closes, and the cached estate comes back.
    await act(async () => {
      fireEvent.keyDown(container.querySelector(".react-flow")!, { key: "Escape" });
    });
    await screen.findByText(/drawing 2 services/i);
    await new Promise((r) => setTimeout(r, 50));
    expect(layoutCalls.mock.calls.length).toBe(openRuns);
  });

  it("places cards from every family but History, whatever is drawn", async () => {
    render(<SystemMap graph={graph} />);
    await screen.findByText(/drawing 2 services/i);
    const edges = layoutCalls.mock.calls.flatMap((c) => (c[1] as { id: string }[]).map((e) => e.id));
    // The package edge into the tray shapes the root even though Build is hidden.
    expect(edges.some((id) => id.includes("tray:"))).toBe(true);
  });
});
