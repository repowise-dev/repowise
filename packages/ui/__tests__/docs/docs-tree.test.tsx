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
// "Zebra Engine" reads before "Alpha API". A fixture whose order and alphabet
// agree cannot tell the two orderings apart.
const ROOT = makePage({
  id: "repo_overview:demo",
  page_type: "repo_overview",
  title: "Repository Overview: demo",
  nav_title: "Overview",
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
const ENGINE = makePage({
  id: "module_page:engine",
  page_type: "module_page",
  title: "Zebra Engine Overview",
  nav_title: "Zebra Engine",
  target_path: "engine",
  layer_id: "layer:service",
  layer_name: "Service",
  parent_page_id: ROOT.id,
  display_order: 2,
  section_number: "2",
});
const API = makePage({
  id: "module_page:api",
  page_type: "module_page",
  title: "Alpha API",
  target_path: "api",
  layer_id: "layer:api",
  layer_name: "API",
  parent_page_id: ROOT.id,
  display_order: 3,
  section_number: "3",
});
const PART = makePage({
  id: "module_page:engine/ingest",
  page_type: "module_page",
  title: "Ingestion",
  target_path: "engine/ingest",
  parent_page_id: ENGINE.id,
  display_order: 1,
  section_number: "2.1",
});
const SUBPART = makePage({
  id: "module_page:engine/ingest/langs",
  page_type: "module_page",
  title: "Languages",
  target_path: "engine/ingest/langs",
  parent_page_id: PART.id,
  display_order: 1,
  section_number: null,
});
const DEEPER = makePage({
  id: "module_page:engine/ingest/langs/go",
  page_type: "module_page",
  title: "Go Support",
  target_path: "engine/ingest/langs/go",
  parent_page_id: SUBPART.id,
  display_order: 1,
  section_number: null,
});
const FILE = makePage({
  id: "file_page:engine/ingest/a.py",
  page_type: "file_page",
  title: "a.py",
  target_path: "engine/ingest/a.py",
  parent_page_id: PART.id,
  display_order: 1,
});

const OUTLINE = [ROOT, ONBOARDING, ENGINE, API, PART, SUBPART, DEEPER, FILE];

/** Rendered row labels, in document order. */
function rowLabels(): string[] {
  return screen.getAllByRole("button").map((b) => b.textContent ?? "");
}

function indexOfRow(fragment: string): number {
  return rowLabels().findIndex((label) => label.includes(fragment));
}

function renderTree(pages: DocPage[], selectedPageId: string | null = null) {
  return render(
    <DocsTree pages={pages} selectedPageId={selectedPageId} onSelectPage={() => {}} />,
  );
}

describe("DocsTree", () => {
  it("reads overview, reading path, then sections on one rung, in stored order", () => {
    renderTree([API, FILE, ENGINE, ROOT, ONBOARDING, PART, SUBPART, DEEPER]);
    const order = ["Overview", "Getting Started", "Zebra Engine", "Alpha API"].map(indexOfRow);
    expect(order.every((i) => i >= 0)).toBe(true);
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect("Zebra Engine".localeCompare("Alpha API")).toBeGreaterThan(0);
    // No grouping rows above the sections: no "Start here", no layer rows.
    expect(screen.queryByText("Start here")).not.toBeInTheDocument();
    expect(rowLabels().some((l) => l.trim() === "Service")).toBe(false);
  });

  it("labels a row with its nav title and keeps the full title as the tooltip", () => {
    renderTree(OUTLINE);
    const row = screen.getByText("Zebra Engine").closest("button");
    expect(row).toHaveAttribute("title", "Zebra Engine Overview");
    expect(screen.queryByText("Zebra Engine Overview")).not.toBeInTheDocument();
  });

  it("numbers the sections and their parts, two levels and no deeper", () => {
    renderTree(OUTLINE);
    const labels = rowLabels();
    expect(labels.some((l) => l.startsWith("1") && l.includes("Getting Started"))).toBe(true);
    expect(labels.some((l) => l.startsWith("2") && l.includes("Zebra Engine"))).toBe(true);
    expect(labels.some((l) => l.startsWith("2.1") && l.includes("Ingestion"))).toBe(true);
  });

  it("hides a number from a store numbered before the cap", () => {
    const old = { ...PART, section_number: "2.1.4" };
    renderTree([ROOT, ONBOARDING, ENGINE, old]);
    const row = rowLabels().find((l) => l.includes("Ingestion")) ?? "";
    expect(row).not.toBe("");
    expect(row).not.toMatch(/^\d/);
  });

  it("names a section without its layer", () => {
    renderTree(OUTLINE);
    const engine = rowLabels().find((l) => l.includes("Zebra Engine")) ?? "";
    expect(engine).not.toContain("Service");
  });

  it("folds a part named as its section into the section's row", () => {
    const echo = makePage({
      id: "module_page:engine/src",
      page_type: "module_page",
      title: "Engine Source",
      nav_title: "Zebra Engine",
      target_path: "engine/src",
      parent_page_id: ENGINE.id,
      display_order: 0,
    });
    const inner = makePage({
      id: "module_page:engine/src/graph",
      page_type: "module_page",
      title: "Graph",
      target_path: "engine/src/graph",
      parent_page_id: echo.id,
      display_order: 1,
    });
    renderTree([...OUTLINE, echo, inner]);
    expect(rowLabels().filter((l) => l.includes("Zebra Engine"))).toHaveLength(1);
    // What the folded part held is listed under the section instead.
    expect(indexOfRow("Zebra Engine")).toBeLessThan(indexOfRow("Graph"));
    expect(indexOfRow("Graph")).toBeLessThan(indexOfRow("Alpha API"));
  });

  it("caps nesting two rungs below a section, listing deeper pages flat", () => {
    renderTree(OUTLINE);
    // Sections open on load; their parts are listed, the rung below is shut.
    expect(screen.getByText("Ingestion")).toBeInTheDocument();
    expect(screen.queryByText("Languages")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Ingestion"));
    // Both deeper pages are reachable from the second rung, neither nests.
    expect(screen.getByText("Languages")).toBeInTheDocument();
    expect(screen.getByText("Go Support")).toBeInTheDocument();
    const languages = screen.getByText("Languages").closest("button");
    const go = screen.getByText("Go Support").closest("button");
    expect(languages?.style.paddingLeft).toBe(go?.style.paddingLeft);
  });

  it("reveals the selected page by opening the rows above it", () => {
    renderTree(OUTLINE, DEEPER.id);
    expect(screen.getByText("Go Support")).toBeInTheDocument();
  });

  it("keeps files out of the outline, in the collapsed Reference folder", () => {
    renderTree(OUTLINE);
    expect(screen.queryByText("a.py")).not.toBeInTheDocument();
    expect(indexOfRow("Alpha API")).toBeLessThan(indexOfRow("Reference"));
    fireEvent.click(screen.getByText("Reference"));
    fireEvent.click(screen.getByText("Files"));
    fireEvent.click(screen.getByText("engine"));
    fireEvent.click(screen.getByText("ingest"));
    expect(screen.getByText("a.py")).toBeInTheDocument();
  });

  it("splits Reference by kind and names each row by its resource", () => {
    const contract = (path: string) =>
      makePage({
        id: `api_contract:${path}`,
        page_type: "api_contract",
        title: `API Contract: ${path}`,
        target_path: path,
        parent_page_id: ENGINE.id,
      });
    const infra = makePage({
      id: "infra_page:docker/Dockerfile",
      page_type: "infra_page",
      title: "Infrastructure: docker/Dockerfile",
      target_path: "docker/Dockerfile",
      parent_page_id: ROOT.id,
    });
    const cycle = makePage({
      id: "scc_page:scc-1",
      page_type: "scc_page",
      title: "Circular Dependency: Pipeline Persist",
      target_path: "scc-1",
      parent_page_id: ROOT.id,
    });
    renderTree([
      ...OUTLINE,
      contract("srv/routers/graph/path.py"),
      contract("srv/routers/pages.py"),
      infra,
      cycle,
    ]);
    fireEvent.click(screen.getByText("Reference"));
    const folders = ["API", "Files", "Infrastructure", "Dependency cycles"].map((name) =>
      rowLabels().findIndex((l) => l.startsWith(name)),
    );
    expect(folders.every((i) => i >= 0)).toBe(true);
    expect(folders).toEqual([...folders].sort((a, b) => a - b));
    fireEvent.click(screen.getAllByText("API").find((el) => el.closest("button")?.textContent?.startsWith("API2"))!);
    expect(screen.getByText("routers/graph/path")).toBeInTheDocument();
    expect(screen.getByText("routers/pages")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Infrastructure"));
    expect(screen.getByText("docker/Dockerfile")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Dependency cycles"));
    expect(screen.getByText("Pipeline Persist")).toBeInTheDocument();
  });

  it("puts walkthroughs after the sections, numbered as one more section", () => {
    const flow = makePage({
      id: "flow_page:cli/main.py::main",
      page_type: "flow_page",
      title: "What happens when you run init",
      target_path: "cli/main.py::main",
      // Parented to a section: it must still get one row, in its own group.
      parent_page_id: ENGINE.id,
      display_order: 9,
      section_number: "4.1",
    });
    renderTree([flow, ...OUTLINE]);
    const group = indexOfRow("Walkthroughs (1)");
    expect(rowLabels()[group]).toMatch(/^4/);
    expect(indexOfRow("Alpha API")).toBeLessThan(group);
    expect(group).toBeLessThan(indexOfRow("Reference"));
    expect(screen.queryByText("What happens when you run init")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Walkthroughs (1)"));
    expect(rowLabels().filter((l) => l.includes("What happens when you run init"))).toHaveLength(1);
  });

  it("lists small top-level packages as one numbered supporting section", () => {
    const small = (name: string, n: number) =>
      makePage({
        id: `module_page:${name}`,
        page_type: "module_page",
        title: name,
        target_path: name,
        parent_page_id: ROOT.id,
        display_order: 10 + n,
        section_number: `4.${n}`,
      });
    renderTree([...OUTLINE, small("bufferpool", 1), small("metrics", 2)]);
    const group = indexOfRow("Supporting packages (2)");
    expect(rowLabels()[group]).toMatch(/^4/);
    expect(indexOfRow("Alpha API")).toBeLessThan(group);
    expect(screen.queryByText("bufferpool")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Supporting packages (2)"));
    expect(indexOfRow("bufferpool")).toBeLessThan(indexOfRow("metrics"));
  });

  it("closes the outline with project activity, below Reference", () => {
    const landscape = makePage({
      id: "onboarding:onboarding/active_landscape",
      page_type: "onboarding",
      title: "Active Landscape",
      target_path: "onboarding/active_landscape",
      // Stamped the way a page written by the current generator is.
      metadata: { nav_section: "project_activity" },
      parent_page_id: ROOT.id,
      display_order: 9,
    });
    renderTree([...OUTLINE, landscape]);
    expect(indexOfRow("Reference")).toBeLessThan(indexOfRow("Project activity"));
    expect(screen.queryByText("Active Landscape")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Project activity"));
    expect(screen.getByText("Active Landscape")).toBeInTheDocument();
  });

  it("hides tombstoned pages, which the tree deliberately leaves unplaced", () => {
    const gone = makePage({
      id: "file_page:deleted.py",
      target_path: "deleted.py",
      title: "deleted.py",
      freshness_status: "tombstone",
    });
    renderTree([...OUTLINE, gone]);
    fireEvent.click(screen.getByText("Reference"));
    fireEvent.click(screen.getByText("Files"));
    expect(screen.queryByText("deleted.py")).not.toBeInTheDocument();
  });

  it("keeps unreached concept pages grouped by type", () => {
    // A store whose tree has not been rebuilt: every parent is null. Nothing
    // may disappear just because it has no recorded place.
    renderTree([
      makePage({ id: "a", target_path: "src/a.ts", title: "a.ts" }),
      makePage({ id: "m", page_type: "module_page", target_path: "src", title: "Module: src" }),
    ]);
    expect(screen.getByText("Module (1)")).toBeInTheDocument();
    expect(screen.getByText("Reference")).toBeInTheDocument();
    expect(screen.queryByText("File (1)")).not.toBeInTheDocument();
  });

  it("survives a concept parent cycle instead of dropping the pages in it", () => {
    const a = makePage({ id: "a", page_type: "module_page", target_path: "a", title: "Module: a", parent_page_id: "b" });
    const b = makePage({ id: "b", page_type: "module_page", target_path: "b", title: "Module: b", parent_page_id: "a" });
    renderTree([...OUTLINE, a, b]);
    expect(screen.getByText("Module (2)")).toBeInTheDocument();
  });

  it("invokes onSelectPage when a leaf page is clicked", () => {
    const onSelectPage = vi.fn();
    const target = makePage({ id: "x", target_path: "x.ts", title: "x.ts" });
    render(<DocsTree pages={[target]} selectedPageId={null} onSelectPage={onSelectPage} />);
    fireEvent.click(screen.getByText("Reference"));
    fireEvent.click(screen.getByText("Files"));
    fireEvent.click(screen.getByText("x.ts"));
    expect(onSelectPage).toHaveBeenCalledWith(target);
  });

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

  it("nests a file's symbols under its row and counts files and symbols apart", () => {
    const file = makePage({ id: "file_page:src/a.py", target_path: "src/a.py", title: "a.py" });
    const symbol = makePage({
      id: "symbol_spotlight:src/a.py::run",
      page_type: "symbol_spotlight",
      target_path: "src/a.py::run",
      title: "run",
      parent_page_id: file.id,
    });
    const other = makePage({ id: "file_page:src/b.py", target_path: "src/b.py", title: "b.py" });
    renderTree([symbol, file, other]);
    fireEvent.click(screen.getByText("Reference"));
    expect(screen.getByText("2 files · 1 symbol")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Files"));
    fireEvent.click(screen.getByText("src"));
    // The symbol is not a sibling of the files; it opens from its file.
    expect(screen.queryByText("run")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("a.py"));
    expect(indexOfRow("a.py")).toBeLessThan(indexOfRow("run"));
    expect(indexOfRow("run")).toBeLessThan(indexOfRow("b.py"));
  });

  it("summarises stale and unwritten pages, each a toggle for the status filter", () => {
    const stub = makePage({
      id: "module_page:core",
      page_type: "module_page",
      title: "Module: core",
      target_path: "core",
      provider_name: "template",
    });
    const stale = makePage({ id: "file_page:x.ts", target_path: "x.ts", freshness_status: "stale" });
    const fresh = makePage({ id: "file_page:y.ts", target_path: "y.ts", title: "y.ts" });
    renderTree([stub, stale, fresh]);
    expect(screen.getByText("3 pages")).toBeInTheDocument();
    expect(screen.queryByText(/outdated/)).not.toBeInTheDocument();
    const unwritten = screen.getByRole("button", { name: "1 not written" });
    fireEvent.click(unwritten);
    expect(unwritten).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("core")).toBeInTheDocument();
    expect(screen.queryByText("Reference")).not.toBeInTheDocument();
    fireEvent.click(unwritten);
    expect(screen.getByText("Reference")).toBeInTheDocument();
  });
});
