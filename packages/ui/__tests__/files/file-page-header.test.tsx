import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { FilePageHeader } from "../../src/files/file-page-header.js";
import { fileTabsFor } from "../../src/files/file-page-tabs.js";
import type { FileDetailResponse } from "@repowise-dev/types/files";

function makeData(overrides: Partial<FileDetailResponse> = {}): FileDetailResponse {
  return {
    file_path: "packages/ui/src/files/file-page.tsx",
    wiki_page: null,
    health: { metric: null, breakdown: null, findings: [], trend: null, signals: null },
    git: null,
    coverage: null,
    graph: null,
    symbols: [],
    function_blame: [],
    governing_decisions: [],
    dead_code: [],
    ...overrides,
  } as unknown as FileDetailResponse;
}

function metric(score: number) {
  return {
    file_path: "packages/ui/src/files/file-page.tsx",
    score,
    max_ccn: 4,
    max_nesting: 2,
    nloc: 245,
    has_test_file: true,
    line_coverage_pct: null,
    module: null,
    duplication_pct: null,
  };
}

describe("FilePageHeader health band", () => {
  it("bands a 6.9 as Warning, the way every other surface does", () => {
    // The trap this replaced: `scoreBadgeClass` is a four-step presentation
    // ramp that calls 6.9 "fair" and paints it caution, while the treemap and
    // the map that link here paint `bandForScore`. Same file, two readings.
    render(
      <FilePageHeader
        data={makeData({ health: { ...makeData().health, metric: metric(6.9) } })}
        linkPrefix="/repos/r1"
      />,
    );
    expect(screen.getByText("6.9")).toBeTruthy();
    expect(screen.getByText("Warning")).toBeTruthy();
  });

  it("bands an 8.0 as Healthy", () => {
    render(
      <FilePageHeader
        data={makeData({ health: { ...makeData().health, metric: metric(8.0) } })}
        linkPrefix="/repos/r1"
      />,
    );
    expect(screen.getByText("Healthy")).toBeTruthy();
  });

  it("renders the path without truncating it", () => {
    render(<FilePageHeader data={makeData()} linkPrefix="/repos/r1" />);
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading.textContent).toBe("packages/ui/src/files/file-page.tsx");
  });
});

describe("FilePageHeader marks", () => {
  it("renders no marker row for a file with nothing to report", () => {
    render(<FilePageHeader data={makeData()} linkPrefix="/repos/r1" />);
    expect(screen.queryByText("Hotspot")).toBeNull();
    expect(screen.queryByText("Entry point")).toBeNull();
  });

  it("names every marker rather than collapsing them into one dot", () => {
    // A priority cascade drawn as a single mark is silent data loss: a file
    // that is both an entry point and a hotspot must report both.
    render(
      <FilePageHeader
        data={makeData({
          graph: {
            language: "typescript",
            is_entry_point: true,
            is_test: false,
            symbol_count: 12,
            pagerank: 0.001,
            pagerank_percentile: 88,
            in_degree: 3,
            out_degree: 9,
            community_id: 1,
            community_label: null,
            dependents: [],
            dependencies: [],
          },
          git: { is_hotspot: true } as never,
        })}
        linkPrefix="/repos/r1"
      />,
    );
    expect(screen.getByText("Entry point")).toBeTruthy();
    expect(screen.getByText("Hotspot")).toBeTruthy();
  });
});

describe("FilePageHeader documentation door", () => {
  // This link used to live inside the Doc tab, which made the file page's one
  // way back to the wiki reachable only from the tab that already renders the
  // wiki. In the header it is on screen from Health, Dependencies and the rest.
  it("offers the documentation page when there is one", () => {
    render(
      <FilePageHeader
        data={makeData()}
        linkPrefix="/repos/r1"
        wikiHref="/repos/r1/docs?page=file_page%3Aa.ts"
      />,
    );
    const link = screen.getByRole("link", { name: /Read in Docs/i });
    expect(link.getAttribute("href")).toBe("/repos/r1/docs?page=file_page%3Aa.ts");
  });

  it("names the surface, not the tab", () => {
    // "Documentation" is the tab, forty pixels below this and staying on the
    // page. A door out wearing near enough the same name is two controls for
    // one subject; "Docs" is what the nav calls the place it goes.
    render(
      <FilePageHeader data={makeData()} linkPrefix="/repos/r1" wikiHref="/x" />,
    );
    expect(screen.queryByRole("link", { name: /Documentation/i })).toBeNull();
  });

  it("renders no door when the file has no page", () => {
    // Rule 21. The docs reader replies to a `?page=` it cannot find by naming
    // the missing page, which is a fine answer to a reader who typed it and a
    // poor one to a link this header offered — and here we already know.
    render(<FilePageHeader data={makeData()} linkPrefix="/repos/r1" />);
    expect(screen.queryByRole("link", { name: /Read in Docs/i })).toBeNull();
  });
});

describe("fileTabsFor", () => {
  it("badges only what there is something to say about", () => {
    const tabs = fileTabsFor(makeData());
    expect(tabs.find((t) => t.id === "health")?.badge).toBeUndefined();
    expect(tabs.find((t) => t.id === "coverage")?.badge).toBeUndefined();
    expect(tabs.find((t) => t.id === "decisions")).toBeUndefined();
  });

  it("carries the finding count and coverage percentage as figures", () => {
    const base = makeData();
    const tabs = fileTabsFor(
      makeData({
        health: { ...base.health, findings: [{}, {}, {}] as never },
        coverage: { line_coverage_pct: 61.4 } as never,
      }),
    );
    expect(tabs.find((t) => t.id === "health")?.badge).toBe(3);
    expect(tabs.find((t) => t.id === "coverage")?.badge).toBe("61%");
  });
});
