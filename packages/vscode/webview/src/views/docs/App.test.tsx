import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
  window.matchMedia ??= ((query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
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

/** The listing the panel asks for: every page without its body. */
function summaries(pages: DocPage[]) {
  return pages.map(({ content: _content, metadata: _metadata, ...rest }) => rest);
}

function mockHost(pages: DocPage[]) {
  const api = {
    pageSummaries: vi.fn().mockResolvedValue(summaries(pages)),
    pageById: vi.fn((id: string) => {
      const page = pages.find((p) => p.id === id);
      return page ? Promise.resolve(page) : Promise.reject(new Error("Not found"));
    }),
    fileDetail: vi.fn().mockRejectedValue(new Error("Not found")),
    searchPages: vi.fn().mockResolvedValue([]),
  };
  const host: WebviewHost = {
    api: api as unknown as WebviewHost["api"],
    onInit: () => () => {},
    onRefresh: () => () => {},
    onUpdateDone: () => () => {},
    onThemeChanged: () => () => {},
    ready: () => {},
    openFile: vi.fn(),
    copyText: vi.fn(),
    openExternal: vi.fn(),
    openView: vi.fn(),
    focusHome: vi.fn(),
    openNativeSettings: vi.fn(),
    updateIndex: vi.fn(),
    setTheme: vi.fn(),
  };
  return { host, api };
}

const REPO = { id: "repo-1", name: "demo", headCommit: null, defaultBranch: null };

describe("docs App", () => {
  it("lists summaries and fetches only the page it shows", async () => {
    const { host, api } = mockHost(PAGES);
    render(<App host={host} repo={REPO} params={{}} refreshToken={0} />);

    // Domain view (the default) files every deterministic page into the
    // collapsed "Reference" folder, so the folder is what renders on load.
    expect(await screen.findByText("Reference")).toBeTruthy();

    // With no params, the reader lands on the repo overview page.
    expect(
      await screen.findByText("Overview intro paragraph rendered by the reader."),
    ).toBeTruthy();
    expect(api.pageSummaries).toHaveBeenCalledTimes(1);
    expect(api.pageById).toHaveBeenCalledTimes(1);
    expect(api.pageById).toHaveBeenCalledWith("ov");
    // The web explorer's header bar: one tab and the search button.
    expect(screen.getByText("Explorer")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Search" })).toBeTruthy();
  });

  it("opens the page for a file path and links the file into the editor", async () => {
    const { host, api } = mockHost(PAGES);
    render(<App host={host} repo={REPO} params={{ filePath: "src/app.ts" }} refreshToken={0} />);

    expect(await screen.findByText("The application entry point.")).toBeTruthy();
    expect(api.pageById).toHaveBeenCalledWith("fp");

    // The reader's door to the source file opens it in an editor column.
    const door = document.querySelector('a[href^="repowise-open:"]');
    expect(door).toBeTruthy();
    fireEvent.click(door!);
    expect(host.openFile).toHaveBeenCalledWith("src/app.ts", undefined);
  });

  it("opens an asked-for page id without waiting for the list", async () => {
    const { host, api } = mockHost(PAGES);
    let release: (value: unknown) => void = () => {};
    api.pageSummaries.mockReturnValue(new Promise((r) => (release = r)));
    render(<App host={host} repo={REPO} params={{ pageId: "fp" }} refreshToken={0} />);

    expect(await screen.findByText("The application entry point.")).toBeTruthy();
    release(summaries(PAGES));
    await waitFor(() => expect(screen.getByText("Reference")).toBeTruthy());
  });

  it("keeps an asked-for page when the listing fails", async () => {
    const { host, api } = mockHost(PAGES);
    api.pageSummaries.mockRejectedValue(new Error("listing timed out"));
    render(<App host={host} repo={REPO} params={{ pageId: "fp" }} refreshToken={0} />);

    expect(await screen.findByText("The application entry point.")).toBeTruthy();
    expect(await screen.findByText("listing timed out")).toBeTruthy();
  });
});
