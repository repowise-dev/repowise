import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

// Regression guard for the "canvas goes empty on hub click" bug (BUG A/C):
// focusNode MUST drive the camera using Sigma's *framed* display coordinates
// (getNodeDisplayData), NOT raw graphology x/y. Radial hubs sit hundreds of
// graph units from the origin, so animating the camera to raw x/y flies it off
// into blank canvas. We mock Sigma to capture the camera.animate target.

const cameraAnimate = vi.fn();
const getNodeDisplayData = vi.fn();
let lastSigmaInstance: Record<string, unknown> | null = null;

class FakeSigma {
  graph: unknown;
  constructor(graph: unknown) {
    this.graph = graph;
    lastSigmaInstance = this as unknown as Record<string, unknown>;
  }
  getCamera() {
    return { animate: cameraAnimate, animatedReset: vi.fn(), animatedZoom: vi.fn(), animatedUnzoom: vi.fn() };
  }
  getNodeDisplayData(id: string) {
    return getNodeDisplayData(id);
  }
  getGraph() {
    return this.graph;
  }
  setGraph() {}
  setSetting() {}
  refresh() {}
  on() {}
  off() {}
  kill() {}
}

vi.mock("sigma", () => ({ default: FakeSigma }));
vi.mock("@sigma/edge-curve", () => ({ default: class {} }));
vi.mock("sigma/rendering", () => ({
  EdgeLineProgram: class {},
  drawDiscNodeLabel: () => {},
}));

import Graph from "graphology";
import { useSigmaRenderer } from "../../src/graph/sigma/use-sigma";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "../../src/graph/sigma/types";

function makeGraph() {
  const g = new Graph<SigmaNodeAttributes, SigmaEdgeAttributes>();
  // A hub far from the origin, exactly like the radial constellation layout.
  g.addNode("__community__7", {
    x: 270,
    y: -180,
    size: 24,
    color: "#fff",
    label: "UI",
    nodeType: "hub",
    fullPath: "",
    language: "",
    communityId: 7,
    pagerank: 0,
    betweenness: 0,
    isTest: false,
    isEntryPoint: false,
    hasDoc: false,
    symbolCount: 0,
  } as SigmaNodeAttributes);
  return g;
}

describe("useSigmaRenderer.focusNode coordinate contract", () => {
  beforeEach(() => {
    cameraAnimate.mockClear();
    getNodeDisplayData.mockReset();
    lastSigmaInstance = null;
  });

  it("animates the camera to the node's DISPLAY coords, not raw graph coords", async () => {
    const container = document.createElement("div");
    const graph = makeGraph();

    const { result } = renderHook(() =>
      useSigmaRenderer({
        container,
        graph,
        selectedNodeId: null,
        hoveredNodeId: null,
        highlightedPath: new Set(),
        highlightedEdges: new Set(),
        searchDimmedNodes: null,
        communityDimmedNodes: null,
        colorMode: "community",
        activeSignals: new Set(),
        graphTheme: "dark",
      }),
    );

    // Wait for the async Sigma init effect to construct the (mocked) instance.
    await waitFor(() => expect(lastSigmaInstance).not.toBeNull());

    // Sigma reports a normalized/framed position distinct from the raw 270/-180.
    getNodeDisplayData.mockReturnValue({ x: 0.83, y: 0.12 });

    act(() => {
      result.current.focusNode("__community__7", 0.45);
    });

    expect(getNodeDisplayData).toHaveBeenCalledWith("__community__7");
    expect(cameraAnimate).toHaveBeenCalledTimes(1);
    const target = cameraAnimate.mock.calls[0]![0] as { x: number; y: number; ratio: number };
    // The camera target is the DISPLAY position + the requested ratio — NOT the
    // raw graph coords (270/-180) that caused the blank-canvas regression.
    expect(target).toEqual({ x: 0.83, y: 0.12, ratio: 0.45 });
    expect(target.x).not.toBe(270);
  });

  it("defaults to a tight ratio (0.15) for small file nodes", async () => {
    const container = document.createElement("div");
    const graph = makeGraph();
    const { result } = renderHook(() =>
      useSigmaRenderer({
        container,
        graph,
        selectedNodeId: null,
        hoveredNodeId: null,
        highlightedPath: new Set(),
        highlightedEdges: new Set(),
        searchDimmedNodes: null,
        communityDimmedNodes: null,
        colorMode: "community",
        activeSignals: new Set(),
        graphTheme: "dark",
      }),
    );
    await waitFor(() => expect(lastSigmaInstance).not.toBeNull());
    getNodeDisplayData.mockReturnValue({ x: 0.5, y: 0.5 });
    act(() => {
      result.current.focusNode("__community__7");
    });
    const target = cameraAnimate.mock.calls[0]![0] as { ratio: number };
    expect(target.ratio).toBe(0.15);
  });
});
