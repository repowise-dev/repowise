import { describe, expect, it } from "vitest";

import {
  clearNarrowing,
  INITIAL_FILTERS,
  narrowingCount,
  narrowingKeysFor,
  parseLegacyFilters,
  readFilters,
  serverKey,
  toQuery,
  withFilter,
  withScope,
  writeFilters,
  type PerformanceFilterState,
} from "../../src/health/performance/query";

const write = (state: PerformanceFilterState, base = "") => {
  const params = new URLSearchParams(base);
  writeFilters(state, params);
  return params;
};

const read = (search: string) => readFilters(new URLSearchParams(search));

describe("performance scope and its URL", () => {
  it("writes the scope always and every other default never", () => {
    expect(write(INITIAL_FILTERS).toString()).toBe("scope=now");
    const state = withFilter(withScope(INITIAL_FILTERS, "all"), "boundary", "db");
    expect(write(state).toString()).toBe("scope=all&boundary=db");
  });

  it("round trips every field and leaves the page's own parameters alone", () => {
    const state: PerformanceFilterState = {
      scope: "all",
      context: "test",
      boundary: "filesystem",
      confidence: "high",
      actionability: "advisory",
      proof: "unproven",
      role: "cli",
      sort: "leverage",
      page: 3,
      area: "packages/core",
      depth: 4,
    };
    const params = write(state, "tab=performance&opportunity=perf1");
    expect(params.get("tab")).toBe("performance");
    expect(params.get("opportunity")).toBe("perf1");
    expect(readFilters(params)).toEqual(state);
  });

  it("keeps the root area, the empty string, through the URL", () => {
    const params = write({ ...INITIAL_FILTERS, area: "" });
    expect(params.has("area")).toBe(true);
    expect(readFilters(params).area).toBe("");
  });

  it("removes the retired parameters on write", () => {
    const params = write(INITIAL_FILTERS, "perf=context%3Dtest&ctx=all&state=advisory&page=4");
    for (const key of ["perf", "ctx", "state", "page"]) expect(params.has(key)).toBe(false);
  });

  it("maps a link minted before scopes onto the scope that lists the same causes", () => {
    expect(read("ctx=production").scope).toBe("now");
    expect(read("ctx=production&proof=unproven")).toMatchObject({
      scope: "production",
      proof: "unproven",
    });
    expect(read("ctx=production&state=investigate")).toMatchObject({
      scope: "production",
      actionability: "investigate",
    });
    expect(read("ctx=production&role=all")).toMatchObject({ scope: "production", role: null });
    expect(read("ctx=test")).toMatchObject({ scope: "all", context: "test" });
    expect(read("ctx=all")).toMatchObject({ scope: "all", context: null });
    expect(read("perf=context%3Dtooling%26offset%3D240")).toMatchObject({
      scope: "all",
      context: "tooling",
      page: 3,
    });
    expect(parseLegacyFilters("context=production&boundary=db")).toMatchObject({
      scope: "now",
      boundary: "db",
    });
  });

  it("drops a value the vocabulary does not contain instead of forwarding it", () => {
    const parsed = read("scope=bogus&actionability=nonsense&page=-3");
    expect(parsed.scope).toBe("now");
    expect(parsed.actionability).toBeNull();
    expect(parsed.page).toBe(1);
    // Proof and role cannot narrow "now", whose causes are all measured and served.
    expect(read("scope=now&proof=unproven&context=test")).toMatchObject({ proof: null, context: null });
    // "Worth doing now" holds plan-ready and advisory causes only.
    expect(read("scope=now&actionability=investigate").actionability).toBeNull();
    expect(read("scope=now&actionability=advisory").actionability).toBe("advisory");
  });
});

describe("performance scope queries", () => {
  it("asks for the default queue in 'now', and keeps a chosen state inside it", () => {
    expect(toQuery(INITIAL_FILTERS, { limit: 100 })).toEqual({
      context: "production",
      view: "summary",
      sort: "rank",
      limit: 100,
      offset: 0,
    });
    const advisory = toQuery({ ...INITIAL_FILTERS, actionability: "advisory" }, { limit: 100 });
    expect(advisory).toMatchObject({ actionability: "advisory", queue: true });
  });

  it("lifts every default in 'production' and 'all'", () => {
    const production = toQuery(withScope(INITIAL_FILTERS, "production"), { limit: 100 });
    expect(production).toMatchObject({
      context: "production",
      actionability: "all",
      proof: "all",
      role: "all",
    });
    expect(production.queue).toBeUndefined();
    const everything = toQuery(withScope(INITIAL_FILTERS, "all", { context: "test" }), { limit: 100 });
    expect(everything).toMatchObject({ context: "test", actionability: "all" });
    expect(toQuery(withScope(INITIAL_FILTERS, "all"), { limit: 100 }).context).toBe("all");
    expect(toQuery({ ...INITIAL_FILTERS, page: 3 }, { limit: 100 }).offset).toBe(200);
  });

  it("sends the retired pairing only when told to collapse", () => {
    expect(toQuery(INITIAL_FILTERS, { limit: 20, collapseContexts: true }).context).toBe(
      "production_tooling",
    );
  });
});

describe("performance state changes", () => {
  it("returns to the first page on any filter, the area included, and resets depth with it", () => {
    const viewed = { ...INITIAL_FILTERS, page: 2, area: "src", depth: 3 };
    expect(withFilter(viewed, "boundary", "db")).toMatchObject({ page: 1, area: "src", boundary: "db" });
    expect(withFilter(viewed, "area", "src/a")).toMatchObject({ page: 1, area: "src/a", depth: null });
    expect(withFilter(viewed, "page", 3).area).toBe("src");
    expect(toQuery(viewed, { limit: 100 })).toMatchObject({ area: "src", area_depth: 3 });
  });

  it("keeps the filters a new scope can narrow by, and drops the state", () => {
    const state: PerformanceFilterState = {
      ...withScope(INITIAL_FILTERS, "all"),
      context: "test",
      boundary: "db",
      proof: "unproven",
      actionability: "expected",
    };
    expect(withScope(state, "production")).toMatchObject({
      scope: "production",
      context: null,
      boundary: "db",
      proof: "unproven",
      actionability: null,
    });
    expect(withScope(state, "now")).toMatchObject({ boundary: "db", proof: null });
    expect(narrowingKeysFor("now")).toEqual(["boundary", "confidence"]);
  });

  it("keys the server query on every field, the area included", () => {
    expect(serverKey({ ...INITIAL_FILTERS, area: "src" })).not.toBe(serverKey(INITIAL_FILTERS));
    expect(serverKey(withScope(INITIAL_FILTERS, "production"))).not.toBe(serverKey(INITIAL_FILTERS));
  });

  it("counts and clears the narrowing, the state included, without leaving the scope", () => {
    const state = { ...withScope(INITIAL_FILTERS, "all"), boundary: "db", actionability: "advisory" as const };
    expect(narrowingCount(state)).toBe(2);
    const cleared = clearNarrowing(state);
    expect(narrowingCount(cleared)).toBe(0);
    expect(cleared.scope).toBe("all");
  });
});
