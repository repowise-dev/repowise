import { describe, it, expect } from "vitest";
import {
  computeStage1Layout,
  computeStage2Layout,
  repairElkInput,
  estimateContainerSize,
} from "../../src/c4/layout/two-stage-layout";

describe("repairElkInput", () => {
  it("removes edges with missing source or target", () => {
    const nodes = [{ id: "a" }, { id: "b" }];
    const edges = [
      { id: "e1", source: "a", target: "b" },
      { id: "e2", source: "a", target: "missing" },
      { id: "e3", source: "ghost", target: "b" },
    ];
    const { edges: cleaned, issues } = repairElkInput(nodes, edges);
    expect(cleaned).toHaveLength(1);
    expect(cleaned[0]!.id).toBe("e1");
    expect(issues).toHaveLength(2);
  });

  it("removes self-loop edges", () => {
    const nodes = [{ id: "a" }, { id: "b" }];
    const edges = [
      { id: "e1", source: "a", target: "a" },
      { id: "e2", source: "a", target: "b" },
    ];
    const { edges: cleaned, issues } = repairElkInput(nodes, edges);
    expect(cleaned).toHaveLength(1);
    expect(cleaned[0]!.id).toBe("e2");
    expect(issues).toHaveLength(1);
    expect(issues[0]).toContain("self-loop");
  });

  it("deduplicates edges by source+target", () => {
    const nodes = [{ id: "a" }, { id: "b" }];
    const edges = [
      { id: "e1", source: "a", target: "b" },
      { id: "e2", source: "a", target: "b" },
      { id: "e3", source: "a", target: "b" },
    ];
    const { edges: cleaned, issues } = repairElkInput(nodes, edges);
    expect(cleaned).toHaveLength(1);
    expect(issues).toHaveLength(2);
    expect(issues[0]).toContain("duplicate");
  });
});

describe("estimateContainerSize", () => {
  it("grows with child count", () => {
    const small = estimateContainerSize(1);
    const medium = estimateContainerSize(3);
    const large = estimateContainerSize(6);
    expect(medium.width).toBeGreaterThan(small.width);
    expect(large.width).toBeGreaterThan(medium.width);
  });

  it("caps at 800x600", () => {
    const huge = estimateContainerSize(100);
    expect(huge.width).toBeLessThanOrEqual(800);
    expect(huge.height).toBeLessThanOrEqual(600);
  });

  it("has minimum of 200x120", () => {
    const tiny = estimateContainerSize(0);
    expect(tiny.width).toBeGreaterThanOrEqual(200);
    expect(tiny.height).toBeGreaterThanOrEqual(120);
  });
});

describe("computeStage1Layout", () => {
  it("returns empty map for no nodes", async () => {
    const result = await computeStage1Layout([], [], [], [], new Map());
    expect(result.positions.size).toBe(0);
    expect(result.issues).toHaveLength(0);
  });

  it("positions all containers with non-negative coordinates", async () => {
    const containers = [
      { id: "c1", label: "C1", childNodeIds: ["n1", "n2"] },
      { id: "c2", label: "C2", childNodeIds: ["n3", "n4"] },
      { id: "c3", label: "C3", childNodeIds: ["n5"] },
    ];
    const edges = [
      { id: "e1", source: "c1", target: "c2" },
      { id: "e2", source: "c2", target: "c3" },
    ];
    const result = await computeStage1Layout(containers, [], [], edges, new Map());
    expect(result.positions.size).toBe(3);
    for (const [, pos] of result.positions) {
      expect(pos.x).toBeGreaterThanOrEqual(0);
      expect(pos.y).toBeGreaterThanOrEqual(0);
    }
  });

  it("produces non-overlapping positions for multiple nodes", async () => {
    const standaloneNodes = [
      { id: "n1", width: 200, height: 100 },
      { id: "n2", width: 200, height: 100 },
      { id: "n3", width: 200, height: 100 },
    ];
    const result = await computeStage1Layout([], standaloneNodes, [], [], new Map());
    expect(result.positions.size).toBe(3);
    const posArray = [...result.positions.values()];
    for (let i = 0; i < posArray.length; i++) {
      for (let j = i + 1; j < posArray.length; j++) {
        const a = posArray[i]!;
        const b = posArray[j]!;
        const overlapX = a.x < b.x + b.width && a.x + a.width > b.x;
        const overlapY = a.y < b.y + b.height && a.y + a.height > b.y;
        expect(overlapX && overlapY).toBe(false);
      }
    }
  });
});

describe("computeStage2Layout", () => {
  it("positions children with reasonable dimensions", async () => {
    const children = [
      { id: "n1", width: 200, height: 80 },
      { id: "n2", width: 200, height: 80 },
      { id: "n3", width: 200, height: 80 },
    ];
    const edges = [
      { id: "e1", source: "n1", target: "n2" },
      { id: "e2", source: "n2", target: "n3" },
    ];
    const result = await computeStage2Layout(children, edges);
    expect(result.positions.size).toBe(3);
    expect(result.actualSize.width).toBeGreaterThan(0);
    expect(result.actualSize.height).toBeGreaterThan(0);
    for (const [, pos] of result.positions) {
      expect(pos.x).toBeGreaterThanOrEqual(0);
      expect(pos.y).toBeGreaterThanOrEqual(0);
    }
  });

  it("returns empty for no children", async () => {
    const result = await computeStage2Layout([], []);
    expect(result.positions.size).toBe(0);
    expect(result.actualSize.width).toBe(0);
    expect(result.actualSize.height).toBe(0);
  });
});
