import { describe, it, expect } from "vitest";
import {
  getScaledNodeSize,
  EDGE_COLORS,
  EDGE_COLORS_BY_THEME,
  edgeColorsForTheme,
  NODE_BASE_SIZES,
  getLayoutDuration,
  getLabelDensity,
  getLabelRenderedSizeThreshold,
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

describe("getLayoutDuration", () => {
  it("caps at 8s for graphs up to 2000 nodes", () => {
    expect(getLayoutDuration(100)).toBe(8000);
    expect(getLayoutDuration(2000)).toBe(8000);
  });

  it("caps at 12s above 2000 nodes", () => {
    expect(getLayoutDuration(2001)).toBe(12000);
    expect(getLayoutDuration(50000)).toBe(12000);
  });
});

describe("label density", () => {
  it("uses 0.15 / 6px at or below 1200 nodes", () => {
    expect(getLabelDensity(1200)).toBe(0.15);
    expect(getLabelRenderedSizeThreshold(1200)).toBe(6);
  });

  it("thins out above 1200 nodes", () => {
    expect(getLabelDensity(1201)).toBe(0.07);
    expect(getLabelRenderedSizeThreshold(1201)).toBe(8);
  });

  it("thins out again near the load-more ceiling", () => {
    expect(getLabelDensity(2501)).toBe(0.05);
    expect(getLabelRenderedSizeThreshold(2501)).toBe(10);
  });

  // Regression guard: both thresholds used to step at 2,000 while the export
  // was capped at 1,500, so the "large graph" branch could never be reached on
  // any repo. Every rung must sit inside what the loader can actually deliver.
  it("steps below the node cap the loader can reach", () => {
    expect(getLabelDensity(1500)).toBeLessThan(0.15);
    expect(getLabelRenderedSizeThreshold(1500)).toBeGreaterThan(6);
  });
});

describe("EDGE_COLORS", () => {
  const expectedTypes = ["crossCommunity", "internal", "dynamic", "lowConfidence"];

  it("has valid hex colors for all edge types in both themes", () => {
    for (const theme of ["light", "dark"] as const) {
      for (const t of expectedTypes) {
        const palette = EDGE_COLORS_BY_THEME[theme];
        expect(palette).toHaveProperty(t);
        expect(palette[t as keyof typeof palette]).toMatch(/^#[0-9a-fA-F]{6}$/);
      }
    }
  });

  it("edgeColorsForTheme returns the per-theme palette and falls back to dark", () => {
    expect(edgeColorsForTheme("light")).toBe(EDGE_COLORS_BY_THEME.light);
    expect(edgeColorsForTheme("dark")).toBe(EDGE_COLORS_BY_THEME.dark);
    // Default export mirrors the product default (dark) palette.
    expect(EDGE_COLORS).toBe(EDGE_COLORS_BY_THEME.dark);
  });

  it("uses the warm semantic scheme, and keys no kind that is never drawn", () => {
    // No "import" kind. classifyEdge returned it only for an edge with an
    // endpoint missing from the graph, and the adapter drops those before
    // drawing, so the swatch keyed zero marks in every state.
    expect("import" in EDGE_COLORS_BY_THEME.light).toBe(false);
    expect("import" in EDGE_COLORS_BY_THEME.dark).toBe(false);
    // cross-community = plum (accent-secondary), different per theme
    expect(EDGE_COLORS_BY_THEME.light.crossCommunity).toBe("#58436c");
    expect(EDGE_COLORS_BY_THEME.dark.crossCommunity).toBe("#a98fc4");
  });
});
