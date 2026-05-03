import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { GraphDocPanel } from "../../src/graph/graph-doc-panel.js";
import type { DocPage } from "@repowise-dev/types/docs";

const samplePage: DocPage = {
  id: "file_page:src/foo.ts",
  repository_id: "repo-1",
  page_type: "file_page",
  title: "foo.ts",
  content: "# Foo\n\nDocs body.",
  target_path: "src/foo.ts",
  source_hash: "abc",
  model_name: "claude-haiku-4-5",
  provider_name: "anthropic",
  input_tokens: 100,
  output_tokens: 50,
  cached_tokens: 0,
  generation_level: 1,
  version: 1,
  confidence: 0.85,
  freshness_status: "fresh",
  metadata: {},
  human_notes: null,
  created_at: "2026-04-01T00:00:00Z",
  updated_at: "2026-04-10T00:00:00Z",
};

describe("GraphDocPanel", () => {
  it("renders the page title and meta when a page is provided", () => {
    render(
      <GraphDocPanel
        nodeId="src/foo.ts"
        page={samplePage}
        isLoading={false}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("foo.ts")).toBeTruthy();
    expect(screen.getByText("claude-haiku-4-5")).toBeTruthy();
  });

  it("renders the empty state when an error is supplied", () => {
    render(
      <GraphDocPanel
        nodeId="src/missing.ts"
        page={null}
        isLoading={false}
        error={new Error("404")}
        browseDocsHref="/repos/r/docs"
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("No documentation found for this file.")).toBeTruthy();
    expect(screen.getByText("Browse all docs").getAttribute("href")).toBe("/repos/r/docs");
  });
});
