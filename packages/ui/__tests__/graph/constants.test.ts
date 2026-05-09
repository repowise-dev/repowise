import { describe, it, expect } from "vitest";
import {
  getScaledNodeSize,
  EDGE_COLORS,
  COMMUNITY_COLORS,
  getCommunityColor,
  NODE_BASE_SIZES,
} from "../../src/graph/sigma/constants";

describe("getScaledNodeSize", () => {
  it("returns base size for small graphs", () => {
    expect(getScaledNodeSize(6, 100)).toBe(6);
    expect(getScaledNodeSize(10, 500)).toBe(10);
  });

  it("scales down for large graphs", () => {
    const base = NODE_BASE_SIZES.file;
    expect(getScaledNodeSize(base, 1000)).toBeLessThan(base);
    expect(getScaledNodeSize(base, 5000)).toBeLessThan(getScaledNodeSize(base, 1000));
    expect(getScaledNodeSize(base, 15000)).toBeLessThan(getScaledNodeSize(base, 5000));
  });

  it("never returns less than the minimum floor", () => {
    expect(getScaledNodeSize(1, 100000)).toBeGreaterThanOrEqual(1);
  });
});

describe("EDGE_COLORS", () => {
  it("has colors for all edge types", () => {
    const expectedTypes = ["import", "crossCommunity", "internal", "dynamic", "lowConfidence"];
    for (const t of expectedTypes) {
      expect(EDGE_COLORS).toHaveProperty(t);
      expect(EDGE_COLORS[t as keyof typeof EDGE_COLORS]).toMatch(/^#[0-9a-fA-F]{6}$/);
    }
  });
});

describe("COMMUNITY_COLORS", () => {
  it("has 24 colors", () => {
    expect(COMMUNITY_COLORS).toHaveLength(24);
  });

  it("getCommunityColor wraps around correctly", () => {
    expect(getCommunityColor(0)).toBe(COMMUNITY_COLORS[0]);
    expect(getCommunityColor(24)).toBe(COMMUNITY_COLORS[0]);
    expect(getCommunityColor(1)).toBe(COMMUNITY_COLORS[1]);
  });

  it("all entries are valid hex colors", () => {
    for (const c of COMMUNITY_COLORS) {
      expect(c).toMatch(/^#[0-9a-fA-F]{6}$/);
    }
  });
});
