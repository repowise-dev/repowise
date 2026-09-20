import { describe, it, expect } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
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
  // These five tools produced no citation at all before: a reader could not
  // check the scored claim the answer was built on. Field names are the live
  // wire shapes, which differ per tool (`file` on get_symbol, `file_path`
  // elsewhere, a path-keyed object on get_risk).
  it("cites the files the scored tools actually read", () => {
    const calls: ChatUIToolCall[] = [
      {
        id: "r1",
        name: "get_risk",
        arguments: {},
        result: { targets: { "packages/core/persist.py": { hotspot_score: 0.9 } } },
        status: "done",
      },
      {
        id: "h1",
        name: "get_health",
        arguments: {},
        result: { findings: [{ file_path: "packages/cli/command.py" }] },
        status: "done",
      },
      {
        id: "d1",
        name: "get_dead_code",
        arguments: {},
        result: { high_confidence: [{ file_path: "packages/web/unused.ts" }] },
        status: "done",
      },
      {
        id: "s1",
        name: "get_symbol",
        arguments: {},
        result: { file: "packages/ui/widget.tsx", name: "Widget" },
        status: "done",
      },
    ];

    const paths = extractSources(calls, "repo1").map((s) => s.targetPath);

    expect(paths).toContain("packages/core/persist.py");
    expect(paths).toContain("packages/cli/command.py");
    expect(paths).toContain("packages/web/unused.ts");
    expect(paths).toContain("packages/ui/widget.tsx");
  });

  it("does not cite a tool call that failed", () => {
    // A failed read is marked status "error"; citing it would advertise
    // evidence the answer never had.
    const sources = extractSources(
      [
        {
          id: "r2",
          name: "get_risk",
          arguments: {},
          result: { error: "no git metadata available" },
          status: "error",
        },
      ],
      "repo1",
    );

    expect(sources).toHaveLength(0);
  });

  it("survives a malformed payload rather than throwing", () => {
    const sources = extractSources(
      [
        {
          id: "h2",
          name: "get_health",
          arguments: {},
          result: { findings: [{ file_path: 42 }], targets: [null, "ok.py"] },
          status: "done",
        },
      ],
      "repo1",
    );

    expect(sources.map((s) => s.targetPath)).toEqual(["ok.py"]);
  });

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

  it("cites files from get_why health stale_decisions", () => {
    const sources = extractSources(
      [
        {
          id: "why1",
          name: "get_why",
          arguments: {},
          status: "done",
          result: {
            mode: "health",
            stale_decisions: [
              {
                title: "Prefer SSE",
                affected_files: ["packages/server/src/chat.py"],
              },
            ],
          },
        },
      ],
      "repo1",
    );

    expect(sources).toHaveLength(1);
    expect(sources[0]?.targetPath).toBe("packages/server/src/chat.py");
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

  it("collapses end-of-answer sources by default and lets the reader expand them", () => {
    const { container } = renderList();
    const disclosure = container.querySelector("details");
    expect(disclosure).not.toHaveAttribute("open");
    expect(screen.getByText("Sources · 2")).toBeInTheDocument();

    fireEvent.click(screen.getByText("Sources · 2"));
    expect(disclosure).toHaveAttribute("open");
  });
});
