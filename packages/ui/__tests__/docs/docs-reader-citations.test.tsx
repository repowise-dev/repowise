import { describe, it, expect, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import { DocsReader } from "../../src/docs/docs-reader.js";
import type { DocPage } from "@repowise-dev/types/docs";

/** Citations open the cited lines: at the forge when there is one, else the file view. */

beforeAll(() => {
  Element.prototype.scrollTo = () => {};
});

function page(sources: unknown[]): DocPage {
  return {
    id: "module_page:cli",
    repository_id: "r1",
    page_type: "module_page",
    title: "CLI",
    content: "Body.",
    target_path: "cli",
    source_hash: "h",
    model_name: "m",
    provider_name: "openai",
    input_tokens: 0,
    output_tokens: 0,
    cached_tokens: 0,
    generation_level: 4,
    version: 1,
    confidence: 0.8,
    freshness_status: "fresh",
    metadata: { sources, sources_commit: "0123456789abcdef" },
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  } as unknown as DocPage;
}

function renderReader(p: DocPage, pages: DocPage[] = []) {
  return render(
    <DocsReader
      page={p}
      pages={pages}
      repoId="r1"
      persona="contributor"
      sidebarOpen={false}
      buildPageHref={(id) => `?page=${id}`}
      buildFileHref={(f) => `/repos/r1/files/${f}`}
      LinkComponent={({ href, children, ...rest }) => (
        <a href={href} {...rest}>
          {children}
        </a>
      )}
    />,
  );
}

describe("DocsReader citations", () => {
  it("links a span to the forge at the generation commit", () => {
    const url = "https://github.com/o/r/blob/0123456789abcdef/cli/run.py#L10-L40";
    renderReader(page([{ path: "cli/run.py", kind: "cited", start_line: 10, end_line: 40, url }]));
    const link = screen.getByRole("link", { name: "cli/run.py:10-40" });
    expect(link.getAttribute("href")).toBe(url);
    expect(link.getAttribute("title")).toContain("@ 0123456");
  });

  it("falls back to the file view when the repo has no known remote", () => {
    renderReader(page([{ path: "cli/run.py", kind: "cited", start_line: 10, end_line: 40 }]));
    const link = screen.getByRole("link", { name: "cli/run.py:10-40" });
    expect(link.getAttribute("href")).toBe("/repos/r1/files/cli/run.py");
  });
});

describe("DocsReader whole-file citations", () => {
  it("keeps opening the wiki page of a cited file that has one", () => {
    const p = page([
      { path: "cli/run.py", kind: "dependency", url: "https://github.com/o/r/blob/x/cli/run.py" },
    ]);
    renderReader(p, [{ ...p, id: "file_page:cli/run.py", page_type: "file_page", target_path: "cli/run.py" }]);
    expect(screen.getByRole("button", { name: "cli/run.py" })).toBeTruthy();
  });
});
