import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { BRAND, LIGHT, DARK, GRADIENTS } from "../src/brand.js";

// styles/globals.css is the single source of truth for the token system;
// the brand constants exist for surfaces that can't resolve CSS vars. This
// suite pins them together so a token retune can't silently strand the
// OG/email/badge values.
const css = readFileSync(join(__dirname, "../styles/globals.css"), "utf8");

describe("brand constants stay in sync with styles/globals.css", () => {
  it("brand identity values appear in the stylesheet", () => {
    for (const value of Object.values(BRAND)) {
      expect(css.toLowerCase()).toContain(value.toLowerCase());
    }
  });

  it("light surface/text values appear in the stylesheet", () => {
    for (const value of Object.values(LIGHT)) {
      expect(css.toLowerCase()).toContain(value.toLowerCase());
    }
  });

  it("dark surface/text values appear in the stylesheet", () => {
    for (const value of Object.values(DARK)) {
      expect(css.toLowerCase()).toContain(value.toLowerCase());
    }
  });

  it("gradients match the stylesheet definitions", () => {
    for (const value of Object.values(GRADIENTS)) {
      expect(css.toLowerCase()).toContain(value.toLowerCase());
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
