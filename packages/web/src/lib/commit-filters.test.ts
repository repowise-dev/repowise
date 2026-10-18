import { describe, expect, it } from "vitest";
import {
  commitFiltersQuery,
  effectiveWindow,
  historyFilters,
  isNarrowed,
  parseCommitFilters,
  windowRange,
} from "./commit-filters";

const NOW = new Date("2026-10-18T15:30:00Z");

describe("commit filters", () => {
  it("defaults to the past month and ignores junk", () => {
    const f = parseCommitFilters({ health: "nope", window: "decade", since: "yesterday" });
    expect(f).toMatchObject({ impact: "all", window: "month", hotspots: false, since: undefined });
  });

  it("drops days that do not exist rather than throwing", () => {
    const f = parseCommitFilters({ window: "custom", since: "2026-02-31", until: "2026-13-01" });
    expect([f.since, f.until]).toEqual([undefined, undefined]);
    expect(effectiveWindow(f)).toBe("all");
    expect(isNarrowed(f)).toBe(false);
  });

  it("keys rolling windows on the UTC day so a day's fetches agree", () => {
    expect(windowRange({ window: "week" }, NOW)).toEqual({ since: "2026-10-11T00:00:00.000Z" });
    expect(windowRange({ window: "all" }, NOW)).toEqual({});
  });

  it("makes the custom end day inclusive", () => {
    expect(windowRange({ window: "custom", since: "2026-10-01", until: "2026-10-05" }, NOW)).toEqual({
      since: "2026-10-01T00:00:00Z",
      until: "2026-10-06T00:00:00.000Z",
    });
  });

  it("maps impact to the server's delta, and leaves it out for the summary", () => {
    const f = parseCommitFilters({ health: "better", hotspots: "1", author: "a@b", area: "packages/ui" });
    expect(historyFilters(f, NOW)).toMatchObject({
      delta: "improved",
      touches_hotspot: true,
      author: "a@b",
      area: "packages/ui",
    });
    expect(historyFilters(f, NOW, { withImpact: false }).delta).toBeUndefined();
  });

  it("writes only non-default filters and keeps other params", () => {
    const q = commitFiltersQuery(new URLSearchParams("commit=abc&health=worse"), {
      impact: "all",
      window: "quarter",
      area: "src",
    });
    expect(Object.fromEntries(new URLSearchParams(q))).toEqual({
      commit: "abc",
      window: "quarter",
      area: "src",
    });
    expect(commitFiltersQuery(new URLSearchParams("window=custom&since=2026-10-01"), { window: "month" })).toBe("");
  });

  it("reads the whole history as not narrowed", () => {
    expect(isNarrowed(parseCommitFilters({ window: "all" }))).toBe(false);
    expect(isNarrowed(parseCommitFilters({}))).toBe(true);
  });
});
