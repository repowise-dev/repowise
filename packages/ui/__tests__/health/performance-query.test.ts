import { describe, expect, it } from "vitest";

import {
  clearNarrowing,
  INITIAL_FILTERS,
  narrowingCount,
  parseFilters,
  serializeFilters,
  toQuery,
  withFilter,
} from "../../src/health/performance/query";

describe("performance filter state", () => {
  it("serializes only what differs from the default, and always the context", () => {
    // Context is written even at its default so a shared link keeps meaning
    // the context it was shared in, whatever the default becomes later.
    expect(serializeFilters(INITIAL_FILTERS)).toBe("context=production");
    const narrowed = withFilter(withFilter(INITIAL_FILTERS, "context", "all"), "boundary", "db");
    expect(serializeFilters(narrowed)).toBe("context=all&boundary=db");
  });

  it("round trips every field through a query string", () => {
    const state = {
      context: "test" as const,
      boundary: "filesystem",
      confidence: "high" as const,
      actionability: "plan_ready" as const,
      sort: "leverage" as const,
      offset: 40,
    };
    expect(parseFilters(serializeFilters(state))).toEqual(state);
  });

  it("drops a value the vocabulary does not contain instead of forwarding it", () => {
    const parsed = parseFilters("context=production_tooling&actionability=nonsense&offset=-3");
    expect(parsed.context).toBe("production");
    expect(parsed.actionability).toBeNull();
    expect(parsed.offset).toBe(0);
  });

  it("returns to the first page when a filter narrows, and holds it when paging", () => {
    const paged = withFilter(INITIAL_FILTERS, "offset", 20);
    expect(paged.offset).toBe(20);
    expect(withFilter(paged, "boundary", "db").offset).toBe(0);
    expect(withFilter(paged, "offset", 40).offset).toBe(40);
  });

  it("sends canonical contexts, and the retired pairing only when told to collapse", () => {
    const production = withFilter(INITIAL_FILTERS, "context", "production");
    expect(toQuery(production, { limit: 20 }).context).toBe("production");
    expect(toQuery(production, { limit: 20, collapseContexts: true }).context).toBe(
      "production_tooling",
    );
    const test = withFilter(INITIAL_FILTERS, "context", "test");
    expect(toQuery(test, { limit: 20, collapseContexts: true }).context).toBe("test");
    const unknown = withFilter(INITIAL_FILTERS, "context", "unknown");
    expect(toQuery(unknown, { limit: 20 }).context).toBe("unknown");
  });

  it("omits a narrowing parameter that is not set rather than sending an empty one", () => {
    const query = toQuery(INITIAL_FILTERS, { limit: 20 });
    expect(query).toEqual({
      context: "production",
      view: "detail",
      sort: "rank",
      limit: 20,
      offset: 0,
    });
    expect("boundary" in query).toBe(false);
  });

  it("counts and clears the narrowing filters without touching context", () => {
    let state = withFilter(INITIAL_FILTERS, "context", "test");
    state = withFilter(state, "boundary", "db");
    state = withFilter(state, "actionability", "advisory");
    expect(narrowingCount(state)).toBe(2);
    const cleared = clearNarrowing(state);
    expect(narrowingCount(cleared)).toBe(0);
    expect(cleared.context).toBe("test");
  });
});
