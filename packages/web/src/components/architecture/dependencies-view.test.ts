import { describe, expect, it } from "vitest";
import type { NodeSearchResult } from "../../lib/api/types";
import {
  chooseExternalPackageNode,
  packageSummaryRequest,
  PACKAGE_SUMMARY_LIMIT,
} from "./package-graph";
import {
  DEFAULT_PACKAGE_QUERY_VALUES,
  queryFromTableState,
  tableStateFromQuery,
} from "./package-query-state";

function candidate(node_id: string): NodeSearchResult {
  return { node_id, language: "", symbol_count: 0 };
}

describe("chooseExternalPackageNode", () => {
  it("prefers the exact package node over subpaths and fuzzy matches", () => {
    expect(
      chooseExternalPackageNode(
        [candidate("external:react-dom"), candidate("external:react/jsx-runtime"), candidate("external:react")],
        "react",
      ),
    ).toBe("external:react");
  });

  it("falls back to a package subpath and ignores repository files", () => {
    expect(
      chooseExternalPackageNode(
        [candidate("packages/react/index.ts"), candidate("external:@scope/pkg/subpath")],
        "@scope/pkg",
      ),
    ).toBe("external:@scope/pkg/subpath");
  });

  it("returns null when node search has no external match", () => {
    expect(chooseExternalPackageNode([candidate("src/pkg.py")], "pkg")).toBeNull();
  });

  it("does not fuzzy-focus a sibling package", () => {
    expect(chooseExternalPackageNode([candidate("external:react-dom")], "react")).toBeNull();
  });
});

describe("package query state", () => {
  it("round-trips shareable filter, sort, and page state", () => {
    const state = tableStateFromQuery({
      ...DEFAULT_PACKAGE_QUERY_VALUES,
      q: "react",
      ecosystem: "npm",
      usage: "observed",
      sort: "edges",
      order: "asc",
      page: 3,
    });
    expect(queryFromTableState(state)).toEqual({
      ...DEFAULT_PACKAGE_QUERY_VALUES,
      q: "react",
      ecosystem: "npm",
      usage: "observed",
      sort: "edges",
      order: "asc",
      page: 3,
    });
  });

  it("clamps invalid URL pages to the first page", () => {
    expect(tableStateFromQuery({ ...DEFAULT_PACKAGE_QUERY_VALUES, page: -4 }).page).toBe(1);
  });
});

describe("initial package request", () => {
  it("uses one bounded summary resource rather than a graph endpoint", () => {
    const request = packageSummaryRequest("repo-id", "primary");
    expect(request.path).toBe("/api/repos/repo-id/external-systems/summary");
    expect(request.path).not.toContain("/api/graph/");
    expect(request.params).toEqual({ scope: "primary", limit: PACKAGE_SUMMARY_LIMIT });
  });
});
