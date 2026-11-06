import { describe, expect, it } from "vitest";
import { biomarkerLabel } from "../../src/health/biomarker-glossary";
import { humanizeBiomarker } from "../../src/refactoring/opportunity";

describe("renamed biomarker ids", () => {
  it("label a row stored under an old id with the current name", () => {
    expect(biomarkerLabel("bumpy_road")).toBe("Repeated nesting");
    expect(humanizeBiomarker("bumpy_road")).toBe("repeated nesting");
  });
});
