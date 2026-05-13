import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { SymbolTable, type SymbolFilters } from "../../src/symbols/symbol-table.js";
import type { CodeSymbol } from "@repowise-dev/types/symbols";

const sym = (overrides: Partial<CodeSymbol> = {}): CodeSymbol => ({
  id: "s1",
  repository_id: "r",
  file_path: "src/foo.ts",
  symbol_id: "src/foo.ts::bar",
  name: "bar",
  qualified_name: "foo.bar",
  kind: "function",
  signature: "function bar()",
  start_line: 10,
  end_line: 15,
  docstring: null,
  visibility: "public",
  is_async: false,
  complexity_estimate: 3,
  language: "typescript",
  parent_name: null,
  importance_score: 0.9,
  ...overrides,
});

const defaultFilters: SymbolFilters = {
  q: "",
  kind: "all",
  language: "all",
  visibility: "all",
  inHotFiles: false,
  inEntryPoints: false,
  sort: "importance",
};

describe("SymbolTable", () => {
  it("renders the empty state when no items", () => {
    render(
      <SymbolTable
        items={[]}
        isLoading={false}
        isValidating={false}
        hasMore={false}
        total={0}
        filters={defaultFilters}
        onFiltersChange={vi.fn()}
        onLoadMore={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("No symbols found")).toBeTruthy();
  });

  it("renders rows for loaded symbols", () => {
    render(
      <SymbolTable
        items={[sym(), sym({ id: "s2", name: "baz", file_path: "src/baz.ts", importance_score: 0.3 })]}
        isLoading={false}
        isValidating={false}
        hasMore={false}
        total={2}
        filters={defaultFilters}
        onFiltersChange={vi.fn()}
        onLoadMore={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("bar")).toBeTruthy();
    expect(screen.getByText("baz")).toBeTruthy();
    // ResultsFooter renders "Showing 2 of 2 symbols"
    expect(screen.getByText(/Showing/)).toBeTruthy();
  });
});
