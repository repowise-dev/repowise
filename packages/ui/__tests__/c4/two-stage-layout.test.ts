import { describe, it, expect } from "vitest";
import {
  assignSlotsByRank,
  computeStage1Layout,
  hoistPrioritySlots,
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

describe("assignSlotsByRank (curated display_order, plan B-4)", () => {
  function pos(x: number, y: number) {
    return { x, y, width: 360, height: 220 };
  }

  it("re-ranks card slots even when edges stacked them the other way", () => {
    // ELK stacked persistence above ui (edge persistence → ui); the curated
    // ranks say ui(0) < service(1) < persistence(2) top→bottom.
    const positions = new Map([
      ["layer:persistence", pos(20, 40)],
      ["layer:ui", pos(20, 340)],
      ["layer:service", pos(20, 640)],
    ]);
    const ranks = new Map([
      ["layer:ui", 0],
      ["layer:service", 1],
      ["layer:persistence", 2],
    ]);
    const out = assignSlotsByRank(positions, ranks);
    expect(out.get("layer:ui")!.y).toBeLessThan(out.get("layer:service")!.y);
    expect(out.get("layer:service")!.y).toBeLessThan(out.get("layer:persistence")!.y);
    // The slot set itself is preserved — only the occupants change.
    const ys = [...out.values()].map((p) => p.y).sort((a, b) => a - b);
    expect(ys).toEqual([40, 340, 640]);
  });

  it("reads same-row slots left→right by rank", () => {
    const positions = new Map([
      ["b", pos(440, 40)],
      ["a", pos(20, 40)],
      ["c", pos(20, 340)],
    ]);
    const ranks = new Map([["a", 0], ["b", 1], ["c", 2]]);
    const out = assignSlotsByRank(positions, ranks);
    expect(out.get("a")).toEqual(pos(20, 40));
    expect(out.get("b")).toEqual(pos(440, 40));
    expect(out.get("c")).toEqual(pos(20, 340));
  });

  it("leaves unranked entries (portals) and tiny inputs untouched", () => {
    const positions = new Map([
      ["layer:a", pos(20, 40)],
      ["portal:x", { x: 500, y: 40, width: 180, height: 60 }],
    ]);
    const out = assignSlotsByRank(positions, new Map([["layer:a", 0]]));
    expect(out).toEqual(positions);
  });
});

describe("hoistPrioritySlots (entry-point anchoring, plan C-3)", () => {
  const children = [
    { id: "a.py", width: 300, height: 140 },
    { id: "b.py", width: 300, height: 140 },
    { id: "main.py", width: 300, height: 140 },
    { id: "conf.yaml", width: 240, height: 100 },
  ];

  it("bubbles entry points to the top-left slot of their size group", () => {
    const positions = new Map([
      ["a.py", { x: 20, y: 20 }],
      ["b.py", { x: 20, y: 180 }],
      ["main.py", { x: 20, y: 340 }],
      ["conf.yaml", { x: 360, y: 20 }],
    ]);
    const out = hoistPrioritySlots(positions, children, (id) => id === "main.py");

    expect(out.get("main.py")).toEqual({ x: 20, y: 20 });
    // Non-priority nodes keep their relative order in the remaining slots.
    expect(out.get("a.py")).toEqual({ x: 20, y: 180 });
    expect(out.get("b.py")).toEqual({ x: 20, y: 340 });
    // Different-size nodes are never disturbed (overlap safety).
    expect(out.get("conf.yaml")).toEqual({ x: 360, y: 20 });
  });

  it("is a no-op when no priority node is present", () => {
    const positions = new Map([
      ["a.py", { x: 20, y: 20 }],
      ["b.py", { x: 20, y: 180 }],
    ]);
    const out = hoistPrioritySlots(positions, children, () => false);
    expect(out).toEqual(positions);
  });
});
