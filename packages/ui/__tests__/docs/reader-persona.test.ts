import { describe, it, expect } from "vitest";
import {
  DEFAULT_PERSONA,
  filterMarkdownByPersona,
  personaFilteringApplies,
} from "../../src/docs/reader-persona.js";

/**
 * The reader lens over a deterministic page.
 *
 * A file page's `## In the code` and `## Questions this page answers` are
 * written for the index, not for a reader: one is a bag of the file's own
 * tokens, the other is question-shaped text so a query has something to match.
 * They cannot be dropped upstream — `content` is a single string that FTS
 * indexes, the vector store embeds and `get_context` returns verbatim — so the
 * lens is where they stop being a reader's problem.
 *
 * What the rendered page actually emits is pinned on the Python side, against
 * the template rather than against a string typed here.
 */

const PAGE = [
  "# src/walk.py",
  "",
  "## Overview",
  "",
  "Walks a repository tree.",
  "",
  "## Public API",
  "",
  "| Symbol | Kind |",
  "| --- | --- |",
  "| `walk` | function |",
  "",
  "## Questions this page answers",
  "",
  "- What does `src/walk.py` export?",
  "",
  "## In the code",
  "",
  "prune_dirs max_depth node_modules",
].join("\n");

describe("filterMarkdownByPersona", () => {
  it("hides the retrieval scaffolding from the default reader", () => {
    const out = filterMarkdownByPersona(PAGE, "contributor");

    expect(out).not.toContain("## Questions this page answers");
    expect(out).not.toContain("## In the code");
    expect(out).not.toContain("prune_dirs max_depth node_modules");
  });

  it("keeps the reference material the default reader came for", () => {
    const out = filterMarkdownByPersona(PAGE, "contributor");

    expect(out).toContain("## Overview");
    expect(out).toContain("Walks a repository tree.");
    expect(out).toContain("## Public API");
    expect(out).toContain("`walk`");
  });

  it("shows everything at the deep level", () => {
    expect(filterMarkdownByPersona(PAGE, "deep")).toBe(PAGE);
  });

  it("never shows at the overview level what the contributor level hides", () => {
    const contributor = filterMarkdownByPersona(PAGE, "contributor");
    const overview = filterMarkdownByPersona(PAGE, "overview");

    for (const heading of PAGE.split("\n").filter((l) => l.startsWith("## "))) {
      if (!contributor.includes(heading)) expect(overview).not.toContain(heading);
    }
    expect(overview).not.toContain("## Public API");
  });

  it("leaves the persona control on a page it now filters", () => {
    // The control is gated on this; hiding two more sections may only make it
    // apply to more pages, never to fewer.
    expect(personaFilteringApplies(PAGE)).toBe(true);
    expect(DEFAULT_PERSONA).toBe("contributor");
  });

  it("still reports no effect on a page with nothing to hide", () => {
    expect(personaFilteringApplies("# Title\n\n## Overview\n\nProse.\n")).toBe(false);
  });
});
