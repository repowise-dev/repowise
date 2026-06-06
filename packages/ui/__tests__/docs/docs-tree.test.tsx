import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DocsTree } from "../../src/docs/docs-tree.js";
import type { DocPage } from "@repowise-dev/types/docs";

function makePage(overrides: Partial<DocPage> = {}): DocPage {
  return {
    id: "p1",
    repository_id: "r1",
    page_type: "file_page",
    title: "Page",
    content: "",
    target_path: "src/foo.ts",
    source_hash: "h",
    model_name: "m",
    provider_name: "g",
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

describe("DocsTree", () => {
  it("renders page nodes from the supplied pages array", () => {
    render(
      <DocsTree
        pages={[
          makePage({ id: "1", target_path: "src/foo.ts", title: "foo.ts" }),
          makePage({ id: "2", target_path: "src/bar.ts", title: "bar.ts" }),
        ]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    expect(screen.getByText("foo.ts")).toBeInTheDocument();
    expect(screen.getByText("bar.ts")).toBeInTheDocument();
  });

  it("invokes onSelectPage when a leaf page is clicked", () => {
    const onSelectPage = vi.fn();
    const target = makePage({ id: "x", target_path: "x.ts", title: "x.ts" });
    render(
      <DocsTree
        pages={[target]}
        selectedPageId={null}
        onSelectPage={onSelectPage}
      />,
    );
    fireEvent.click(screen.getByText("x.ts"));
    expect(onSelectPage).toHaveBeenCalledWith(target);
  });

  it("claims deep files for their nearest module ancestor", () => {
    const mod = makePage({
      id: "module_page:core/ingestion",
      page_type: "module_page",
      title: "Module: core/ingestion",
      target_path: "core/ingestion",
    });
    const deepFile = makePage({
      id: "file_page:core/ingestion/resolvers/dotnet/index.py",
      page_type: "file_page",
      title: "index.py",
      target_path: "core/ingestion/resolvers/dotnet/index.py",
    });
    render(
      <DocsTree
        pages={[mod, deepFile]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    // Curated module name renders (path-shaped, not a raw graph id).
    const modNode = screen.getByText("core/ingestion");
    expect(modNode).toBeInTheDocument();
    // Expanding the module reveals the deep file, named relative to the
    // module dir so nested files stay unambiguous.
    fireEvent.click(modNode);
    expect(
      screen.getByText("resolvers/dotnet/index.py"),
    ).toBeInTheDocument();
  });

  it("prefers the deepest module when modules nest", () => {
    const outer = makePage({
      id: "module_page:core",
      page_type: "module_page",
      title: "Module: core",
      target_path: "core",
    });
    const inner = makePage({
      id: "module_page:core/ingestion",
      page_type: "module_page",
      title: "Module: core/ingestion",
      target_path: "core/ingestion",
    });
    const file = makePage({
      id: "file_page:core/ingestion/parser.py",
      page_type: "file_page",
      title: "parser.py",
      target_path: "core/ingestion/parser.py",
    });
    render(
      <DocsTree
        pages={[outer, inner, file]}
        selectedPageId={null}
        onSelectPage={() => {}}
      />,
    );
    fireEvent.click(screen.getByText("core/ingestion"));
    // File appears under the inner module (relative name), not the outer one.
    expect(screen.getByText("parser.py")).toBeInTheDocument();
  });
});
