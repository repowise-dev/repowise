import { describe, it, expect } from "vitest";
import { hasRole, healthBandLabel, nodeRoles } from "../../src/zoom/node-signals";
import type { ZoomMetrics, ZoomNode } from "../../src/zoom/types";

const METRICS: ZoomMetrics = {
  file_count: 0,
  descendant_count: 0,
  hotspot_count: 0,
  entry_point_count: 0,
  on_flow_count: 0,
  dead_count: 0,
};

/**
 * `metrics` merges rather than replaces, so a fixture names only the counts its
 * assertion is about. Requiring all six meant every new field in `ZoomMetrics`
 * broke every fixture that set any of them, which is what happened to
 * `descendant_count`.
 *
 * The return is not cast. The `as ZoomNode` that used to sit here accepted an
 * object that did not describe a `ZoomNode` at all: `metrics` was missing
 * `descendant_count`, `summary` was null against a `string`, `importance` and
 * `sibling_rank` were absent, and `rect` is not a field (a node carries no box
 * at all; the pack computes one). Only the one override call site ever errored,
 * and only in the type-check job, which does not run on pull requests.
 */
function node(
  over: Omit<Partial<ZoomNode>, "metrics"> & { metrics?: Partial<ZoomMetrics> } = {},
): ZoomNode {
  const { metrics, ...rest } = over;
  return {
    id: "n",
    parent_id: null,
    level: 0,
    kind: "file",
    name: "n",
    path: "n",
    children: [],
    importance: 0,
    sibling_rank: 0,
    page_id: "",
    summary: "",
    language: null,
    health_score: null,
    is_entry_point: false,
    is_hotspot: false,
    is_dead: false,
    is_test: false,
    on_flow: false,
    ...rest,
    metrics: { ...METRICS, ...metrics },
  };
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
      metrics: { file_count: 9, hotspot_count: 2 },
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
