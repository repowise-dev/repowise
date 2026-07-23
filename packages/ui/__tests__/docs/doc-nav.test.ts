import { describe, it, expect } from "vitest";
import { computeDocNav } from "../../src/docs/doc-nav.js";
import type { DocPage } from "@repowise-dev/types/docs";

function makePage(overrides: Partial<DocPage> = {}): DocPage {
  return {
    id: "p1",
    repository_id: "r1",
    page_type: "file_page",
    title: "Page",
    content: "",
    target_path: "src/foo.ts",
    source_hash: "h",
    model_name: "m",
    provider_name: "g",
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
    generation_level: 1,
    version: 1,
    confidence: 0.9,
    freshness_status: "fresh",
    metadata: {},
    human_notes: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

const ROOT = makePage({
  id: "repo_overview:demo",
  page_type: "repo_overview",
  title: "Repository Overview: demo",
  target_path: "demo",
  parent_page_id: null,
});
const LAYER = makePage({
  id: "layer_page:layer:runtime",
  page_type: "layer_page",
  title: "Layer: Runtime",
  target_path: "layer:runtime",
  parent_page_id: ROOT.id,
  display_order: 2,
});
const MODULE = makePage({
  id: "module_page:community-7",
  page_type: "module_page",
  // A community-clustered module: its target_path is a clustering ordinal, so
  // no file path can ever be a prefix of it. Path splitting cannot find this
  // ancestor at all; the stored parent can.
  title: "Module: runtime/engine",
  target_path: "community-7",
  parent_page_id: LAYER.id,
  display_order: 1,
});
const FILE_B = makePage({
  id: "file_page:runtime/engine/b.py",
  title: "b.py",
  target_path: "runtime/engine/b.py",
  parent_page_id: MODULE.id,
  display_order: 1,
});
const FILE_A = makePage({
  id: "file_page:runtime/engine/a.py",
  title: "a.py",
  target_path: "runtime/engine/a.py",
  parent_page_id: MODULE.id,
  display_order: 2,
});

const PAGES = [ROOT, LAYER, MODULE, FILE_B, FILE_A];

describe("computeDocNav", () => {
  it("walks stored parents rather than splitting the path", () => {
    const nav = computeDocNav(FILE_B, PAGES);
    expect(nav.breadcrumbs.map((b) => b.label)).toEqual([
      "Runtime",
      "runtime/engine",
      "b.py",
    ]);
    // Every ancestor crumb links to a real page, which is the point: a path
    // segment often has no page behind it.
    expect(nav.breadcrumbs.map((b) => b.pageId)).toEqual([
      LAYER.id,
      MODULE.id,
      FILE_B.id,
    ]);
  });

  it("omits the repo root, which the caller prepends itself", () => {
    const nav = computeDocNav(MODULE, PAGES);
    expect(nav.breadcrumbs.map((b) => b.pageId)).toEqual([LAYER.id, MODULE.id]);
  });

  it("orders siblings by stored order, not by path", () => {
    // b.py is stored first even though a.py sorts first alphabetically.
    const nav = computeDocNav(FILE_B, PAGES);
    expect(nav.prev).toBeUndefined();
    expect(nav.next?.pageId).toBe(FILE_A.id);
    expect(computeDocNav(FILE_A, PAGES).prev?.pageId).toBe(FILE_B.id);
  });

  it("stops at a parent cycle instead of looping forever", () => {
    const a = makePage({ id: "a", target_path: "a.py", title: "a.py", parent_page_id: "b" });
    const b = makePage({ id: "b", target_path: "b.py", title: "b.py", parent_page_id: "a" });
    const nav = computeDocNav(a, [a, b]);
    expect(nav.breadcrumbs.length).toBeLessThanOrEqual(3);
    expect(nav.breadcrumbs.at(-1)?.pageId).toBe("a");
  });

  it("falls back to the path split when the store has no tree", () => {
    // Pages written before the hierarchy existed carry no parent at all.
    const mod = makePage({
      id: "module_page:src",
      page_type: "module_page",
      title: "Module: src",
      target_path: "src",
    });
    const file = makePage({ id: "f", target_path: "src/foo.ts", title: "foo.ts" });
    const nav = computeDocNav(file, [mod, file]);
    expect(nav.breadcrumbs.map((b) => b.label)).toEqual(["src", "foo.ts"]);
    expect(nav.breadcrumbs[0]?.pageId).toBe(mod.id);
  });

  it("keeps the directory trail for a page hanging off the repo root", () => {
    // The tree places files no module claimed directly under the overview. A
    // lone basename crumb would say less than the path the page still carries.
    const loose = makePage({
      id: "file_page:docs/design/contrast_check.py",
      title: "contrast_check.py",
      target_path: "docs/design/contrast_check.py",
      parent_page_id: ROOT.id,
    });
    const nav = computeDocNav(loose, [ROOT, loose]);
    expect(nav.breadcrumbs.map((b) => b.label)).toEqual([
      "docs",
      "design",
      "contrast_check.py",
    ]);
  });

  it("labels ancestors with their page type so callers need not guess", () => {
    const nav = computeDocNav(FILE_B, PAGES);
    expect(nav.breadcrumbs.map((b) => b.pageType)).toEqual([
      "layer_page",
      "module_page",
      "file_page",
    ]);
  });

  it("keeps a cycle page's derived name rather than its raw graph id", () => {
    const cycle = makePage({
      id: "scc_page:scc-1050",
      page_type: "scc_page",
      title: "Circular Dependency: scc-1050",
      // No back-ticked member paths in the content, so sccDisplayLabel has
      // nothing to derive from and falls back to the title.
      content: "These modules import each other.",
      target_path: "scc-1050",
      parent_page_id: LAYER.id,
    });
    const nav = computeDocNav(cycle, [ROOT, LAYER, cycle]);
    expect(nav.breadcrumbs.at(-1)?.label).toBe("Circular Dependency: scc-1050");
  });

  it("shows a single label for a page with no path and no parent", () => {
    const overview = makePage({
      id: "o",
      page_type: "repo_overview",
      title: "Overview",
      target_path: "",
    });
    expect(computeDocNav(overview, [overview]).breadcrumbs).toEqual([{ label: "Overview" }]);
  });
});
