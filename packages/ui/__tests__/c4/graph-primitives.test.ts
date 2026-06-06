import { describe, it, expect } from "vitest";
import { TONE_STYLES, getTone, ARCH_NODE_SIZES } from "../../src/graph-primitives/tone-styles";
import { computeEdgeStrokeWidth } from "../../src/graph-primitives/edge-renderer";
import type { ArchNodeType } from "../../src/c4/types";

const ALL_ARCH_NODE_TYPES: ArchNodeType[] = [
  "file", "function", "class", "module", "concept",
  "config", "document", "service", "table", "endpoint",
  "pipeline", "schema", "resource",
];

describe("tone-styles", () => {
  it("every ArchNodeType has a corresponding key in TONE_STYLES", () => {
    for (const nodeType of ALL_ARCH_NODE_TYPES) {
      expect(TONE_STYLES).toHaveProperty(nodeType);
      const tone = TONE_STYLES[nodeType as keyof typeof TONE_STYLES];
      expect(tone).toHaveProperty("bg");
      expect(tone).toHaveProperty("border");
      expect(tone).toHaveProperty("band");
      expect(tone).toHaveProperty("text");
    }
  });

  it("getTone returns fallback (external tone) for unknown types", () => {
    const fallback = getTone("unknown_type");
    expect(fallback).toEqual(TONE_STYLES.external);
  });

  it("getTone returns correct tone for known types", () => {
    expect(getTone("file")).toEqual(TONE_STYLES.file);
    expect(getTone("system")).toEqual(TONE_STYLES.system);
  });
});

describe("ARCH_NODE_SIZES", () => {
  it("every ArchNodeType has an entry in ARCH_NODE_SIZES", () => {
    for (const nodeType of ALL_ARCH_NODE_TYPES) {
      expect(ARCH_NODE_SIZES).toHaveProperty(nodeType);
      const size = ARCH_NODE_SIZES[nodeType as keyof typeof ARCH_NODE_SIZES];
      expect(size).toHaveProperty("width");
      expect(size).toHaveProperty("height");
      expect(size.width).toBeGreaterThan(0);
      expect(size.height).toBeGreaterThan(0);
    }
  });
});

describe("edge stroke width formula", () => {
  // Blueprint edges are thin by design (kg-ux plan §2.3): 1px floor,
  // gentle log growth, 2.5px cap so heavy aggregates never dominate.
  it("returns expected values for various counts", () => {
    expect(computeEdgeStrokeWidth(0)).toBeCloseTo(1);
    expect(computeEdgeStrokeWidth(1)).toBeCloseTo(1.5);
    expect(computeEdgeStrokeWidth(5)).toBeCloseTo(1 + Math.log2(6) * 0.5);
    expect(computeEdgeStrokeWidth(15)).toBe(2.5);
    expect(computeEdgeStrokeWidth(100)).toBe(2.5); // capped at 2.5
    expect(computeEdgeStrokeWidth(1000)).toBe(2.5); // still capped
  });
});
