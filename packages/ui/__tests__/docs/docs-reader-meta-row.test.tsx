import { describe, it, expect, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import { DocsReader } from "../../src/docs/docs-reader.js";
import type { DocPage } from "@repowise-dev/types/docs";

/**
 * The metadata row under a page title carries the page's own model action and
 * its freshness. Fresh is the quiet default and draws nothing; only a page
 * that fell behind its code gets a mark, and that mark leads to doc drift.
 */

beforeAll(() => {
  Element.prototype.scrollTo = () => {};
});

function makePage(overrides: Partial<DocPage> = {}): DocPage {
  return {
    id: "module_page:src/core",
    repository_id: "r1",
    page_type: "module_page",
    title: "Core",
    content: "The core module.",
    target_path: "src/core",
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

function renderReader(page: DocPage, extra: Partial<Parameters<typeof DocsReader>[0]> = {}) {
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
      {...extra}
    />,
  );
}

/** The row directly under the h1. */
function metaRow() {
  return screen.getByRole("heading", { level: 1 }).nextElementSibling as HTMLElement;
}

describe("DocsReader metadata row", () => {
  it("renders the page action in the row under the title", () => {
    renderReader(makePage(), {
      pageActionSlot: <button>Rewrite with AI</button>,
    });
    expect(metaRow()).toContainElement(screen.getByRole("button", { name: "Rewrite with AI" }));
  });

  it("draws no freshness mark on a fresh page", () => {
    renderReader(makePage(), { freshnessHref: "/drift" });
    expect(metaRow().textContent).not.toMatch(/fresh|out of date/i);
    expect(screen.queryByRole("link", { name: /out of date/i })).toBeNull();
  });

  it("marks a stale page and links the mark to doc drift", () => {
    renderReader(makePage({ freshness_status: "stale" }), { freshnessHref: "/drift" });
    const mark = screen.getByRole("link", { name: "May be out of date" });
    expect(mark).toHaveAttribute("href", "/drift");
    expect(metaRow()).toContainElement(mark);
  });

  it("takes the host's wording and stays plain text without an href", () => {
    renderReader(makePage({ freshness_status: "outdated" }), {
      freshnessLabels: { outdated: "Behind its code" },
    });
    expect(metaRow().textContent).toContain("Behind its code");
    expect(screen.queryByRole("link", { name: "Behind its code" })).toBeNull();
  });
});
