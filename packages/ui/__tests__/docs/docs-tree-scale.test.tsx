import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { DocsTree } from "../../src/docs/docs-tree.js";
import type { DocPageSummary } from "@repowise-dev/types/docs";

beforeEach(() => {
  window.localStorage.clear();
});

function page(overrides: Partial<DocPageSummary>): DocPageSummary {
  return {
    id: "p",
    repository_id: "r1",
    page_type: "file_page",
    title: "t",
    target_path: "src/x.py",
    source_hash: "h",
    model_name: "",
    provider_name: "template",
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
    generation_level: 1,
    version: 1,
    confidence: 1,
    freshness_status: "fresh",
    human_notes: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

/** The shape of a large real index: thousands of files, each with symbols. */
function bigCorpus(files: number, symbolsPerFile: number): DocPageSummary[] {
  const out: DocPageSummary[] = [];
  for (let f = 0; f < files; f++) {
    const path = `pkg${f % 40}/mod${f % 7}/file_${f}.py`;
    const fileId = `file_page:${path}`;
    out.push(page({ id: fileId, target_path: path, title: `file_${f}.py` }));
    for (let s = 0; s < symbolsPerFile; s++) {
      out.push(
        page({
          id: `symbol_spotlight:${path}::get_${s}`,
          page_type: "symbol_spotlight",
          target_path: `${path}::get_${s}`,
          title: `get_${s}`,
          parent_page_id: fileId,
        }),
      );
    }
  }
  return out;
}

describe("DocsTree at the scale of a large index", () => {
  it("keeps a broad search to a bounded number of rendered rows", () => {
    vi.useFakeTimers();
    try {
      render(
        <DocsTree pages={bigCorpus(3000, 2)} selectedPageId={null} onSelectPage={() => {}} />,
      );
      fireEvent.change(screen.getByRole("textbox"), { target: { value: "get" } });
      act(() => {
        vi.advanceTimersByTime(500);
      });
      const rows = screen.getAllByRole("button").length;
      expect(rows).toBeLessThan(400);
      expect(screen.getByText(/Show .* more/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
