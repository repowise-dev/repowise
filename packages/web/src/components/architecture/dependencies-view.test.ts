import { describe, expect, it } from "vitest";
import {
  packageSummaryRequest,
  PACKAGE_SUMMARY_LIMIT,
} from "./package-graph";
import {
  DEFAULT_PACKAGE_QUERY_VALUES,
  queryFromTableState,
  tableStateFromQuery,
} from "./package-query-state";

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
