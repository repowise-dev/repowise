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

  it("numbers only the top-level rows that continue the run from 1", () => {
    // A module no layer claimed keeps the global number generation stamped on
    // it, so it can land on the top rung as "41" beside onboarding's "1".
    const unclaimed = makePage({
      id: "module_page:odd/bits",
      page_type: "module_page",
      title: "Module: odd/bits",
      target_path: "odd/bits",
      parent_page_id: ROOT.id,
      display_order: 41,
      section_number: "41",
    });
    render(
      <DocsTree
        pages={[...SPINE, unclaimed]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    const labels = rowLabels();
    // The unbroken run keeps its numbers, so a render-nothing implementation
    // cannot pass this either.
    expect(labels.some((l) => l.startsWith("1") && l.includes("Getting Started"))).toBe(true);
    expect(labels.some((l) => l.startsWith("2") && l.includes("Zebra Runtime"))).toBe(true);
    expect(labels.some((l) => l.startsWith("3") && l.includes("Alpha API"))).toBe(true);
    // The row that breaks the run shows no number at all.
    const stray = labels.find((l) => l.includes("odd/bits")) ?? "";
    expect(stray).not.toBe("");
    expect(stray).not.toMatch(/^\d/);
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

  // -------------------------------------------------------------------------
  // Layer grouping from the stamp on the members
  // -------------------------------------------------------------------------
  //
  // The wiki no longer writes a page per layer, so a module cannot be parented
  // onto one. Every module and cycle carries the layer that claims it, and the
  // tree builds the grouping row from that.

  /** A repo whose modules hang straight off the overview, each stamped. */
  const SPINE_IDS = ["layer:runtime", "layer:api"];
  const layeredRoot = (metadata: Record<string, unknown> = {}) =>
    makePage({
      id: "repo_overview:demo",
      page_type: "repo_overview",
      title: "Repository Overview: demo",
      target_path: "demo",
      parent_page_id: null,
      display_order: 0,
      metadata,
    });
  const stampedModule = (
    id: string,
    title: string,
    stamp: { layer_id?: string; layer_name?: string } = {},
    order = 1,
  ) =>
    makePage({
      id,
      page_type: "module_page",
      title,
      target_path: id.replace("module_page:", ""),
      parent_page_id: "repo_overview:demo",
      display_order: order,
      ...stamp,
    });

  it("groups modules under a layer row built from the stamp on the modules", () => {
    render(
      <DocsTree
        pages={[
          layeredRoot({ layer_order_ids: SPINE_IDS }),
          stampedModule("module_page:api/routes", "Module: api/routes", {
            layer_id: "layer:api",
            layer_name: "Alpha API",
          }),
          stampedModule("module_page:runtime/engine", "Module: runtime/engine", {
            layer_id: "layer:runtime",
            layer_name: "Zebra Runtime",
          }),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    // The layer row exists even though no page describes the layer.
    expect(screen.getByText("Zebra Runtime")).toBeInTheDocument();
    expect(screen.getByText("Alpha API")).toBeInTheDocument();
    // Ordered by the overview's spine, which disagrees with the alphabet — so
    // this cannot pass on an alphabetical grouping.
    expect(indexOfRow("Zebra Runtime")).toBeLessThan(indexOfRow("Alpha API"));
    expect("Zebra Runtime".localeCompare("Alpha API")).toBeGreaterThan(0);
    // Each module reads under its own layer, not beside it. The layers open on
    // load, so their members are there without a click.
    expect(indexOfRow("Zebra Runtime")).toBeLessThan(indexOfRow("runtime/engine"));
    expect(indexOfRow("runtime/engine")).toBeLessThan(indexOfRow("Alpha API"));
    expect(indexOfRow("Alpha API")).toBeLessThan(indexOfRow("api/routes"));
  });

  it("opens every layer row on load, and closes one on a click", () => {
    render(
      <DocsTree
        pages={[
          layeredRoot({ layer_order_ids: SPINE_IDS }),
          stampedModule("module_page:api/routes", "Module: api/routes", {
            layer_id: "layer:api",
            layer_name: "Alpha API",
          }),
          stampedModule("module_page:runtime/engine", "Module: runtime/engine", {
            layer_id: "layer:runtime",
            layer_name: "Zebra Runtime",
          }),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    // The first screen is a table of contents: every layer named, and what each
    // one holds listed under it, with no click spent to get there.
    expect(screen.getByText("Zebra Runtime")).toBeInTheDocument();
    expect(screen.getByText("Alpha API")).toBeInTheDocument();
    expect(screen.getByText("runtime/engine")).toBeInTheDocument();
    expect(screen.getByText("api/routes")).toBeInTheDocument();

    // One click shuts one layer and leaves the other open — so this cannot
    // pass on a tree that merely ignores the expansion state.
    fireEvent.click(screen.getByText("Zebra Runtime"));
    expect(screen.queryByText("runtime/engine")).not.toBeInTheDocument();
    expect(screen.getByText("api/routes")).toBeInTheDocument();
  });

  it("puts the file corpus below the layers, so it never fronts the outline", () => {
    render(
      <DocsTree
        pages={[
          layeredRoot({ layer_order_ids: SPINE_IDS }),
          makePage({
            id: "onboarding:onboarding/getting_started",
            page_type: "onboarding",
            title: "Getting Started",
            target_path: "onboarding/getting_started",
            parent_page_id: "repo_overview:demo",
            display_order: 1,
          }),
          stampedModule("module_page:runtime/engine", "Module: runtime/engine", {
            layer_id: "layer:runtime",
            layer_name: "Zebra Runtime",
          }),
          stampedModule("module_page:api/routes", "Module: api/routes", {
            layer_id: "layer:api",
            layer_name: "Alpha API",
          }),
          makePage({ id: "f1", target_path: "src/foo.ts", title: "foo.ts" }),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    // Orientation, then the layer outline, then the way into the files. The
    // corpus is a reference rather than a section, and a row whose count runs
    // to four digits sitting mid-outline reads as the point of the sidebar.
    const corpus = indexOfRow("Auto-documented files (1)");
    expect(indexOfRow("Repository Overview: demo")).toBeLessThan(indexOfRow("Getting Started"));
    expect(indexOfRow("Getting Started")).toBeLessThan(indexOfRow("Zebra Runtime"));
    expect(indexOfRow("Alpha API")).toBeLessThan(corpus);
    // The layers keep their own order ahead of it, so this cannot pass on a
    // build that merely shuffled the top-level rows.
    expect(indexOfRow("Zebra Runtime")).toBeLessThan(indexOfRow("Alpha API"));
    // Still a deliberate drill-in, not opened on load.
    expect(screen.queryByText("foo.ts")).not.toBeInTheDocument();
  });

  it("labels a layer by its id when no member carries the display name", () => {
    render(
      <DocsTree
        pages={[
          layeredRoot({ layer_order_ids: ["layer:api"] }),
          stampedModule("module_page:api/routes", "Module: api/routes", {
            layer_id: "layer:api",
          }),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    expect(screen.getByText("layer:api")).toBeInTheDocument();
  });

  it("leaves a layer grouping row unnumbered, and the stray beside it too", () => {
    const onboarding = makePage({
      id: "onboarding:onboarding/getting_started",
      page_type: "onboarding",
      title: "Getting Started",
      target_path: "onboarding/getting_started",
      metadata: { subkind: "getting_started" },
      parent_page_id: "repo_overview:demo",
      display_order: 1,
      section_number: "1",
    });
    const unclaimed = {
      ...stampedModule("module_page:odd/bits", "Module: odd/bits", {}, 41),
      section_number: "41",
    };
    render(
      <DocsTree
        pages={[
          layeredRoot({ layer_order_ids: ["layer:api"] }),
          onboarding,
          unclaimed,
          stampedModule(
            "module_page:api/routes",
            "Module: api/routes",
            { layer_id: "layer:api", layer_name: "Alpha API" },
            2,
          ),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    const labels = rowLabels();
    // The chapter that starts the run keeps its number.
    expect(labels.some((l) => l.startsWith("1") && l.includes("Getting Started"))).toBe(true);
    // A layer is a grouping row, not a page — it has no number to show.
    const layer = labels.find((l) => l.includes("Alpha API")) ?? "";
    expect(layer).not.toBe("");
    expect(layer).not.toMatch(/^\d/);
    // The row holding the modules no layer claimed is a grouping row too, so
    // it has no number of its own either. (The unclaimed module itself is no
    // longer on the top rung under a declared spine — it sits inside this row.
    // The case where it does reach the top rung, on a repo with no spine at
    // all, is covered by the test above.)
    const unlayeredRow = labels.find((l) => l.includes("Modules with no layer")) ?? "";
    expect(unlayeredRow).not.toBe("");
    expect(unlayeredRow).not.toMatch(/^\d/);
    // And it does not show its global number once opened, either.
    fireEvent.click(screen.getByText("Modules with no layer (1)"));
    const stray = rowLabels().find((l) => l.includes("odd/bits")) ?? "";
    expect(stray).not.toBe("");
    expect(stray).not.toMatch(/^\d/);
  });

  it("reads the stamp out of metadata when the row was fetched in full", () => {
    // A hydrated row carries the blob rather than the promoted column.
    const module = makePage({
      id: "module_page:api/routes",
      page_type: "module_page",
      title: "Module: api/routes",
      target_path: "api/routes",
      parent_page_id: "repo_overview:demo",
      metadata: { layer_id: "layer:api", layer_name: "Alpha API" },
    });
    render(
      <DocsTree
        pages={[layeredRoot({ layer_order_ids: ["layer:api"] }), module]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    expect(screen.getByText("Alpha API")).toBeInTheDocument();
  });

  it("warns when the overview names a spine but nothing carries a stamp", () => {
    // A listing that dropped the stamp and a repo with no layers at all look
    // identical in the rendered tree, so the two must not be told apart by
    // eye. The overview declaring a spine is the signal something is wrong.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    render(
      <DocsTree
        pages={[
          layeredRoot({ layer_order_ids: SPINE_IDS }),
          stampedModule("module_page:api/routes", "Module: api/routes"),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    expect(warn).toHaveBeenCalled();
    expect(warn.mock.calls.flat().join(" ")).toContain("layer");
    // The pages are still shown — a missing grouping never hides a page. An
    // unstamped module now waits in the trailing group rather than posing as a
    // layer on the top rung, so it takes one click to reach.
    fireEvent.click(screen.getByText("Modules with no layer (1)"));
    expect(screen.getByText("api/routes")).toBeInTheDocument();
    warn.mockRestore();
  });

  it("stays quiet on a repo that genuinely has no layers", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    render(
      <DocsTree
        pages={[
          layeredRoot(),
          stampedModule("module_page:api/routes", "Module: api/routes"),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    expect(warn).not.toHaveBeenCalled();
    expect(screen.getByText("api/routes")).toBeInTheDocument();
    warn.mockRestore();
  });

  it("warns about a stamped layer the spine does not list, and still shows it", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    render(
      <DocsTree
        pages={[
          layeredRoot({ layer_order_ids: ["layer:api"] }),
          stampedModule("module_page:api/routes", "Module: api/routes", {
            layer_id: "layer:api",
            layer_name: "Alpha API",
          }),
          stampedModule("module_page:odd/bits", "Module: odd/bits", {
            layer_id: "layer:ghost",
            layer_name: "Ghost",
          }),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    expect(warn.mock.calls.flat().join(" ")).toContain("layer:ghost");
    // Off-spine layers sort last rather than disappearing.
    expect(indexOfRow("Alpha API")).toBeLessThan(indexOfRow("Ghost"));
    // Without this the spy outlives the case and every later test that reads
    // console.warn sees this call too.
    warn.mockRestore();
  });

  it("keeps an unstamped module off the top rung, in a trailing group of its own", () => {
    render(
      <DocsTree
        pages={[
          layeredRoot({ layer_order_ids: SPINE_IDS }),
          // Listed first and named so the alphabet would put it at the very
          // top: an implementation that leaves it at depth 0 cannot pass.
          stampedModule("module_page:aaa/config", "Module: aaa/config", {}, 1),
          stampedModule("module_page:runtime/engine", "Module: runtime/engine", {
            layer_id: "layer:runtime",
            layer_name: "Zebra Runtime",
          }, 2),
          stampedModule("module_page:api/routes", "Module: api/routes", {
            layer_id: "layer:api",
            layer_name: "Alpha API",
          }, 3),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    // Not a sibling of the layer rows, where it would be styled as one.
    expect(screen.queryByText("aaa/config")).not.toBeInTheDocument();
    // The trailing row says what it is, and does not read like a layer.
    const group = screen.getByText("Modules with no layer (1)");
    // After every layer row, and before the bottom folder.
    expect(indexOfRow("Zebra Runtime")).toBeLessThan(indexOfRow("Modules with no layer"));
    expect(indexOfRow("Alpha API")).toBeLessThan(indexOfRow("Modules with no layer"));
    // Collapsed on load — only layer keys join the default-expanded set.
    fireEvent.click(group);
    expect(screen.getByText("aaa/config")).toBeInTheDocument();
  });

  it("moves only the unstamped module, leaving a stamped one under its layer", () => {
    render(
      <DocsTree
        pages={[
          layeredRoot({ layer_order_ids: SPINE_IDS }),
          stampedModule("module_page:aaa/config", "Module: aaa/config", {}, 1),
          stampedModule("module_page:runtime/engine", "Module: runtime/engine", {
            layer_id: "layer:runtime",
            layer_name: "Zebra Runtime",
          }, 2),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    // The layer opens on load; the leftovers bucket is one of the two rows that
    // does not, so open it to see where its member landed.
    fireEvent.click(screen.getByText("Modules with no layer (1)"));
    // The stamped module reads under its layer; the unstamped one reads under
    // the trailing group, which sits below every layer.
    expect(indexOfRow("Zebra Runtime")).toBeLessThan(indexOfRow("runtime/engine"));
    expect(indexOfRow("runtime/engine")).toBeLessThan(indexOfRow("Modules with no layer"));
    expect(indexOfRow("Modules with no layer")).toBeLessThan(indexOfRow("aaa/config"));
  });

  it("leaves the onboarding chapters on the top rung, ahead of the layers", () => {
    render(
      <DocsTree
        pages={[
          layeredRoot({ layer_order_ids: SPINE_IDS }),
          ONBOARDING,
          stampedModule("module_page:aaa/config", "Module: aaa/config", {}, 4),
          stampedModule("module_page:runtime/engine", "Module: runtime/engine", {
            layer_id: "layer:runtime",
            layer_name: "Zebra Runtime",
          }, 5),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    // Only the module was moved — the chapter is not swept into the group.
    expect(screen.getByText("Modules with no layer (1)")).toBeInTheDocument();
    expect(indexOfRow("Getting Started")).toBeLessThan(indexOfRow("Zebra Runtime"));
    expect(indexOfRow("Getting Started")).toBeLessThan(indexOfRow("Modules with no layer"));
  });

  it("warns about an unstamped module by name, and not about the chapters", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    render(
      <DocsTree
        pages={[
          layeredRoot({ layer_order_ids: SPINE_IDS }),
          ONBOARDING,
          stampedModule("module_page:aaa/config", "Module: aaa/config", {}, 4),
          stampedModule("module_page:runtime/engine", "Module: runtime/engine", {
            layer_id: "layer:runtime",
            layer_name: "Zebra Runtime",
          }, 5),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    const said = warn.mock.calls.flat().join(" ");
    expect(warn).toHaveBeenCalled();
    expect(said).toContain("aaa/config");
    // The chapter legitimately belongs on the top rung — complaining about it
    // would make the warning noise on every healthy repo.
    expect(said).not.toContain("getting_started");
    warn.mockRestore();
  });

  it("changes nothing on a repo that declares no layer spine at all", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    render(
      <DocsTree
        pages={[
          layeredRoot(),
          ONBOARDING,
          stampedModule("module_page:aaa/config", "Module: aaa/config", {}, 4),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    // With no spine there is nothing for a module to be missing from: it keeps
    // its place, no trailing group appears, and nobody complains.
    expect(screen.queryByText(/Modules with no layer/)).not.toBeInTheDocument();
    expect(screen.getByText("aaa/config")).toBeInTheDocument();
    expect(indexOfRow("Getting Started")).toBeLessThan(indexOfRow("aaa/config"));
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  // A chapter with chapters of its own — the rung below the one that opens.
  const SUB_MODULE = makePage({
    id: "module_page:runtime/engine/codecs",
    page_type: "module_page",
    title: "Module: runtime/engine/codecs",
    target_path: "runtime/engine/codecs",
    parent_page_id: MODULE.id,
    display_order: 1,
    section_number: "2.1.1",
  });

  it("opens the top rung only, leaving a chapter's own chapters shut", () => {
    render(
      <DocsTree
        pages={[...SPINE, SUB_MODULE]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    // The layer is open, so its chapter is on the first screen...
    expect(screen.getByText("runtime/engine")).toBeInTheDocument();
    // ...but the rung below it is not. Without this the outline came out four
    // deep on load and the indentation stopped carrying rank.
    expect(screen.queryByText("runtime/engine/codecs")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("runtime/engine"));
    expect(screen.getByText("runtime/engine/codecs")).toBeInTheDocument();
  });

  it("reveals the selected page by opening the chapters above it", () => {
    render(
      <DocsTree
        pages={[...SPINE, SUB_MODULE]}
        selectedPageId={SUB_MODULE.id}
        onSelectPage={() => {}}
      />,
    );
    // Reached by a deep link rather than a click, so nothing opened its parent.
    // A selected row inside a shut chapter is a highlight a reader cannot see.
    expect(screen.getByText("runtime/engine/codecs")).toBeInTheDocument();
  });

  it("carries no link on a layer row — the outline is rows of text", () => {
    render(
      <DocsTree
        pages={[
          layeredRoot({ layer_order_ids: ["layer:api"] }),
          stampedModule("module_page:api/routes", "Module: api/routes", {
            layer_id: "layer:api",
            layer_name: "Alpha API",
          }),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    // The row itself is untouched — this cannot pass on a tree that dropped the
    // layer along with its trailing button.
    expect(screen.getByText("Alpha API")).toBeInTheDocument();
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

  // Three orientation slots were retired, and their rows survive in every
  // index built before the sweep that removes them. `ONBOARDING_SLOT_TITLES`
  // still lists all three on purpose, and this is the reason: in *folder*
  // view a dedicated onboarding page whose slot is not in that map does not
  // disappear, it falls through to path-based grouping and surfaces as a stray
  // top-level `onboarding/` directory beside the Onboarding folder. That reads
  // as a bug rather than a retirement.
  //
  // The default domain view is unaffected either way — it places pages by the
  // stamped parent_page_id and labels them from page.title — so folder view is
  // what this has to assert on.
  it("keeps a retired onboarding slot in the Onboarding folder rather than a stray directory", () => {
    const retired = makePage({
      id: "onboarding:onboarding/codebase_map",
      page_type: "onboarding",
      title: "Codebase Map",
      target_path: "onboarding/codebase_map",
      metadata: { subkind: "codebase_map" },
      parent_page_id: ROOT.id,
      display_order: 3,
    });
    render(
      <DocsTree
        pages={[ROOT, ONBOARDING, retired]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    fireEvent.click(screen.getByLabelText("Switch to folder view"));

    // Both slots are in the one Onboarding folder, the retired one labelled
    // from the map, which is exactly what keeping its entry buys.
    const onboardingRow = rowLabels().find((l) => l.includes("Onboarding")) ?? "";
    expect(onboardingRow).not.toBe("");
    expect(screen.getByText("Codebase Map")).toBeInTheDocument();
    expect(screen.getByText("Getting Started")).toBeInTheDocument();
    // The failure mode if the entry were dropped: a bare `onboarding` row.
    expect(rowLabels().some((l) => l.trim() === "onboarding")).toBe(false);
  });
});
