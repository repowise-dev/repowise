import { describe, it, expect } from "vitest";
import { buildPresentModel, canPresent } from "../../src/present/build-present-model.js";
import {
  splitOnH2,
  splitBlocks,
  stripLeadingH1,
  extractMermaidBlocks,
  isProse,
  isDrawable,
  diagramKind,
  wholeSentences,
} from "../../src/present/split-markdown.js";
import type { DocPage } from "@repowise-dev/types/docs";
import type { PresentSource } from "../../src/present/types.js";

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
    provider_name: "p",
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
    generation_level: 0,
    version: 1,
    confidence: 1,
    freshness_status: "fresh",
    metadata: {},
    human_notes: null,
    created_at: "",
    updated_at: "",
    ...overrides,
  };
}

function source(overrides: Partial<PresentSource> = {}): PresentSource {
  return {
    overview: makePage({
      id: "ov",
      page_type: "repo_overview",
      title: "Repository Overview: acme",
      target_path: "acme",
      content: "## Summary\n\nAcme turns orders into shipments through a queue of workers.",
    }),
    parts: [],
    totalParts: 0,
    pageIdByPath: new Map(),
    ...overrides,
  };
}

const FLOW = 'flowchart TB\n  api["API"] -->|queues| worker["Worker"]';
const SEQUENCE = "sequenceDiagram\n  participant A\n  participant B\n  A->>B: request";

// The current module-page contract: plain opening, a diagram, step-named
// sections that close on a Sources line, and a reading list.
const CONTRACT_PART = [
  "# Order Intake",
  "",
  "This part accepts orders over HTTP and hands them to the queue. Everything downstream reads what it writes.",
  "",
  "```mermaid",
  SEQUENCE,
  "```",
  "",
  "## From request to queued job",
  "",
  "The handler validates the payload before anything is stored.",
  "",
  "Sources: `handler.py`, `schema.py`",
  "",
  "## How retries are bounded",
  "",
  "Each job carries an attempt counter.",
  "",
  "Sources: `retry.py`",
  "",
  "## Where to start reading",
  "",
  "- `handler.py` - The request entry point.",
  "- `retry.py` - The retry policy.",
  "",
  "## How it connects",
  "",
  "Workers consume the queue this part fills.",
].join("\n");

describe("split-markdown", () => {
  it("splits on H2 and keeps the lead", () => {
    const { lead, sections } = splitOnH2("Intro line.\n\n## First\nbody a\n\n## Second\nbody b");
    expect(lead).toBe("Intro line.");
    expect(sections.map((s) => s.heading)).toEqual(["First", "Second"]);
  });

  it("ignores headings and blank lines inside code fences", () => {
    expect(splitOnH2("lead\n\n```\n## not a heading\n```\n\n## Real\nx").sections).toHaveLength(1);
    expect(splitBlocks("a\n\n```\nx\n\ny\n```\n\nb")).toEqual(["a", "```\nx\n\ny\n```", "b"]);
  });

  it("strips a leading H1 and extracts mermaid blocks", () => {
    expect(stripLeadingH1("# Title\n\nbody")).toBe("body");
    expect(stripLeadingH1("no heading")).toBe("no heading");
    expect(extractMermaidBlocks("text\n\n```mermaid\ngraph TD\nA-->B\n```\n\nmore")).toEqual([
      "graph TD\nA-->B",
    ]);
  });

  it("tells prose from tables, lists, stat lines and footnotes", () => {
    expect(isProse("The service accepts orders and hands them to a queue.")).toBe(true);
    expect(isProse("The subsystem has three distinct ways in, listed below:")).toBe(true);
    expect(isProse("| a | b |\n|---|---|\n| 1 | 2 |")).toBe(false);
    expect(isProse("- one item that is long enough to pass the length floor.")).toBe(false);
    expect(isProse("**Files:** 130 | **Lines:** 16167 | **Packages:** 3 and more.")).toBe(false);
    expect(isProse("*Built from the code's structure. It states what is there.*")).toBe(false);
    expect(isProse("A paragraph that stops without finishing its")).toBe(false);
  });

  it("keeps whole sentences and never cuts one", () => {
    const text = "First sentence is here. Second one, e.g. with an aside, follows. Third.";
    expect(wholeSentences(text, 30)).toBe("First sentence is here.");
    expect(wholeSentences(text, 70)).toBe(
      "First sentence is here. Second one, e.g. with an aside, follows.",
    );
    const long = `${"word ".repeat(100).trim()}.`;
    expect(wholeSentences(long, 20)).toBe(long);
  });

  it("treats an edgeless flowchart as not worth drawing", () => {
    expect(isDrawable('flowchart LR\n  subgraph a["A"]\n    x["X -- label"]\n  end')).toBe(false);
    expect(isDrawable(FLOW)).toBe(true);
    expect(isDrawable("graph TD\n  a -.-> b")).toBe(true);
    expect(isDrawable(SEQUENCE)).toBe(true);
  });

  it("reads the diagram type past front matter and init directives", () => {
    expect(diagramKind("%%{init: {'theme': 'base'}}%%\nsequenceDiagram\n  A->>B: x")).toBe(
      "sequencediagram",
    );
    expect(diagramKind("---\ntitle: Flow\n---\n%% note\nflowchart LR\n  a --> b")).toBe(
      "flowchart",
    );
    expect(isDrawable('%%{init: {}}%%\nflowchart LR\n  a["A"]')).toBe(false);
  });
});

