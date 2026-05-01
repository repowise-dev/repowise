import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { FreshnessTable } from "../../src/coverage/freshness-table.js";
import type { DocPage } from "@repowise-dev/types/docs";

function makePage(overrides: Partial<DocPage> = {}): DocPage {
  return {
    id: "p1",
    repository_id: "r1",
    page_type: "module",
    title: "Page 1",
    content: "...",
    target_path: "src/foo.ts",
    source_hash: "h",
    model_name: "gemini-2.5",
    provider_name: "google",
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
    generation_level: 1,
    version: 1,
    confidence: 0.9,
    freshness_status: "fresh",
    metadata: {},
    human_notes: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("FreshnessTable", () => {
  it("renders one row per page", () => {
    render(
      <FreshnessTable
        pages={[
          makePage({ id: "1", target_path: "a.ts" }),
          makePage({ id: "2", target_path: "b.ts", freshness_status: "stale" }),
        ]}
      />,
    );
    expect(screen.getByText("a.ts")).toBeInTheDocument();
    expect(screen.getByText("b.ts")).toBeInTheDocument();
  });

  it("filters rows when a status tab is clicked", () => {
    render(
      <FreshnessTable
        pages={[
          makePage({ id: "1", target_path: "fresh.ts", freshness_status: "fresh" }),
          makePage({ id: "2", target_path: "stale.ts", freshness_status: "stale" }),
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /Stale/ }));
    expect(screen.queryByText("fresh.ts")).not.toBeInTheDocument();
    expect(screen.getByText("stale.ts")).toBeInTheDocument();
  });

  it("invokes onRegenerate with the page id when the button is clicked", async () => {
    const onRegenerate = vi.fn().mockResolvedValue(undefined);
    render(
      <FreshnessTable
        pages={[makePage({ id: "p1", target_path: "x.ts" })]}
        onRegenerate={onRegenerate}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Regenerate" }));
    await waitFor(() => expect(onRegenerate).toHaveBeenCalledWith("p1"));
  });

  it("hides the regenerate button when no callback is supplied", () => {
    render(<FreshnessTable pages={[makePage()]} />);
    expect(
      screen.queryByRole("button", { name: "Regenerate" }),
    ).not.toBeInTheDocument();
  });
});
