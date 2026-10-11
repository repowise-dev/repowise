import { describe, it, expect, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import { DocsReader } from "../../src/docs/docs-reader.js";
import type { DocPage } from "@repowise-dev/types/docs";

/**
 * What the reader says about a page with no prose on it, and to whom.
 *
 * This used to be a bordered warning above the content ("generated with low
 * confidence, verify against the source") gated on `confidence < 0.5`.
 * Generation stamped that value on every deterministic page, not only on the
 * ones where a provider call had failed, so an index built without a key
 * opened every page it had under a trust warning about pages assembled
 * entirely from the parse, the import graph and git history. The banner was
 * loudest exactly where it was least true.
 *
 * Two things changed. Generation now reserves the low value for a page whose
 * provider call actually failed, and the reader keys on the marker that
 * records that failure rather than on the number, so an already-published
 * wiki reads correctly without being reindexed. What is left is one quiet line
 * at the end of the content, beside the affordance that fixes it.
 */

const OLD_BANNER = /generated with low confidence/i;
const PROSE_LOST = /did not complete/i;
const CAN_WRITE = /a model can write/i;

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

function renderReader(page: DocPage, opts: { upgradeSlot?: boolean } = {}) {
  const { upgradeSlot = true } = opts;
  return render(
    <DocsReader
      page={page}
      repoId="r1"
      persona="contributor"
      sidebarOpen={false}
      buildPageHref={(id) => `?page=${id}`}
      upgradeSlot={upgradeSlot ? <button>Write with AI</button> : undefined}
      LinkComponent={({ href, children, ...rest }) => (
        <a href={href} {...rest}>
          {children}
        </a>
      )}
    />,
  );
}

describe("DocsReader prose-lost notice", () => {
  it("says nothing alarming on a deterministic page", () => {
    // The case that made this a bug: a keyless run's module page. It offers to
    // have prose written and makes no claim about trustworthiness.
    renderReader(makePage());

    expect(screen.queryByText(OLD_BANNER)).toBeNull();
    expect(screen.queryByText(PROSE_LOST)).toBeNull();
    expect(screen.getByText(CAN_WRITE)).toBeTruthy();
  });

  it("stays quiet on an already-published page still carrying the old 0.3", () => {
    // No reindex required. Wikis published before the split have keyless stubs
    // stamped 0.3 with no failure marker; keying on the marker is what lets
    // those read correctly today rather than after a rebuild.
    renderReader(makePage({ confidence: 0.3 }));

    expect(screen.queryByText(OLD_BANNER)).toBeNull();
    expect(screen.queryByText(PROSE_LOST)).toBeNull();
    // Asserted positively too: without this the test would also pass if the
    // reader rendered nothing at all, which is a different bug.
    expect(screen.getByText(CAN_WRITE)).toBeTruthy();
  });

  it("names the lost prose when a provider call actually failed", () => {
    renderReader(
      makePage({
        confidence: 0.3,
        metadata: { stub_fallback_error: "upstream 529 overloaded" },
      }),
    );

    expect(screen.getByText(PROSE_LOST)).toBeTruthy();
    // One line, not two: the caveat replaces the generic offer rather than
    // stacking a second block on top of it.
    expect(screen.queryByText(CAN_WRITE)).toBeNull();
  });

  it("says nothing at all on a model-written page", () => {
    renderReader(makePage({ confidence: 0.8, provider_name: "anthropic" }));

    expect(screen.queryByText(OLD_BANNER)).toBeNull();
    expect(screen.queryByText(PROSE_LOST)).toBeNull();
    expect(screen.queryByText(CAN_WRITE)).toBeNull();
  });

  it("still names the lost prose with no upgrade affordance to offer", () => {
    // The VS Code webview mounts the reader without an `upgradeSlot`. The
    // caveat is a statement about the page, not a label on a button, so it has
    // to survive on its own. This is the arm the web app never exercises.
    renderReader(
      makePage({
        confidence: 0.3,
        metadata: { stub_fallback_error: "upstream 529 overloaded" },
      }),
      { upgradeSlot: false },
    );

    expect(screen.getByText(PROSE_LOST)).toBeTruthy();
  });

  it("ignores a metadata shape that is not an object", () => {
    // Artifacts have carried `metadata` as null and, on older snapshots, as a
    // non-object. Neither may throw, and neither is a failure record.
    renderReader(makePage({ metadata: null as never }));
    expect(screen.queryByText(PROSE_LOST)).toBeNull();

    renderReader(makePage({ metadata: "oops" as never }));
    expect(screen.queryByText(PROSE_LOST)).toBeNull();
  });

  it("ignores an empty error string", () => {
    // The marker is the error text. An empty one records nothing, and reading
    // it as a failure would put the caveat on a page with no failure behind it.
    renderReader(makePage({ metadata: { stub_fallback_error: "" } }));

    expect(screen.queryByText(PROSE_LOST)).toBeNull();
    expect(screen.getByText(CAN_WRITE)).toBeTruthy();
  });
});