describe("canPresent", () => {
  it("requires an overview page", () => {
    expect(canPresent([makePage()])).toBe(false);
    expect(canPresent([makePage({ page_type: "repo_overview" })])).toBe(true);
  });
});

describe("buildPresentModel", () => {
  it("tells the story in order on the current page contract", () => {
    const overview = makePage({
      id: "ov",
      page_type: "repo_overview",
      title: "Repository Overview: acme",
      target_path: "acme",
      content: [
        "**Files:** 40 | **Lines:** 5000",
        "",
        "## Summary",
        "",
        "Acme turns orders into shipments. It runs as an API in front of a pool of workers.",
        "",
        "## Architecture",
        "",
        "Requests enter through the API and wait on a queue until a worker takes them.",
        "",
        "```mermaid",
        FLOW,
        "```",
      ].join("\n"),
    });
    const part = makePage({
      id: "m1",
      page_type: "module_page",
      title: "Order Intake",
      target_path: "src/intake",
      content: CONTRACT_PART,
    });
    const model = buildPresentModel(
      source({
        overview,
        parts: [part],
        totalParts: 1,
        pageIdByPath: new Map([["src/intake/handler.py", "f1"]]),
      }),
    );

    expect(model.repoName).toBe("acme");
    expect(model.slides.map((s) => s.kind)).toEqual(["title", "architecture", "part", "start"]);

    const [title, arch, partSlide, start] = model.slides;
    expect(title!.body).toBe(
      "Acme turns orders into shipments. It runs as an API in front of a pool of workers.",
    );
    expect(arch!.mermaid).toBe(FLOW);
    expect(arch!.body).toBe(
      "Requests enter through the API and wait on a queue until a worker takes them.",
    );
    expect(partSlide!.eyebrow).toBe("Part 1 of 1");
    expect(partSlide!.body).toBe(
      "This part accepts orders over HTTP and hands them to the queue. Everything downstream reads what it writes.",
    );
    expect(partSlide!.mermaid).toBe(SEQUENCE);
    expect(partSlide!.steps).toEqual(["From request to queued job", "How retries are bounded"]);
    // The part's reading list resolves relative to its directory.
    expect(start!.start?.[0]).toEqual({
      label: "Order Intake",
      note: "The request entry point.",
      files: [{ path: "src/intake/handler.py", pageId: "f1" }],
    });
    // Every part was presented, so no scope note.
    expect(start!.body).toBeUndefined();
  });

  it("adds one flow slide for a sequence diagram no part slide shows", () => {
    const part = makePage({
      id: "m1",
      page_type: "module_page",
      title: "Workers",
      target_path: "src/workers",
      content: [
        "Workers drain the queue and write shipments to the store.",
        "",
        "```mermaid",
        FLOW,
        "```",
        "",
        "## How a job runs",
        "",
        "A worker claims a job, runs it, then acknowledges it.",
        "",
        "```mermaid",
        SEQUENCE,
        "```",
      ].join("\n"),
    });
    const model = buildPresentModel(source({ parts: [part], totalParts: 1 }));
    const flow = model.slides.find((s) => s.kind === "flow");
    expect(flow?.title).toBe("How a job runs");
    expect(flow?.mermaid).toBe(SEQUENCE);
    expect(flow?.body).toBe("A worker claims a job, runs it, then acknowledges it.");
    expect(model.slides.find((s) => s.kind === "part")?.mermaid).toBe(FLOW);
  });

  it("degrades on older pages: no diagrams, list lead-ins, reference sections", () => {
    const part = makePage({
      id: "m1",
      page_type: "module_page",
      title: "Storage",
      target_path: "src/storage",
      content: [
        "## How storage is divided",
        "",
        "Storage is split into two layers with separate jobs:",
        "",
        "| Layer | Role |",
        "|---|---|",
        "| cache | reads |",
        "",
        "Both layers share one connection pool and never open their own.",
        "",
        "## Questions this page answers",
        "",
        "- Where is data cached?",
      ].join("\n"),
    });
    const model = buildPresentModel(source({ parts: [part], totalParts: 4 }));
    const partSlide = model.slides.find((s) => s.kind === "part")!;
    expect(partSlide.body).toBe(
      "Storage is split into two layers with separate jobs. Both layers share one connection pool and never open their own.",
    );
    expect(partSlide.mermaid).toBeUndefined();
    expect(partSlide.steps).toEqual([]);
    expect(model.slides.some((s) => s.kind === "architecture")).toBe(false);
    // Three parts were left out, and the deck says so.
    expect(model.slides.at(-1)?.body).toBe(
      "This deck covers 1 of the 4 top-level sections. The rest are in the documentation.",
    );
  });

  it("skips an edgeless architecture map", () => {
    const overview = makePage({
      id: "ov",
      page_type: "repo_overview",
      title: "Overview",
      content:
        'Acme is a small service that ships orders.\n\n## Map\n\n```mermaid\nflowchart LR\n  a["A"]\n  b["B"]\n```',
    });
    const model = buildPresentModel(source({ overview }));
    expect(model.slides.map((s) => s.kind)).toEqual(["title"]);
  });

  it("fills the reading list from the guided tour, deduped and grouped by reason", () => {
    const overview = makePage({
      id: "ov",
      page_type: "repo_overview",
      title: "Overview",
      content: "Acme is a small service that ships orders.",
      metadata: {
        guided_tour: [
          { target_path: "README.md", reason: "Start here." },
          { target_path: "src/app.py", reason: "An entry point." },
          { target_path: "src/cli.py", reason: "An entry point." },
          { target_path: "src/db.py", reason: "Widely imported." },
          { target_path: "src/app.py", reason: "An entry point." },
          { title: "no path" },
        ],
      },
    });
    const model = buildPresentModel(
      source({ overview, pageIdByPath: new Map([["src/app.py", "f-app"]]) }),
    );
    const start = model.slides.find((s) => s.kind === "start");
    expect(start?.start).toEqual([
      { note: "Start here.", files: [{ path: "README.md", pageId: undefined }] },
      {
        note: "An entry point.",
        files: [
          { path: "src/app.py", pageId: "f-app" },
          { path: "src/cli.py", pageId: undefined },
        ],
      },
      { note: "Widely imported.", files: [{ path: "src/db.py", pageId: undefined }] },
    ]);
  });

  it("does not repeat a tour stop a part already recommends", () => {
    const overview = makePage({
      id: "ov",
      page_type: "repo_overview",
      title: "Overview",
      metadata: {
        guided_tour: [
          { target_path: "src/intake/handler.py", reason: "An entry point." },
          { target_path: "src/db.py", reason: "Widely imported." },
        ],
      },
    });
    const part = makePage({
      id: "m1",
      page_type: "module_page",
      title: "Order Intake",
      target_path: "src/intake",
      content: CONTRACT_PART,
    });
    const model = buildPresentModel(
      source({
        overview,
        parts: [part],
        totalParts: 1,
        pageIdByPath: new Map([["src/intake/handler.py", "f1"]]),
      }),
    );
    const paths = model.slides
      .find((s) => s.kind === "start")
      ?.start?.flatMap((g) => g.files.map((f) => f.path));
    expect(paths).toEqual(["src/intake/handler.py", "src/db.py"]);
  });

  it("always yields a title slide, even for a bare overview", () => {
    const model = buildPresentModel(
      source({
        overview: makePage({ id: "ov", page_type: "repo_overview", title: "Overview", content: "" }),
      }),
    );
    expect(model.slides).toHaveLength(1);
    expect(model.slides[0]).toMatchObject({ kind: "title", title: "Overview", body: undefined });
  });
});
