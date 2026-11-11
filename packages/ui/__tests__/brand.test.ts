import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { HEALTH_BAND_ORDER, bandForScore } from "@repowise-dev/types/health";
import {
  BRAND,
  LIGHT,
  DARK,
  DARK_CANVAS,
  DARK_CANVAS_BAND,
  GRADIENTS,
} from "../src/brand.js";
import { healthBandNodeFill } from "../src/health/tokens.js";

// styles/globals.css is the single source of truth for the token system;
// the brand constants exist for surfaces that can't resolve CSS vars. This
// suite pins them together so a token retune can't silently strand the
// OG/email/badge values.
const css = readFileSync(join(__dirname, "../styles/globals.css"), "utf8");

// Whitespace-insensitive view of the stylesheet so a gradient reformat
// (line breaks, spacing after commas) doesn't break the drift guard.
const cssNormalized = css.toLowerCase().replace(/\s+/g, " ");

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// A hex literal must appear bounded: #f59520 should not match inside
// #f59520ab (a longer, different color).
function expectHex(value: string): void {
  const re = new RegExp(escapeRegExp(value) + "(?![0-9a-f])", "i");
  expect(css).toMatch(re);
}

// Gradient strings are compared whitespace-insensitively against the
// stylesheet so only the colors/structure are pinned, not CSS formatting.
function expectGradient(value: string): void {
  const needle = value.toLowerCase().replace(/\s+/g, " ").trim();
  expect(cssNormalized).toContain(needle);
}

describe("brand constants stay in sync with styles/globals.css", () => {
  it("brand identity values appear in the stylesheet", () => {
    for (const value of Object.values(BRAND)) {
      expectHex(value);
    }
  });

  it("light surface/text values appear in the stylesheet", () => {
    for (const value of Object.values(LIGHT)) {
      expectHex(value);
    }
  });

  it("dark surface/text values appear in the stylesheet", () => {
    for (const value of Object.values(DARK)) {
      expectHex(value);
    }
  });

  it("gradients match the stylesheet definitions", () => {
    for (const value of Object.values(GRADIENTS)) {
      expectGradient(value);
    }
  });
});

// The canvas ramp shares names with the light block, so its values are read
// from the first `.dark {` block rather than matched anywhere in the file.
function darkToken(name: string): string | undefined {
  const start = css.indexOf("\n.dark {");
  const block = css.slice(start, css.indexOf("\n}", start));
  const re = new RegExp(`--color-${escapeRegExp(name)}:\\s*(#[0-9a-f]{6})\\s*;`, "i");
  return re.exec(block)?.[1]?.toLowerCase();
}

describe("dark canvas constants match the .dark block", () => {
  it("node ramp and caution equal their dark tokens", () => {
    expect(DARK_CANVAS).toEqual({
      nodeAtRisk: darkToken("node-at-risk"),
      nodeNeedsWork: darkToken("node-needs-work"),
      nodeFair: darkToken("node-fair"),
      nodeGood: darkToken("node-good"),
      nodeExcellent: darkToken("node-excellent"),
      nodeNeutral: darkToken("node-neutral"),
      caution: darkToken("caution"),
    });
  });

  it("pins the literal values", () => {
    expect(DARK_CANVAS).toEqual({
      nodeAtRisk: "#b0544b",
      nodeNeedsWork: "#bd7c42",
      nodeFair: "#a89453",
      nodeGood: "#42906f",
      nodeExcellent: "#5cb389",
      nodeNeutral: "#333336",
      caution: "#d9b04a",
    });
  });

  it("band map covers exactly the health bands", () => {
    expect(Object.keys(DARK_CANVAS_BAND).sort()).toEqual([...HEALTH_BAND_ORDER].sort());
  });

  it("band map uses the node token the in-app health surface paints each band with", () => {
    for (const band of HEALTH_BAND_ORDER) {
      // healthBandNodeFill returns `var(--color-node-<x>)`; map <x> to its
      // DARK_CANVAS key (needs-work -> nodeNeedsWork).
      const match = /^var\(--color-node-([a-z-]+)\)$/.exec(healthBandNodeFill(band));
      expect(match, band).not.toBeNull();
      const key = `node-${match![1]}`.replace(/-([a-z])/g, (_, c: string) =>
        c.toUpperCase(),
      ) as keyof typeof DARK_CANVAS;
      expect(DARK_CANVAS[key], band).toBeDefined();
      expect(DARK_CANVAS_BAND[band], band).toBe(DARK_CANVAS[key]);
    }
  });

  it("band map resolves scores to the matching node token", () => {
    expect(DARK_CANVAS_BAND[bandForScore(9)]).toBe(darkToken("node-excellent"));
    expect(DARK_CANVAS_BAND[bandForScore(7.5)]).toBe(darkToken("node-good"));
    expect(DARK_CANVAS_BAND[bandForScore(6)]).toBe(darkToken("node-fair"));
    expect(DARK_CANVAS_BAND[bandForScore(4.5)]).toBe(darkToken("node-needs-work"));
    expect(DARK_CANVAS_BAND[bandForScore(2)]).toBe(darkToken("node-at-risk"));
  });
});

describe("brand module purity", () => {
  it("has no imports (dependency-free by contract)", () => {
    const src = readFileSync(join(__dirname, "../src/brand.ts"), "utf8");
    expect(src).not.toMatch(/^\s*import\s/m);
    expect(src).not.toMatch(/\brequire\s*\(/);
  });
});
