import { describe, expect, it } from "vitest";
import type { HotspotPage } from "@/lib/api/git";
import { summarizePages } from "./use-hotspot-list";

const facets = { total: 3, bus_factor_1: 1, accelerating: 1, bug_magnet: 0 };
const page = (paths: string[], has_more: boolean): HotspotPage =>
  ({
    items: paths.map((file_path) => ({ file_path })),
    total: 3,
    has_more,
    next_offset: null,
    facets,
  }) as unknown as HotspotPage;

describe("summarizePages", () => {
  it("appends rows and reads counts from the first page, more from the last", () => {
    const s = summarizePages([page(["a", "b"], true), page(["c"], false)]);
    expect(s.items.map((h) => h.file_path)).toEqual(["a", "b", "c"]);
    expect(s).toMatchObject({ total: 3, facets, hasMore: false, loaded: 2 });
  });

  it("is empty before the first page lands", () => {
    expect(summarizePages(undefined)).toEqual({
      items: [],
      total: 0,
      facets: undefined,
      hasMore: false,
      loaded: 0,
    });
  });
});
