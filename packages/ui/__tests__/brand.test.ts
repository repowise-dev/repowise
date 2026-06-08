import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { BRAND, LIGHT, DARK, GRADIENTS } from "../src/brand.js";

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

describe("brand module purity", () => {
  it("has no imports (dependency-free by contract)", () => {
    const src = readFileSync(join(__dirname, "../src/brand.ts"), "utf8");
    expect(src).not.toMatch(/^\s*import\s/m);
    expect(src).not.toMatch(/\brequire\s*\(/);
  });
});
