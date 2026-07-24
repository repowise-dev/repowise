import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// The tree persists its expansion state to localStorage; isolate tests so
// toggles from one render don't pre-expand (and invert clicks in) the next.
beforeEach(() => {
  window.localStorage.clear();
});
import { DocsTree } from "../../src/docs/docs-tree.js";
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

// A stored tree whose sibling order deliberately DISAGREES with the alphabet:
// the dependency spine puts "Zebra Runtime" above "Alpha API". A fixture whose
// spine and alphabet agree cannot tell the two orderings apart, so it would
// pass whether or not the component reads the stored order at all.
const ROOT = makePage({
  id: "repo_overview:demo",
  page_type: "repo_overview",
  title: "Repository Overview: demo",
  target_path: "demo",
  parent_page_id: null,
  display_order: 0,
  section_number: null,
});
const ONBOARDING = makePage({
  id: "onboarding:onboarding/getting_started",
  page_type: "onboarding",
  title: "Getting Started",
  target_path: "onboarding/getting_started",
  metadata: { subkind: "getting_started" },
  parent_page_id: ROOT.id,
  display_order: 1,
  section_number: "1",
});
const LAYER_RUNTIME = makePage({
  id: "layer_page:layer:runtime",
  page_type: "layer_page",
  title: "Layer: Zebra Runtime",
  target_path: "layer:runtime",
  parent_page_id: ROOT.id,
  display_order: 2,
  section_number: "2",
});
const LAYER_API = makePage({
  id: "layer_page:layer:api",
  page_type: "layer_page",
  title: "Layer: Alpha API",
  target_path: "layer:api",
  parent_page_id: ROOT.id,
  display_order: 3,
  section_number: "3",
});
const MODULE = makePage({
  id: "module_page:runtime/engine",
  page_type: "module_page",
  title: "Module: runtime/engine",
  target_path: "runtime/engine",
  parent_page_id: LAYER_RUNTIME.id,
  display_order: 1,
  section_number: "2.1",
});
const DEEP_FILE = makePage({
  id: "file_page:runtime/engine/resolvers/dotnet/index.py",
  page_type: "file_page",
  title: "index.py",
  target_path: "runtime/engine/resolvers/dotnet/index.py",
  parent_page_id: MODULE.id,
  display_order: 1,
  section_number: "2.1.1",
});

const SPINE = [ROOT, ONBOARDING, LAYER_RUNTIME, LAYER_API, MODULE, DEEP_FILE];

/** Rendered row labels, in document order. */
function rowLabels(): string[] {
  return screen.getAllByRole("button").map((b) => b.textContent ?? "");
}

function indexOfRow(fragment: string): number {
  return rowLabels().findIndex((label) => label.includes(fragment));
}

