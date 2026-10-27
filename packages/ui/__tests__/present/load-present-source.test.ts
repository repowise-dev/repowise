import { describe, it, expect, vi } from "vitest";
import { loadPresentSource } from "../../src/present/load-present-source.js";
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

/** Fetcher that serves a page for any id and records what was asked for. */
function fetcher() {
  const asked: string[] = [];
  const fetchPage = vi.fn(async (id: string) => {
    asked.push(id);
    return full({ id });
  });
  return { fetchPage, asked };
}

const OVERVIEW = summary({ id: "ov", page_type: "repo_overview", target_path: "repo" });

function module(id: string, overrides: Partial<DocPageSummary> = {}): DocPageSummary {
  return summary({ id, page_type: "module_page", target_path: `src/${id}`, ...overrides });
}

describe("loadPresentSource", () => {
  it("returns null when there is no overview to present", async () => {
    const { fetchPage } = fetcher();
    expect(await loadPresentSource([summary()], fetchPage)).toBeNull();
    expect(fetchPage).not.toHaveBeenCalled();
  });

  it("fetches only the overview and the chosen parts", async () => {
    const pages = [
      OVERVIEW,
      module("m1"),
      ...Array.from({ length: 500 }, (_, i) => summary({ id: `f${i}`, target_path: `src/f${i}.ts` })),
    ];
    const { fetchPage, asked } = fetcher();

    const source = await loadPresentSource(pages, fetchPage);

    expect(asked).toEqual(["ov", "m1"]);
    expect(source?.parts.map((p) => p.id)).toEqual(["m1"]);
    expect(source?.pageIdByPath.get("src/f7.ts")).toBe("f7");
  });

  it("picks the top-level sections with the most nested pages, shown in outline order", async () => {
    const pages = [
      OVERVIEW,
      module("small", { parent_page_id: "ov", display_order: 1 }),
      module("big", { parent_page_id: "ov", display_order: 3 }),
      module("big-a", { parent_page_id: "big" }),
      module("big-b", { parent_page_id: "big" }),
      module("big-a-1", { parent_page_id: "big-a" }),
      module("mid", { parent_page_id: "ov", display_order: 2 }),
      module("mid-a", { parent_page_id: "mid" }),
      ...Array.from({ length: 6 }, (_, i) =>
        module(`leaf${i}`, { parent_page_id: "ov", display_order: 10 + i }),
      ),
    ];
    const { fetchPage } = fetcher();

    const source = await loadPresentSource(pages, fetchPage);

    // Nine top-level sections; the cap keeps six. The two with children rank
    // first, and the deck presents the selection in outline order.
    expect(source?.totalParts).toBe(9);
    expect(source?.parts.map((p) => p.id)).toEqual([
      "small",
      "mid",
      "big",
      "leaf0",
      "leaf1",
      "leaf2",
    ]);
  });

  it("ranks a flat wiki by how much was written", async () => {
    const pages = [
      OVERVIEW,
      module("m-small", { content_chars: 10 }),
      module("m-big", { content_chars: 9000 }),
      module("m-mid", { content_chars: 500 }),
    ];
    const { fetchPage } = fetcher();

    const source = await loadPresentSource(pages, fetchPage);

    expect(source?.parts.map((p) => p.id)).toEqual(["m-big", "m-mid", "m-small"]);
  });

  it("does not hang on a malformed parent cycle", async () => {
    const pages = [OVERVIEW, module("a", { parent_page_id: "b" }), module("b", { parent_page_id: "a" })];
    const { fetchPage } = fetcher();

    const source = await loadPresentSource(pages, fetchPage);

    expect(source?.parts).toEqual([]);
  });

  it("does not refetch a row that already carries its body", async () => {
    const pages = [full({ id: "ov", page_type: "repo_overview" }), full({ id: "m1", page_type: "module_page" })];
    const { fetchPage } = fetcher();

    const source = await loadPresentSource(pages, fetchPage);

    expect(fetchPage).not.toHaveBeenCalled();
    expect(source?.overview.id).toBe("ov");
    expect(source?.parts.map((p) => p.id)).toEqual(["m1"]);
  });
});
