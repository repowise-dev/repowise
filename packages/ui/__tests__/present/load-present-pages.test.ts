import { describe, it, expect, vi } from "vitest";
import { loadPresentPages } from "../../src/present/load-present-pages.js";
import type { DocPage, DocPageSummary } from "@repowise-dev/types/docs";

function summary(overrides: Partial<DocPageSummary> = {}): DocPageSummary {
  return {
    id: "p1",
    repository_id: "r1",
    page_type: "file_page",
    title: "Page",
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
    human_notes: null,
    created_at: "",
    updated_at: "",
    ...overrides,
  };
}

function full(overrides: Partial<DocPage> = {}): DocPage {
  return {
    ...summary(overrides),
    content: "body",
    metadata: {},
    ...overrides,
  };
}

/** Fetcher that serves whatever it is given and records what was asked for. */
function fetcher(pool: DocPage[]) {
  const asked: string[] = [];
  const fetchPage = vi.fn(async (id: string) => {
    asked.push(id);
    const hit = pool.find((p) => p.id === id);
    if (!hit) throw new Error(`no page ${id}`);
    return hit;
  });
  return { fetchPage, asked };
}

describe("loadPresentPages", () => {
  it("returns nothing when there is no overview to present", async () => {
    const { fetchPage } = fetcher([]);
    expect(await loadPresentPages([summary()], fetchPage)).toEqual([]);
    expect(fetchPage).not.toHaveBeenCalled();
  });

  it("leaves the bulk of the wiki alone", async () => {
    const pages: DocPageSummary[] = [
      summary({ id: "overview", page_type: "repo_overview", target_path: "repo" }),
      ...Array.from({ length: 500 }, (_, i) =>
        summary({ id: `file-${i}`, target_path: `src/f${i}.ts` }),
      ),
    ];
    const { fetchPage, asked } = fetcher([
      full({ id: "overview", page_type: "repo_overview", target_path: "repo" }),
    ]);

    const loaded = await loadPresentPages(pages, fetchPage);

    expect(asked).toEqual(["overview"]);
    expect(loaded.map((p) => p.id)).toEqual(["overview"]);
  });

  it("takes the richest modules by content_chars, not by whatever came first", async () => {
    const modules = [
      summary({ id: "m-small", page_type: "module_page", content_chars: 10 }),
      summary({ id: "m-big", page_type: "module_page", content_chars: 9000 }),
      summary({ id: "m-mid", page_type: "module_page", content_chars: 500 }),
    ];
    const pages = [
      summary({ id: "overview", page_type: "repo_overview" }),
      ...modules,
    ];
    const pool = [
      full({ id: "overview", page_type: "repo_overview" }),
      ...modules.map((m) => full({ id: m.id, page_type: "module_page" })),
    ];
    const { fetchPage, asked } = fetcher(pool);

    await loadPresentPages(pages, fetchPage);

    // All three fit under the cap, but the order they are taken in is the
    // order the deck shows them.
    expect(asked.slice(1)).toEqual(["m-big", "m-mid", "m-small"]);
  });

  it("follows the overview's guided tour to its landmark pages", async () => {
    const overview = full({
      id: "overview",
      page_type: "repo_overview",
      metadata: {
        guided_tour: [
          { order: 1, target_path: "src/entry.ts" },
          { order: 2, target_path: "src/missing.ts" },
        ],
      },
    });
    const pages = [
      summary({ id: "overview", page_type: "repo_overview" }),
      summary({ id: "entry", target_path: "src/entry.ts" }),
    ];
    const { fetchPage, asked } = fetcher([
      overview,
      full({ id: "entry", target_path: "src/entry.ts" }),
    ]);

    const loaded = await loadPresentPages(pages, fetchPage);

    // The stop with no page behind it is skipped rather than fetched blindly.
    expect(asked).toEqual(["overview", "entry"]);
    expect(loaded.map((p) => p.id)).toEqual(["overview", "entry"]);
  });

  it("does not refetch a row that already carries its body", async () => {
    const pages = [
      full({ id: "overview", page_type: "repo_overview" }),
      full({ id: "arch", page_type: "architecture_diagram" }),
    ];
    const { fetchPage } = fetcher([]);

    const loaded = await loadPresentPages(pages, fetchPage);

    expect(fetchPage).not.toHaveBeenCalled();
    expect(loaded.map((p) => p.id)).toEqual(["overview", "arch"]);
  });
});