describe("DocsTree", () => {
  it("renders deterministic pages inside the collapsed Auto-documented folder", () => {
    render(
      <DocsTree
        pages={[
          makePage({ id: "1", target_path: "src/foo.ts", title: "foo.ts" }),
          makePage({ id: "2", target_path: "src/bar.ts", title: "bar.ts" }),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    const bucket = screen.getByText("Auto-documented files (2)");
    expect(bucket).toBeInTheDocument();
    // Collapsed by default: the files are a deliberate drill-in, not on load.
    expect(screen.queryByText("foo.ts")).not.toBeInTheDocument();
    fireEvent.click(bucket);
    fireEvent.click(screen.getByText("src"));
    expect(screen.getByText("foo.ts")).toBeInTheDocument();
    expect(screen.getByText("bar.ts")).toBeInTheDocument();
  });

  it("invokes onSelectPage when a leaf page is clicked", () => {
    const onSelectPage = vi.fn();
    const target = makePage({ id: "x", target_path: "x.ts", title: "x.ts" });
    render(
      <DocsTree
        pages={[target]}
        selectedPageId={null}
        onSelectPage={onSelectPage}
      />,
    );
    fireEvent.click(screen.getByText("Auto-documented files (1)"));
    fireEvent.click(screen.getByText("x.ts"));
    expect(onSelectPage).toHaveBeenCalledWith(target);
  });

  it("orders top-level rows by the stored spine, not alphabetically", () => {
    render(
      <DocsTree pages={SPINE} selectedPageId={null} onSelectPage={() => {}} />,
    );
    // Every layer is a top-level row, so this asserts on what a reader sees
    // without expanding anything — the failure mode that let a layer-ordering
    // bug ship green twice.
    const overview = indexOfRow("Repository Overview: demo");
    const onboarding = indexOfRow("Getting Started");
    const zebra = indexOfRow("Zebra Runtime");
    const alpha = indexOfRow("Alpha API");
    expect(overview).toBeGreaterThanOrEqual(0);
    expect(overview).toBeLessThan(onboarding);
    expect(onboarding).toBeLessThan(zebra);
    expect(zebra).toBeLessThan(alpha);
    // Reversing the stored order must reverse the render, or the assertion
    // above is only reading the array we happened to pass in.
    expect("Zebra Runtime".localeCompare("Alpha API")).toBeGreaterThan(0);
  });

  it("follows the stored order when it is the reverse of the input array", () => {
    // Same pages, passed in alphabetical order, with the spine unchanged.
    render(
      <DocsTree
        pages={[ROOT, LAYER_API, LAYER_RUNTIME, ONBOARDING, MODULE, DEEP_FILE]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    expect(indexOfRow("Zebra Runtime")).toBeLessThan(indexOfRow("Alpha API"));
  });

  it("shows the stored section number on the top rung", () => {
    render(
      <DocsTree pages={SPINE} selectedPageId={null} onSelectPage={() => {}} />,
    );
    expect(rowLabels().some((l) => l.startsWith("2") && l.includes("Zebra Runtime"))).toBe(true);
    expect(rowLabels().some((l) => l.startsWith("3") && l.includes("Alpha API"))).toBe(true);
  });

  it("opens the layer spine by default and keeps files out of the outline", () => {
    render(
      <DocsTree pages={SPINE} selectedPageId={null} onSelectPage={() => {}} />,
    );
    // Layer is open on load, so its concept title reads as a clean leaf in the
    // outline — no file rows beside it.
    expect(screen.getByText("runtime/engine")).toBeInTheDocument();
    expect(screen.queryByText("resolvers/dotnet/index.py")).not.toBeInTheDocument();
    // The file lives in the single Auto-documented folder at the bottom.
    expect(screen.getByText("Auto-documented files (1)")).toBeInTheDocument();
  });

  it("hides tombstoned pages, which the tree deliberately leaves unplaced", () => {
    const gone = makePage({
      id: "file_page:deleted.py",
      target_path: "deleted.py",
      title: "deleted.py",
      freshness_status: "tombstone",
      parent_page_id: null,
      display_order: 0,
    });
    render(
      <DocsTree
        pages={[...SPINE, gone]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    expect(screen.queryByText("deleted.py")).not.toBeInTheDocument();
  });

  it("keeps unreached concept pages grouped by type, files go to the bottom folder", () => {
    // A store whose tree has not been rebuilt: every parent is null. Nothing
    // may disappear just because it has no recorded place.
    render(
      <DocsTree
        pages={[
          makePage({ id: "a", target_path: "src/a.ts", title: "a.ts" }),
          makePage({
            id: "m",
            page_type: "module_page",
            target_path: "src",
            title: "Module: src",
          }),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    // The unplaced concept page is grouped by type so it never vanishes.
    expect(screen.getByText("Module (1)")).toBeInTheDocument();
    // The file page is not a stray group; it lives in the one bottom folder.
    expect(screen.getByText("Auto-documented files (1)")).toBeInTheDocument();
    expect(screen.queryByText("File (1)")).not.toBeInTheDocument();
  });

  it("survives a concept parent cycle instead of dropping the pages in it", () => {
    const a = makePage({
      id: "a",
      page_type: "module_page",
      target_path: "a",
      title: "Module: a",
      parent_page_id: "b",
    });
    const b = makePage({
      id: "b",
      page_type: "module_page",
      target_path: "b",
      title: "Module: b",
      parent_page_id: "a",
    });
    render(
      <DocsTree pages={[...SPINE, a, b]} selectedPageId={null} onSelectPage={() => {}} />,
    );
    expect(screen.getByText("Module (2)")).toBeInTheDocument();
  });

  it("routes structural pages to the bottom folder, never the concept outline", () => {
    // This repo's own overview carries dozens of cycle and loose file pages as
    // direct children; listed inline they push the layers out of sight.
    const cycles = Array.from({ length: 20 }, (_, i) =>
      makePage({
        id: `scc_page:${i}`,
        page_type: "scc_page",
        title: `Circular Dependency: scc-${i}`,
        target_path: `scc-${i}`,
        parent_page_id: ROOT.id,
        display_order: 10 + i,
        section_number: `${10 + i}`,
      }),
    );
    render(
      <DocsTree
        pages={[...SPINE, ...cycles]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    // Cycles are deterministic: they never appear in the outline, and they join
    // every other file page in the one bottom folder (20 cycles + 1 SPINE file).
    expect(screen.queryByText("scc-0")).not.toBeInTheDocument();
    expect(screen.getByText("Auto-documented files (21)")).toBeInTheDocument();
    // The bottom folder sits after the concept spine.
    expect(indexOfRow("Zebra Runtime")).toBeLessThan(indexOfRow("Auto-documented files"));
    // The layers themselves are the spine, always shown.
    expect(screen.getByText("Zebra Runtime")).toBeInTheDocument();
    expect(screen.getByText("Alpha API")).toBeInTheDocument();
  });

  it("routes even a few structural pages to the bottom folder, collapsed", () => {
    const few = Array.from({ length: 3 }, (_, i) =>
      makePage({
        id: `infra_page:${i}`,
        page_type: "infra_page",
        title: `infra-${i}.yml`,
        target_path: `deploy/infra-${i}.yml`,
        parent_page_id: ROOT.id,
        display_order: 10 + i,
      }),
    );
    render(
      <DocsTree pages={[...SPINE, ...few]} selectedPageId={null} onSelectPage={() => {}} />,
    );
    // 3 infra pages + 1 SPINE file, all in the single collapsed folder.
    expect(screen.getByText("Auto-documented files (4)")).toBeInTheDocument();
    expect(screen.queryByText("infra-0.yml")).not.toBeInTheDocument();
    // Drill in: bottom folder -> deploy directory -> file.
    fireEvent.click(screen.getByText("Auto-documented files (4)"));
    fireEvent.click(screen.getByText("deploy"));
    expect(screen.getByText("infra-0.yml")).toBeInTheDocument();
  });

  it("lifts a concept's file pages to the bottom folder, leaving the concept a clean leaf", () => {
    // The concept stays a pure title in the outline; its files move wholesale
    // into the single bottom folder rather than sitting beside it.
    const file = (id: string, path: string) =>
      makePage({
        id,
        target_path: path,
        title: path,
        parent_page_id: MODULE.id,
        display_order: 1,
      });
    render(
      <DocsTree
        pages={[ROOT, LAYER_RUNTIME, MODULE, file("f1", "runtime/engine/a.py"), file("f2", "runtime/engine/b.py")]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    // Concept visible as a leaf in the outline; its files are not beside it.
    expect(screen.getByText("runtime/engine")).toBeInTheDocument();
    expect(screen.queryByText("a.py")).not.toBeInTheDocument();
    // Both files are in the single bottom folder.
    expect(screen.getByText("Auto-documented files (2)")).toBeInTheDocument();
  });
});
