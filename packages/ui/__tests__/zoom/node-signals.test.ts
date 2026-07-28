import { describe, it, expect } from "vitest";
import { hasRole, healthBandLabel, nodeRoles } from "../../src/zoom/node-signals";
import type { ZoomNode } from "../../src/zoom/types";

function node(over: Partial<ZoomNode> = {}): ZoomNode {
  return {
    id: "n",
    parent_id: null,
    kind: "file",
    name: "n",
    path: "n",
    language: null,
    summary: null,
    children: [],
    rect: { x: 0, y: 0, w: 1, h: 1 },
    metrics: {
      file_count: 0,
      hotspot_count: 0,
      entry_point_count: 0,
      on_flow_count: 0,
      dead_count: 0,
    },
    health_score: null,
    is_entry_point: false,
    is_hotspot: false,
    is_dead: false,
    is_test: false,
    on_flow: false,
    ...over,
  } as ZoomNode;
}

describe("nodeRoles", () => {
  it("is empty when the node carries no role, so no dot is drawn", () => {
    expect(nodeRoles(node())).toEqual([]);
    expect(hasRole(node())).toBe(false);
  });

  it("names every applicable role, not just the priority winner", () => {
    // The old dot ran entry > hotspot > dead > on-flow and drew one colour, so
    // a box that was both reported only "entry".
    const both = node({ is_entry_point: true, is_hotspot: true });
    expect(nodeRoles(both)).toEqual(["Entry point", "Hotspot"]);
  });

  it("inherits a role from the subtree, matching what the card's dot tests", () => {
    const container = node({
      kind: "folder",
      metrics: {
        file_count: 9,
        hotspot_count: 2,
        entry_point_count: 0,
        on_flow_count: 0,
        dead_count: 0,
      },
    });
    expect(nodeRoles(container)).toEqual(["Hotspot"]);
    expect(hasRole(container)).toBe(true);
  });
});

describe("healthBandLabel", () => {
  it("stays quiet for an unscored node, since health is sparse", () => {
    expect(healthBandLabel(null)).toBeNull();
  });

  it("uses the canonical 3-band scale the card's dot paints on", () => {
    expect(healthBandLabel(8.0)).toBe("Healthy");
    expect(healthBandLabel(4.0)).toBe("Warning");
    expect(healthBandLabel(3.9)).toBe("Alert");
  });

  it("does not report a 6.9 as 'Good' while the dot beside it paints amber", () => {
    expect(healthBandLabel(6.9)).toBe("Warning");
  });
});
