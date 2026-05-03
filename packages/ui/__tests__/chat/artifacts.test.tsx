import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  ContextRenderer,
  DeadCodeRenderer,
  DecisionsRenderer,
  DiagramRenderer,
  GenericJsonRenderer,
  GraphPathRenderer,
  OverviewRenderer,
  RiskReportRenderer,
  SearchResultsRenderer,
} from "../../src/chat/artifacts.js";

// Mermaid pulls in DOM measuring APIs jsdom doesn't implement; the renderer is
// covered by smoke-asserting only the description fallback path.
describe("chat artifact renderers", () => {
  it("OverviewRenderer surfaces top-line stats", () => {
    render(
      <OverviewRenderer
        data={{
          total_files: 42,
          total_symbols: 1337,
          languages: { TypeScript: 30, Python: 12 },
          modules: ["packages/web", "packages/ui"],
          entry_points: ["packages/web/src/app/page.tsx"],
          hotspot_count: 5,
          is_monorepo: true,
        }}
      />,
    );
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("1,337")).toBeInTheDocument();
    expect(screen.getByText("TypeScript")).toBeInTheDocument();
    expect(screen.getByText("packages/web")).toBeInTheDocument();
  });

  it("ContextRenderer shows target paths with markdown when present", () => {
    render(
      <ContextRenderer
        data={{
          targets: {
            "packages/ui/src/chat/artifacts.tsx": {
              docs: { content_md: "# Hello\n\nA short doc." },
            },
          },
        }}
      />,
    );
    expect(
      screen.getByText("packages/ui/src/chat/artifacts.tsx"),
    ).toBeInTheDocument();
    expect(screen.getByText("Hello")).toBeInTheDocument();
  });

  it("RiskReportRenderer flags hotspots", () => {
    render(
      <RiskReportRenderer
        data={{
          targets: [
            { file_path: "src/hot.ts", churn_percentile: 99, is_hotspot: true },
          ],
          global_hotspots: [{ path: "src/other.ts", churn_percentile: 88 }],
        }}
      />,
    );
    expect(screen.getByText("src/hot.ts")).toBeInTheDocument();
    expect(screen.getByText("hotspot")).toBeInTheDocument();
    expect(screen.getByText("src/other.ts")).toBeInTheDocument();
  });

  it("SearchResultsRenderer lists results with snippets", () => {
    render(
      <SearchResultsRenderer
        data={{
          query: "auth flow",
          results: [
            {
              title: "Authentication Overview",
              page_type: "module_page",
              snippet: "JWT-based auth pipeline.",
              relevance_score: 0.91,
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("auth flow")).toBeInTheDocument();
    expect(screen.getByText("Authentication Overview")).toBeInTheDocument();
    expect(screen.getByText("JWT-based auth pipeline.")).toBeInTheDocument();
  });

  it("SearchResultsRenderer handles empty results", () => {
    render(<SearchResultsRenderer data={{ query: "nope", results: [] }} />);
    expect(screen.getByText("No results found.")).toBeInTheDocument();
  });

  it("GraphPathRenderer renders the explanation and ordered path", () => {
    render(
      <GraphPathRenderer
        data={{
          path: ["a.ts", "b.ts", "c.ts"],
          distance: 2,
          explanation: "Path from a.ts to c.ts via 2 hop(s).",
        }}
      />,
    );
    expect(
      screen.getByText("Path from a.ts to c.ts via 2 hop(s)."),
    ).toBeInTheDocument();
    expect(screen.getByText("a.ts")).toBeInTheDocument();
    expect(screen.getByText("b.ts")).toBeInTheDocument();
    expect(screen.getByText("c.ts")).toBeInTheDocument();
  });

  it("DecisionsRenderer health mode shows totals + by_source", () => {
    render(
      <DecisionsRenderer
        data={{
          mode: "health",
          total_decisions: 17,
          by_source: { adr: 12, commit: 5 },
          decisions: [{ title: "Use SSE for chat", status: "accepted" }],
        }}
      />,
    );
    expect(screen.getByText("17")).toBeInTheDocument();
    expect(screen.getByText("adr")).toBeInTheDocument();
    expect(screen.getByText("Use SSE for chat")).toBeInTheDocument();
  });

  it("DecisionsRenderer search mode renders matches with affected files", () => {
    render(
      <DecisionsRenderer
        data={{
          mode: "search",
          query: "cache",
          results: [
            {
              title: "LRU artifact cache",
              decision: "Use LRU.",
              rationale: "Bounded memory.",
              affected_files: ["app/services/artifact_service.py"],
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("LRU artifact cache")).toBeInTheDocument();
    expect(
      screen.getByText("app/services/artifact_service.py"),
    ).toBeInTheDocument();
  });

  it("DeadCodeRenderer separates high and medium confidence", () => {
    render(
      <DeadCodeRenderer
        data={{
          total_findings: 3,
          deletable_lines: 88,
          high_confidence: [
            {
              file_path: "src/legacy.ts",
              symbol_name: "oldFn",
              kind: "unused_export",
              confidence: 0.95,
              reason: "No callers found.",
              lines: 30,
              safe_to_delete: true,
            },
          ],
          medium_confidence: [
            {
              file_path: "src/maybe.ts",
              kind: "unreachable_file",
              confidence: 0.6,
              reason: "Possibly imported via dynamic require.",
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("88")).toBeInTheDocument();
    expect(screen.getByText(/src\/legacy\.ts::oldFn/)).toBeInTheDocument();
    expect(screen.getByText("src/maybe.ts")).toBeInTheDocument();
    expect(screen.getByText("95%")).toBeInTheDocument();
  });

  it("DiagramRenderer falls back gracefully when no syntax", () => {
    render(<DiagramRenderer data={{ diagram_type: "flowchart", mermaid_syntax: "" }} />);
    expect(screen.getByText("No diagram available.")).toBeInTheDocument();
  });

  it("GenericJsonRenderer pretty-prints data", () => {
    render(<GenericJsonRenderer data={{ foo: "bar" }} />);
    expect(screen.getByText(/"foo": "bar"/)).toBeInTheDocument();
  });
});
