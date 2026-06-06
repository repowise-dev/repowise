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
  it("uses 0.15 / 6px at or below 2000 nodes", () => {
    expect(getLabelDensity(2000)).toBe(0.15);
    expect(getLabelRenderedSizeThreshold(2000)).toBe(6);
  });

  it("uses 0.07 / 8px above 2000 nodes", () => {
    expect(getLabelDensity(2001)).toBe(0.07);
    expect(getLabelRenderedSizeThreshold(2001)).toBe(8);
  });
});

describe("EDGE_COLORS", () => {
  const expectedTypes = ["import", "crossCommunity", "internal", "dynamic", "lowConfidence"];

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

  it("uses the warm semantic scheme: orange imports, plum cross-community", () => {
    // import = brand orange in both themes
    expect(EDGE_COLORS_BY_THEME.light.import).toBe("#f59520");
    expect(EDGE_COLORS_BY_THEME.dark.import).toBe("#f59520");
    // cross-community = plum (accent-secondary), different per theme
    expect(EDGE_COLORS_BY_THEME.light.crossCommunity).toBe("#58436c");
    expect(EDGE_COLORS_BY_THEME.dark.crossCommunity).toBe("#a98fc4");
  });
});
