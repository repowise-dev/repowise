/**
 * Guards the cost of the Sigma color pass.
 *
 * `getComputedStyle` is a synchronous style read: doing one per node inside a
 * loop is the difference between a constant and a linear cost on every graph
 * load, colorMode toggle and theme flip. This pass regressed exactly that way
 * once — the node loop called the raw `getCommunityFamily` (two reads a node,
 * ~3,000 for a 1,500-node graph) while the hook that pre-resolves the 12
 * families once already existed — and the palette resolver was passed as the
 * argument to `useRef`, re-running on every render for a result React discards.
 *
 * The assertions are deliberately loose: they only pin the *shape* of the cost
 * (constant, not per-node), so palette work can be added without churn here.
 */
import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import Graph from "graphology";
import { useSigmaRenderer } from "../../src/graph/sigma/use-sigma.js";
import type {
  SigmaNodeAttributes,
  SigmaEdgeAttributes,
} from "../../src/graph/sigma/types.js";

const NODE_COUNT = 1500;

function makeGraph() {
  const graph = new Graph<SigmaNodeAttributes, SigmaEdgeAttributes>();
  for (let i = 0; i < NODE_COUNT; i++) {
    graph.addNode(`n${i}`, {
      x: i,
      y: i,
      size: 5,
      color: "#000000",
      label: `n${i}`,
      // Mix in hubs: they take a different branch of the color pass, and both
      // branches used to resolve community tokens per node.
      nodeType: i < 12 ? "hub" : "file",
      fullPath: `src/f${i}.ts`,
      language: "typescript",
      communityId: i % 12,
      pagerank: 0.001,
      betweenness: 0,
      isTest: false,
      isEntryPoint: false,
      hasDoc: false,
      symbolCount: 1,
    });
  }
  return graph;
}

function options(graph: Graph<SigmaNodeAttributes, SigmaEdgeAttributes>) {
  return {
    container: null,
    graph,
    selectedNodeId: null,
    highlightedPath: new Set<string>(),
    highlightedEdges: new Set<string>(),
    searchDimmedNodes: null,
    communityDimmedNodes: null,
    colorMode: "community" as const,
    activeSignals: new Set<never>(),
    graphTheme: "dark" as "light" | "dark",
  };
}

describe("hub disc label ink", () => {
  it("is resolved from the base fill, so dimming never brightens a label", () => {
    const graph = makeGraph();
    renderHook(() => useSigmaRenderer(options(graph)));

    // The colour pass must stamp the ink onto the node. If it did not, the
    // drawer would fall back to deriving ink from `data.color` — which Sigma
    // hands over POST-reducer, i.e. already dimmed. In dark mode every dimmed
    // fill lands near-black, so the luminance pick would flip to the light ink
    // and a dimmed hub's label would render brighter than an undimmed one:
    // the exact inverse of what dimming is for.
    const hubs = graph.filterNodes((_id, attrs) => attrs.nodeType === "hub");
    expect(hubs.length).toBeGreaterThan(0);

    for (const id of hubs) {
      const attrs = graph.getNodeAttributes(id);
      expect(attrs.labelInk, `hub ${id} has no labelInk`).toBeTruthy();
      // Ink must contrast with the node's own undimmed fill.
      expect(attrs.labelInk).not.toBe(attrs.color);
    }
  });
});

describe("useSigmaRenderer style reads", () => {
  it("resolves tokens a constant number of times, not once per node", () => {
    const graph = makeGraph();
    const spy = vi.spyOn(window, "getComputedStyle");

    const { rerender } = renderHook(
      (props: { theme: "light" | "dark" }) =>
        useSigmaRenderer({ ...options(graph), graphTheme: props.theme }),
      { initialProps: { theme: "dark" as "light" | "dark" } },
    );

    // Well under NODE_COUNT: the 12 community families plus the palette, once.
    expect(spy.mock.calls.length).toBeLessThan(100);

    // A re-render with unchanged props must resolve nothing at all — the
    // palette is memoized, not recomputed and thrown away.
    spy.mockClear();
    rerender({ theme: "dark" });
    expect(spy.mock.calls.length).toBe(0);

    // A theme flip re-resolves, but still at constant cost.
    spy.mockClear();
    rerender({ theme: "light" });
    expect(spy.mock.calls.length).toBeGreaterThan(0);
    expect(spy.mock.calls.length).toBeLessThan(100);

    spy.mockRestore();
  });
});
