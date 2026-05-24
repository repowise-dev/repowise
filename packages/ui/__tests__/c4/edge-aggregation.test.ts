import { describe, it, expect } from "vitest";
import { aggregateEdges } from "../../src/c4/layout/edge-aggregation";
import type { ArchEdge } from "../../src/c4/types";

function makeEdge(source: string, target: string, edge_type: string): ArchEdge {
  return { source, target, edge_type, direction: "forward", weight: 1, confidence: 1 };
}

describe("aggregateEdges", () => {
  it("aggregates multiple file edges into single container edge with summed count", () => {
    const edges: ArchEdge[] = [
      makeEdge("a1", "b1", "imports"),
      makeEdge("a2", "b2", "imports"),
      makeEdge("a3", "b3", "calls"),
    ];
    const nodeToBox = new Map([
      ["a1", "boxA"],
      ["a2", "boxA"],
      ["a3", "boxA"],
      ["b1", "boxB"],
      ["b2", "boxB"],
      ["b3", "boxB"],
    ]);
    const result = aggregateEdges(edges, nodeToBox);
    expect(result).toHaveLength(1);
    expect(result[0]!.source).toBe("boxA");
    expect(result[0]!.target).toBe("boxB");
    expect(result[0]!.count).toBe(3);
  });

  it("drops intra-container edges (self-loops)", () => {
    const edges: ArchEdge[] = [
      makeEdge("a1", "a2", "imports"),
      makeEdge("a1", "b1", "calls"),
    ];
    const nodeToBox = new Map([
      ["a1", "boxA"],
      ["a2", "boxA"],
      ["b1", "boxB"],
    ]);
    const result = aggregateEdges(edges, nodeToBox);
    expect(result).toHaveLength(1);
    expect(result[0]!.source).toBe("boxA");
    expect(result[0]!.target).toBe("boxB");
  });

  it("picks most common edge type as dominantType", () => {
    const edges: ArchEdge[] = [
      makeEdge("a1", "b1", "imports"),
      makeEdge("a2", "b2", "imports"),
      makeEdge("a3", "b3", "calls"),
    ];
    const nodeToBox = new Map([
      ["a1", "boxA"],
      ["a2", "boxA"],
      ["a3", "boxA"],
      ["b1", "boxB"],
      ["b2", "boxB"],
      ["b3", "boxB"],
    ]);
    const result = aggregateEdges(edges, nodeToBox);
    expect(result[0]!.dominantType).toBe("imports");
    expect(result[0]!.types).toContain("imports");
    expect(result[0]!.types).toContain("calls");
  });

  it("returns empty array for no edges", () => {
    const result = aggregateEdges([], new Map());
    expect(result).toHaveLength(0);
  });

  it("skips edges where nodes are not in nodeToBox", () => {
    const edges: ArchEdge[] = [
      makeEdge("unknown1", "unknown2", "imports"),
    ];
    const nodeToBox = new Map<string, string>();
    const result = aggregateEdges(edges, nodeToBox);
    expect(result).toHaveLength(0);
  });

  it("sorts results by count descending", () => {
    const edges: ArchEdge[] = [
      makeEdge("a1", "b1", "imports"),
      makeEdge("c1", "d1", "calls"),
      makeEdge("c2", "d2", "calls"),
      makeEdge("c3", "d3", "calls"),
    ];
    const nodeToBox = new Map([
      ["a1", "boxA"],
      ["b1", "boxB"],
      ["c1", "boxC"],
      ["c2", "boxC"],
      ["c3", "boxC"],
      ["d1", "boxD"],
      ["d2", "boxD"],
      ["d3", "boxD"],
    ]);
    const result = aggregateEdges(edges, nodeToBox);
    expect(result[0]!.count).toBeGreaterThan(result[1]!.count);
  });
});
