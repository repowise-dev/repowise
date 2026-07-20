import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SymbolPage } from "../../src/symbols/symbol-page.js";
import { SymbolDrawer } from "../../src/symbols/symbol-drawer.js";
import type {
  SymbolDetailData,
  SymbolDetailResponse,
} from "@repowise-dev/types/symbols";

const daysAgo = (n: number) =>
  new Date(Date.now() - n * 86_400_000).toISOString();

const routeData = (
  overrides: Partial<SymbolDetailResponse> = {},
): SymbolDetailResponse => ({
  symbol: {
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
    file_is_hotspot: true,
  },
  graph: { pagerank: 0, in_degree: 0, out_degree: 0, callers: [], callees: [] },
  governing_decisions: [],
  file_context: {
    file_path: "src/foo.ts",
    health_score: 70,
    is_hotspot: true,
    primary_owner: "Ada",
    language: "typescript",
  },
  ...overrides,
});

const drawerData = (
  overrides: Partial<SymbolDetailData> = {},
): SymbolDetailData => ({
  identity: {
    name: "bar",
    qualified_name: "foo.bar",
    kind: "function",
    visibility: "public",
    language: "typescript",
    is_async: false,
    file_path: "src/foo.ts",
    start_line: 10,
    parent_name: null,
    file_is_hotspot: true,
  },
  ...overrides,
});

describe("symbol headers", () => {
  it("shows the fix count beside the hot-file flame on the route", () => {
    render(
      <SymbolPage
        data={routeData({ fix_count: 2, fix_last_at: daysAgo(5) })}
        repoId="r"
      />,
    );
    expect(screen.getByText("hot file")).toBeTruthy();
    expect(screen.getByText("2 fixes · last 5d ago")).toBeTruthy();
  });

  it("leaves the route header unchanged for a symbol with no fixes", () => {
    render(<SymbolPage data={routeData({ fix_count: 0 })} repoId="r" />);
    expect(screen.getByText("hot file")).toBeTruthy();
    expect(screen.queryByText(/fix/)).toBeNull();
  });

  it("shows the same fix count in the drawer header", () => {
    render(
      <SymbolDrawer
        data={drawerData({ fix_count: 2, fix_last_at: daysAgo(5) })}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText("2 fixes · last 5d ago")).toBeTruthy();
  });

  it("leaves the drawer header unchanged for a symbol with no fixes", () => {
    render(<SymbolDrawer data={drawerData({ fix_count: null })} onClose={() => {}} />);
    expect(screen.queryByText(/fix/)).toBeNull();
  });
});
