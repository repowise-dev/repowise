import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { DocPage } from "@repowise-dev/types/docs";
import type { WebviewHost } from "../../runtime/rpc";
import { App } from "./App";

// jsdom omits a couple of browser APIs the shared reader touches (the table of
// contents observes headings; the reader scrolls to top on page change). Stub
// them so mounting DocsReader doesn't throw.
beforeAll(() => {
  Element.prototype.scrollTo = vi.fn();
  class NoopObserver {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): [] {
      return [];
    }
  }
  (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = NoopObserver;
});

afterEach(cleanup);

function makePage(overrides: Partial<DocPage>): DocPage {
  return {
    id: "id",
    repository_id: "repo-1",
    page_type: "file_page",
    title: "Untitled",
    content: "",
    target_path: "",
    source_hash: "hash",
    model_name: "test-model",
    provider_name: "test",
    input_tokens: 100,
    output_tokens: 200,
    cached_tokens: 0,
    generation_level: 0,
    version: 1,
    confidence: 1,
    freshness_status: "fresh",
    metadata: {},
    human_notes: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

const PAGES: DocPage[] = [
  makePage({
    id: "ov",
    page_type: "repo_overview",
    title: "Project Overview",
    target_path: "",
    content: "# Project Overview\n\nOverview intro paragraph rendered by the reader.",
  }),
  makePage({
    id: "fp",
    page_type: "file_page",
    title: "app.ts",
    target_path: "src/app.ts",
    content: "# app.ts\n\nThe application entry point.",
  }),
];

function mockHost(pages: DocPage[]): WebviewHost {
  return {
    api: {
      pagesList: vi.fn().mockResolvedValue(pages),
      fileDetail: vi.fn(),
    } as unknown as WebviewHost["api"],
    onInit: () => () => {},
    onRefresh: () => () => {},
    onUpdateDone: () => () => {},
    onThemeChanged: () => () => {},
    ready: () => {},
    openFile: vi.fn(),
    copyText: vi.fn(),
    openExternal: vi.fn(),
    openView: vi.fn(),
    updateIndex: vi.fn(),
    setTheme: vi.fn(),
  };
}

describe("docs App", () => {
  it("renders the page tree and the selected page's markdown", async () => {
    const host = mockHost(PAGES);
    render(
      <App
        host={host}
        repo={{ id: "repo-1", name: "demo", headCommit: null, defaultBranch: null }}
        params={{}}
        refreshToken={0}
      />,
    );

    // Tree lists both pages (file leaf shows its basename).
    expect(await screen.findByText("app.ts")).toBeTruthy();

    // With no params, the reader lands on the repo overview page.
    expect(
      await screen.findByText("Overview intro paragraph rendered by the reader."),
    ).toBeTruthy();
  });
});
