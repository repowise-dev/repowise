import { describe, it, expect } from "vitest";
import { buildPresentModel, canPresent } from "../../src/present/build-present-model.js";
import {
  splitOnH2,
  extractMermaidBlocks,
  stripLeadingH1,
  countWords,
  clampProse,
} from "../../src/present/split-markdown.js";
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

describe("split-markdown", () => {
  it("splits on H2 and keeps the lead", () => {
    const { lead, sections } = splitOnH2("Intro line.\n\n## First\nbody a\n\n## Second\nbody b");
    expect(lead).toBe("Intro line.");
    expect(sections.map((s) => s.heading)).toEqual(["First", "Second"]);
    expect(sections[0]!.body).toContain("body a");
  });

  it("ignores ## inside a code fence", () => {
    const { sections } = splitOnH2("lead\n\n```\n## not a heading\n```\n\n## Real\nx");
    expect(sections.map((s) => s.heading)).toEqual(["Real"]);
  });

  it("strips a single leading H1", () => {
    expect(stripLeadingH1("# Title\n\nbody")).toBe("body");
    expect(stripLeadingH1("no heading")).toBe("no heading");
  });

  it("extracts mermaid blocks", () => {
    const blocks = extractMermaidBlocks("text\n\n```mermaid\ngraph TD\nA-->B\n```\n\nmore");
    expect(blocks).toEqual(["graph TD\nA-->B"]);
  });

  it("counts words ignoring fences and markdown punctuation", () => {
    expect(countWords("## Heading\n\none two three\n\n```\nignored code here\n```")).toBe(4);
  });

  it("clamps prose on paragraph boundaries but always returns the first", () => {
    const long = "a".repeat(600);
    expect(clampProse(`${long}\n\nsecond`, 300)).toBe(long);
  });
});

describe("canPresent", () => {
  it("requires a repo_overview page", () => {
    expect(canPresent([makePage({ page_type: "file_page" })])).toBe(false);
    expect(canPresent([makePage({ page_type: "repo_overview" })])).toBe(true);
  });
});

describe("buildPresentModel", () => {
  const overview = makePage({
    id: "ov",
    page_type: "repo_overview",
    title: "Acme Overview",
    target_path: "repo",
    content: "# Acme\n\nA build tool.\n\n## Details\nmore",
    metadata: {
      layer_order: ["API", "Core"],
      guided_tour: [
        { order: 1, title: "main.py", target_path: "src/main.py", reason: "entry point" },
        { order: 2, title: "core.py", target_path: "src/core.py", reason: "the engine" },
      ],
    },
  });
  const arch = makePage({
    id: "arch",
    page_type: "architecture_diagram",
    title: "System architecture",
    content: "Intro\n\n```mermaid\ngraph TD\nA-->B\n```",
  });
  const layerCore = makePage({
    id: "lc",
    page_type: "layer_page",
    title: "Core layer",
    content: "# Core\n\nThe core does the work and holds the pipeline together nicely.",
    metadata: { layer_name: "Core" },
  });
  const layerApi = makePage({
    id: "la",
    page_type: "layer_page",
    title: "API layer",
    content: "# API\n\nThe API exposes endpoints to callers over HTTP with care.",
    metadata: { layer_name: "API" },
  });
  const mainFile = makePage({ id: "mf", target_path: "src/main.py", title: "main.py", content: "# main\n\nStarts the program and wires everything up on boot." });

  it("derives a clean repo name from a 'Repository Overview: name' title", () => {
    const colonOverview = makePage({
      page_type: "repo_overview",
      title: "Repository Overview: repowise",
      content: "## Project Summary\n\nRepowise is a codebase documentation engine.",
      metadata: {},
    });
    const model = buildPresentModel([colonOverview]);
    expect(model.repoName).toBe("repowise");
    // Title tagline is the real summary sentence, not the "Project Summary" heading.
    expect(model.deck[0]!.bodyMarkdown).toContain("codebase documentation engine");
    expect(model.deck[0]!.bodyMarkdown).not.toContain("Project Summary");
  });

  it("builds a title slide, diagram slide, ordered layers, and a closing", () => {
    const model = buildPresentModel([overview, arch, layerCore, layerApi, mainFile]);
    expect(model.repoName).toBe("Acme");
    expect(model.deck[0]!.kind).toBe("title");
    expect(model.deck.some((s) => s.kind === "diagram" && s.mermaid?.includes("graph TD"))).toBe(true);
    // Layers ordered by layer_order: API before Core.
    const layerTitles = model.deck.filter((s) => s.eyebrow === "Layer").map((s) => s.title);
    expect(layerTitles).toEqual(["API layer", "Core layer"]);
    expect(model.deck[model.deck.length - 1]!.kind).toBe("closing");
  });

  it("builds a walkthrough from the guided tour with time estimates", () => {
    const model = buildPresentModel([overview, mainFile]);
    expect(model.walkthrough).toHaveLength(2);
    expect(model.walkthrough[0]!.title).toBe("main.py");
    expect(model.walkthrough[0]!.reason).toBe("entry point");
    expect(model.walkthrough[0]!.sourcePageId).toBe("mf");
    expect(model.walkthrough[0]!.estMinutes).toBeGreaterThanOrEqual(2);
    expect(model.totalMinutes).toBe(
      model.walkthrough.reduce((s, w) => s + w.estMinutes, 0),
    );
  });

  it("falls back to deck sections when there is no guided tour", () => {
    const bareOverview = makePage({ id: "ov2", page_type: "repo_overview", title: "Bare", content: "# Bare\n\nx", metadata: {} });
    const model = buildPresentModel([bareOverview, layerCore]);
    expect(model.walkthrough.length).toBeGreaterThan(0);
  });
});
