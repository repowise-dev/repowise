import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, fireEvent } from "@testing-library/react";
import { DocsCommandPalette } from "../../src/docs/command-palette.js";
import type { DocPage, DocPageSummary } from "@repowise-dev/types/docs";

afterEach(cleanup);

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

const PAGES: DocPageSummary[] = [
  summary({ id: "auth", title: "Authentication", target_path: "src/auth.ts" }),
  summary({ id: "db", title: "Database", target_path: "src/db.ts" }),
];

describe("DocsCommandPalette", () => {
  it("matches titles and paths without any page bodies loaded", async () => {
    render(
      <DocsCommandPalette pages={PAGES} open onSelect={() => {}} />,
    );

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "auth" } });

    await waitFor(() =>
      expect(screen.getByText("Authentication")).toBeInTheDocument(),
    );
    expect(screen.queryByText("Database")).not.toBeInTheDocument();
  });

  it("gets body matches from searchFn, with the server's snippet", async () => {
    const searchFn = vi.fn(async () => [
      { page: PAGES[1]!, snippet: "…rotates the connection pool…" },
    ]);
    render(
      <DocsCommandPalette
        pages={PAGES}
        open
        onSelect={() => {}}
        searchFn={searchFn}
      />,
    );

    // "pool" appears in no title and no path — only in a body the list does
    // not carry, so this hit can only come from the server.
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "pool" } });

    await waitFor(() =>
      expect(screen.getByText("…rotates the connection pool…")).toBeInTheDocument(),
    );
    expect(searchFn).toHaveBeenCalledWith("pool");
  });

  it("still matches bodies locally when a row was loaded with one", async () => {
    const hydrated: DocPage = {
      ...summary({ id: "cfg", title: "Config", target_path: "src/cfg.ts" }),
      content: "The retry budget is fixed at three attempts.",
      metadata: {},
    };
    render(
      <DocsCommandPalette
        pages={[...PAGES, hydrated]}
        open
        onSelect={() => {}}
      />,
    );

    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "retry budget" },
    });

    await waitFor(() => expect(screen.getByText("Config")).toBeInTheDocument());
  });
});
