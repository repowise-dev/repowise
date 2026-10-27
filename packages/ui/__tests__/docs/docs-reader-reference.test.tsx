import { describe, it, expect, beforeAll } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { DocsReader } from "../../src/docs/docs-reader.js";
import type { DocPage } from "@repowise-dev/types/docs";

/**
 * A page's agent digest is one click away, never in the reading path.
 *
 * The body is what a reader came for. The questions, identifiers and git
 * signals written for search and agents sit behind a Reference tab, which
 * exists only on pages that carry a digest. Git signals also show as a small
 * History card beside the page.
 */

beforeAll(() => {
  Element.prototype.scrollTo = () => {};
});

function makePage(overrides: Partial<DocPage> = {}): DocPage {
  return {
    id: "module_page:pkg/parser",
    repository_id: "r1",
    page_type: "module_page",
    title: "Parsing",
    content: "The parser turns files into records.",
    target_path: "pkg/parser",
    source_hash: "h",
    model_name: "m",
    provider_name: "openai",
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
    generation_level: 4,
    version: 1,
    confidence: 1,
    freshness_status: "fresh",
    metadata: {},
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  } as DocPage;
}

function renderReader(page: DocPage) {
  return render(
    <DocsReader
      page={page}
      repoId="r1"
      persona="contributor"
      sidebarOpen={false}
      buildPageHref={(id) => `?page=${id}`}
      LinkComponent={({ href, children, ...rest }) => (
        <a href={href} {...rest}>
          {children}
        </a>
      )}
    />,
  );
}

const DIGEST = "## Questions this page answers\n\n- How is a file parsed?";

describe("DocsReader reference view", () => {
  it("shows the body first and the digest behind the Reference tab", () => {
    renderReader(makePage({ digest: DIGEST }));

    expect(screen.getByText("The parser turns files into records.")).toBeTruthy();
    expect(screen.queryByText("How is a file parsed?")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Reference" }));

    expect(screen.getByText("How is a file parsed?")).toBeTruthy();
    expect(screen.queryByText("The parser turns files into records.")).toBeNull();
  });

  it("offers no tabs on a page without a digest", () => {
    renderReader(makePage());

    expect(screen.queryByRole("tab")).toBeNull();
  });

  it("summarises the module's git history beside the page", () => {
    renderReader(
      makePage({
        metadata: {
          module_signals: { files: 12, hotspots: 2, owners: [{ name: "Ada", files: 9 }] },
        },
      }),
    );

    expect(screen.getByText("Ada · 9 of 12 files")).toBeTruthy();
    expect(screen.getAllByText("2 of 12 files").length).toBeGreaterThan(0);
  });
});
