import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  SourceCitations,
  extractSources,
} from "../../src/chat/source-citations.js";
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

describe("SourceCitations render", () => {
  const LONG_TITLE =
    "Authentication session handling and refresh-token rotation";

  function renderList() {
    return render(
      <SourceCitations
        toolCalls={[
          searchCall([
            {
              page_id: "file_page:auth.py",
              title: LONG_TITLE,
              confidence_score: 0.82,
            },
            { page_id: "file_page:b.py", title: "b.py", confidence_score: 0.4 },
          ]),
        ]}
        repoId="repo1"
      />,
    );
  }

  it("renders sources as links, not numbered chips", () => {
    // A border and a ground on every entry turned a list of eight into a wall
    // of boxes that outweighed the answer above it. The counter went with them:
    // numbering only earns its weight when the prose cites [1], and it does not.
    const { container } = renderList();
    expect(container.querySelectorAll("a")).toHaveLength(2);
    expect(screen.queryByText("1")).not.toBeInTheDocument();
    expect(screen.queryByText("2")).not.toBeInTheDocument();
  });

  it("does not truncate a source title", () => {
    // Rule 6: a cut title reports a width decision as missing content.
    renderList();
    const title = screen.getByText(LONG_TITLE);
    expect(title.className).not.toContain("truncate");
    expect(title.className).not.toContain("max-w-");
  });

  it("renders confidence as tabular mono", () => {
    renderList();
    const pct = screen.getByText("82%");
    expect(pct.className).toContain("tabular-nums");
    expect(pct.className).toContain("font-mono");
  });
});
