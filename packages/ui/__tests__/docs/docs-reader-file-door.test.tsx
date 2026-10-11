import { describe, it, expect, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import { DocsReader } from "../../src/docs/docs-reader.js";
import type { DocPage } from "@repowise-dev/types/docs";

/**
 * The way out of a file's documentation and into the file.
 *
 * The docs surface knows exactly which file a page is about — `target_path` on
 * a `file_page` is the repo-relative path the file route takes, and it is the
 * same pair the file endpoint matches on — and yet every href it rendered was a
 * `?page=` wiki link. A reader who finished a file's page and wanted its
 * health, history or dependents had to go back to the tree and start again
 * from the Files index.
 *
 * The door is conditional on three things at once, and each of them is a way
 * to render a link that goes somewhere wrong: the page must be a `file_page`
 * (a module page's `target_path` is a directory), it must carry a path, and
 * the host must have a file route to send it to. A host without one — the VS
 * Code webview — renders no door rather than a broken one.
 */

const DOOR = /Open file page/i;

beforeAll(() => {
  Element.prototype.scrollTo = () => {};
});

function makePage(overrides: Partial<DocPage> = {}): DocPage {
  return {
    id: "file_page:packages/ui/src/files/file-page.tsx",
    repository_id: "r1",
    page_type: "file_page",
    title: "file-page.tsx",
    content: "The shell for the file entity page.",
    target_path: "packages/ui/src/files/file-page.tsx",
    source_hash: "h",
    model_name: "m",
    provider_name: "anthropic",
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
    generation_level: 3,
    version: 1,
    confidence: 1,
    freshness_status: "fresh",
    metadata: {},
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  } as DocPage;
}

function renderReader(
  page: DocPage,
  buildFileHref?: (filePath: string) => string,
) {
  return render(
    <DocsReader
      page={page}
      repoId="r1"
      persona="contributor"
      sidebarOpen={false}
      buildPageHref={(id) => `?page=${id}`}
      buildFileHref={buildFileHref}
      LinkComponent={({ href, children, ...rest }) => (
        <a href={href} {...rest}>
          {children}
        </a>
      )}
    />,
  );
}

describe("DocsReader file-page door", () => {
  it("links a file page at the path the file route takes", () => {
    renderReader(makePage(), (p) => `/repos/r1/files/${p}`);
    const link = screen.getByRole("link", { name: DOOR });
    expect(link.getAttribute("href")).toBe(
      "/repos/r1/files/packages/ui/src/files/file-page.tsx",
    );
  });

  it("renders no door on a module page", () => {
    // `target_path` there is a directory, so the file route would 404 on it.
    renderReader(
      makePage({
        page_type: "module_page",
        target_path: "packages/ui/src/files",
      }),
      (p) => `/repos/r1/files/${p}`,
    );
    expect(screen.queryByRole("link", { name: DOOR })).toBeNull();
  });

  it("renders no door for a host with no file route", () => {
    renderReader(makePage());
    expect(screen.queryByRole("link", { name: DOOR })).toBeNull();
  });
});

describe("DocsReader missing page", () => {
  /**
   * Links into a specific `?page=` arrive from all over the app — the hotspot
   * and symbol row actions, the file card, the command palette, bookmarks —
   * and page selection is budgeted, so most files have no page. The reader
   * used to answer all of those with "Select a page. Choose a file or module
   * from the tree", which is the prompt for someone who asked for nothing, and
   * reads as the link having done nothing at all.
   */
  it("names the page that is missing", () => {
    render(
      <DocsReader
        page={null}
        repoId="r1"
        persona="contributor"
        sidebarOpen={false}
        buildPageHref={(id) => `?page=${id}`}
        missingPageId="file_page:packages/core/src/resolvers/dotnet.py"
        LinkComponent={({ href, children }) => <a href={href}>{children}</a>}
      />,
    );
    // The path, not the id: a `file_page:` prefix on screen is machinery.
    expect(
      screen.getByText("packages/core/src/resolvers/dotnet.py"),
    ).toBeTruthy();
    expect(screen.queryByText("Select a page")).toBeNull();
  });

  it("falls back to the pick-something prompt when nothing was asked for", () => {
    render(
      <DocsReader
        page={null}
        repoId="r1"
        persona="contributor"
        sidebarOpen={false}
        buildPageHref={(id) => `?page=${id}`}
        LinkComponent={({ href, children }) => <a href={href}>{children}</a>}
      />,
    );
    expect(screen.getByText("Select a page")).toBeTruthy();
  });
});
