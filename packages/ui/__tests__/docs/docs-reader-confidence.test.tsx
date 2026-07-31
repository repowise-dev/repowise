import { describe, it, expect, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import { DocsReader } from "../../src/docs/docs-reader.js";
import type { DocPage } from "@repowise-dev/types/docs";

/**
 * The low-confidence warning had never rendered for anyone.
 *
 * Generation wrote a constant 1.0 into every page's `confidence`, so the
 * condition guarding this banner could not be true on any real wiki. It was
 * shipped, styled, and unreachable — and untested, because a test would have
 * had to construct the value no page ever had.
 *
 * Generation now writes three values, and the lowest of them — a structural
 * stub standing in for a page a model was meant to write — falls under this
 * threshold. These assert the banner at values on both sides of it.
 */

const WARNING = /generated with low confidence/i;

// The reader scrolls its pane back to the top on every page change, and jsdom
// implements no scrolling. Stubbed here rather than in the shared setup: it is
// this component's requirement, not the suite's.
beforeAll(() => {
  Element.prototype.scrollTo = () => {};
});

function makePage(overrides: Partial<DocPage> = {}): DocPage {
  return {
    id: "p1",
    repository_id: "r1",
    page_type: "module_page",
    title: "Resolution Layer",
    content: "The layer turns references into edges.",
    target_path: "core/resolvers",
    source_hash: "h",
    model_name: "m",
    provider_name: "template",
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

describe("DocsReader low-confidence warning", () => {
  it("warns on a page written with low confidence", () => {
    // 0.3 is what generation stamps on a structural stub: real material with
    // the prose missing, standing in for a page a model never wrote.
    renderReader(makePage({ confidence: 0.3 }));

    expect(screen.getByText(WARNING)).toBeTruthy();
  });

  it("stays quiet on a model-written page", () => {
    // 0.8. A model page is grounded and checked — normal to read, not suspect.
    renderReader(makePage({ confidence: 0.8 }));

    expect(screen.queryByText(WARNING)).toBeNull();
  });

  it("stays quiet on a template-rendered page", () => {
    renderReader(makePage({ confidence: 1 }));

    expect(screen.queryByText(WARNING)).toBeNull();
  });

  it("stays quiet when confidence is absent rather than low", () => {
    // Zero is the "nobody measured this" value the column carries before a
    // page has been through generation. Warning on it would put the flag on
    // every page of an older wiki, which is the opposite of a trust signal.
    renderReader(makePage({ confidence: 0 }));

    expect(screen.queryByText(WARNING)).toBeNull();
  });
});
