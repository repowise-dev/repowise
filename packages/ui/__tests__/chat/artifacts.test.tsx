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
            // Wire scores are 0–1 fractions (rank / total), not 0–100.
            { file_path: "src/hot.ts", churn_percentile: 0.99 },
          ],
          global_hotspots: [{ path: "src/other.ts", churn_percentile: 0.88 }],
        }}
      />,
    );
    expect(screen.getByText("src/hot.ts")).toBeInTheDocument();
    expect(screen.getByText("hotspot")).toBeInTheDocument();
    expect(screen.getByText("src/other.ts")).toBeInTheDocument();
    expect(screen.getByText(/99th pct/)).toBeInTheDocument();
    expect(screen.getByText("88th")).toBeInTheDocument();
  });

  it("RiskReportRenderer accepts MCP dict targets and hotspot_score", () => {
    render(
      <RiskReportRenderer
        data={{
          targets: {
            "src/auth.py": {
              target: "src/auth.py",
              // No is_hotspot on the MCP row — badge comes from score >= 0.75.
              hotspot_score: 0.91,
              risk_type: "churn-heavy",
              trend: "increasing",
            },
          },
          global_hotspots: [
            { file_path: "src/db.py", hotspot_score: 0.85 },
          ],
        }}
      />,
    );
    expect(screen.getByText("src/auth.py")).toBeInTheDocument();
    expect(screen.getByText("hotspot")).toBeInTheDocument();
    expect(screen.getByText("src/db.py")).toBeInTheDocument();
    expect(screen.getByText(/91th pct/)).toBeInTheDocument();
    expect(screen.getByText("85th")).toBeInTheDocument();
  });

  it("RiskReportRenderer renders get_change_risk score cards", () => {
    render(
      <RiskReportRenderer
        data={{
          ref: "HEAD",
          score: 7.2,
          risk_percentile: 82,
          review_priority: "Elevated",
          classification: "Above typical recent changes.",
        }}
      />,
    );
    expect(screen.getByText("HEAD")).toBeInTheDocument();
    expect(screen.getByText("Elevated")).toBeInTheDocument();
    expect(screen.getByText("p82")).toBeInTheDocument();
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

  it("DecisionsRenderer health mode reads MCP counts and stale rows", () => {
    render(
      <DecisionsRenderer
        data={{
          mode: "health",
          summary: "12 active · 2 stale · 1 proposed · 3 ungoverned hotspots",
          counts: { active: 12, stale: 2, proposed: 1 },
          stale_decisions: [
            {
              title: "Prefer SSE",
              affected_files: ["packages/server/src/chat.py"],
            },
          ],
          proposed_awaiting_review: [{ title: "Drop Redis", source: "commit" }],
          ungoverned_hotspots: ["src/hot.ts"],
        }}
      />,
    );
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("Prefer SSE")).toBeInTheDocument();
    expect(screen.getByText("Drop Redis")).toBeInTheDocument();
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

  it("DecisionsRenderer search mode reads MCP decisions array", () => {
    render(
      <DecisionsRenderer
        data={{
          mode: "search",
          query: "auth",
          decisions: [
            {
              title: "JWT sessions",
              decision: "Use signed cookies.",
              affected_files: ["packages/server/auth.py"],
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("JWT sessions")).toBeInTheDocument();
    expect(screen.getByText("packages/server/auth.py")).toBeInTheDocument();
  });

  it("DecisionsRenderer path mode shows governing decisions", () => {
    render(
      <DecisionsRenderer
        data={{
          mode: "path",
          path: "packages/ui/src/chat/artifacts.tsx",
          decisions: [
            {
              title: "Typed chat artifacts",
              status: "active",
              decision: "One renderer per tool.",
            },
          ],
        }}
      />,
    );
    expect(screen.getByText("Typed chat artifacts")).toBeInTheDocument();
    expect(
      screen.getByText((_, el) => el?.textContent === '"packages/ui/src/chat/artifacts.tsx"'),
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
