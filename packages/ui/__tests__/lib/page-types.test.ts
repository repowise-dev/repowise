import { describe, it, expect } from "vitest";
import {
  getPageTypeLabel,
  isModelWrittenType,
  isStubPage,
  MODEL_WRITTEN_PAGE_TYPES,
} from "../../src/lib/page-types.js";

// The regenerate / write affordance is gated on `isModelWrittenType`: it renders
// on a concept-tree or onboarding page and on nothing else. These assert that
// gate by page type, so a structural page can never grow the button.

const STRUCTURAL_TYPES = [
  "file_page",
  "symbol_spotlight",
  "api_contract",
  "infra_page",
  "scc_page",
  "layer_page",
];

describe("isModelWrittenType", () => {
  it("is true for exactly the four model-written types", () => {
    for (const t of ["module_page", "repo_overview", "architecture_diagram", "onboarding"]) {
      expect(isModelWrittenType(t)).toBe(true);
    }
    expect([...MODEL_WRITTEN_PAGE_TYPES].sort()).toEqual(
      ["architecture_diagram", "module_page", "onboarding", "repo_overview"],
    );
  });

  it("is false for every structural type", () => {
    for (const t of STRUCTURAL_TYPES) expect(isModelWrittenType(t)).toBe(false);
  });

  it("is false for null / undefined / unknown", () => {
    expect(isModelWrittenType(null)).toBe(false);
    expect(isModelWrittenType(undefined)).toBe(false);
    expect(isModelWrittenType("nonsense")).toBe(false);
  });
});

describe("isStubPage", () => {
  it("is true for a model-written type still stamped template", () => {
    expect(isStubPage({ page_type: "module_page", provider_name: "template" })).toBe(true);
  });

  it("is false for a written model page (real provider)", () => {
    expect(isStubPage({ page_type: "module_page", provider_name: "openai" })).toBe(false);
  });

  it("is false for a structural page even when it is a template", () => {
    // A file page is template forever; it is never a stub awaiting prose.
    expect(isStubPage({ page_type: "file_page", provider_name: "template" })).toBe(false);
  });

  it("is false for null / undefined", () => {
    expect(isStubPage(null)).toBe(false);
    expect(isStubPage(undefined)).toBe(false);
  });
});

describe("getPageTypeLabel", () => {
  it("calls a cycle page a cycle, the same word the tree uses", () => {
    // The tree labels these "Cycle: generation/page_generator" via
    // sccDisplayLabel. The filter chip said "SCC", so the one control for
    // narrowing to them shared no word with the rows it selected.
    expect(getPageTypeLabel("scc_page")).toBe("Cycle");
  });

  it("humanises an unknown type rather than rendering nothing", () => {
    expect(getPageTypeLabel("not_a_page_type")).toBe("not a page type");
  });
});
