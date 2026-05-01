import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { SymbolTable } from "../../src/symbols/symbol-table.js";
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
  ...overrides,
});

describe("SymbolTable", () => {
  it("renders the empty state when no items", () => {
    render(
      <SymbolTable
        items={[]}
        importanceScores={new Map()}
        isLoading={false}
        isValidating={false}
        hasMore={false}
        q=""
        onQChange={vi.fn()}
        kind="all"
        onKindChange={vi.fn()}
        language="all"
        onLanguageChange={vi.fn()}
        onLoadMore={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("No symbols found")).toBeTruthy();
  });

  it("renders rows for loaded symbols", () => {
    render(
      <SymbolTable
        items={[sym(), sym({ id: "s2", name: "baz", file_path: "src/baz.ts" })]}
        importanceScores={new Map([["s1", 0.9], ["s2", 0.3]])}
        isLoading={false}
        isValidating={false}
        hasMore={false}
        q=""
        onQChange={vi.fn()}
        kind="all"
        onKindChange={vi.fn()}
        language="all"
        onLanguageChange={vi.fn()}
        onLoadMore={vi.fn()}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("bar")).toBeTruthy();
    expect(screen.getByText("baz")).toBeTruthy();
    expect(screen.getByText("2 symbols")).toBeTruthy();
  });
});
