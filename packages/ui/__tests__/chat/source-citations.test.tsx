import { describe, it, expect } from "vitest";
import { extractSources } from "../../src/chat/source-citations.js";
import type { ChatUIToolCall } from "@repowise-dev/types/chat";

function searchCall(results: Array<Record<string, unknown>>): ChatUIToolCall {
  return {
    id: "tc1",
    name: "search_codebase",
    arguments: { query: "where is auth handled" },
    result: { results },
    status: "done",
  };
}

describe("extractSources", () => {
  it("reads the rank-normalized confidence, not the raw backend score", () => {
    // BM25 fallback scores are unbounded — reading relevance_score here is what
    // rendered "1808%" in the sources list.
    const sources = extractSources(
      [
        searchCall([
          {
            page_id: "file_page:auth.py",
            title: "auth.py",
            relevance_score: 18.08,
            confidence_score: 1,
          },
        ]),
      ],
      "repo1",
    );

    expect(sources).toHaveLength(1);
    expect(sources[0]?.confidence).toBe(1);
  });

  it("keeps every confidence renderable as a 0-100% badge", () => {
    const sources = extractSources(
      [
        searchCall([
          { page_id: "a", title: "a", relevance_score: 18.08, confidence_score: 1 },
          { page_id: "b", title: "b", relevance_score: 14.75, confidence_score: 0.82 },
          { page_id: "c", title: "c", relevance_score: 0.43, confidence_score: 0.02 },
        ]),
      ],
      "repo1",
    );

    expect(sources).toHaveLength(3);
    for (const s of sources) {
      expect(s.confidence).toBeGreaterThanOrEqual(0);
      expect(s.confidence).toBeLessThanOrEqual(1);
    }
  });

  it("omits confidence when the server sends no confidence_score", () => {
    const sources = extractSources(
      [searchCall([{ page_id: "file_page:a.py", title: "a.py", relevance_score: 9.1 }])],
      "repo1",
    );

    // Badge hides rather than falling back to the unbounded raw score.
    expect(sources[0]?.confidence).toBeUndefined();
  });
});
