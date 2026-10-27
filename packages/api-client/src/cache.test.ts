import { describe, expect, it } from "vitest";

import { createCache } from "./cache";

describe("createCache", () => {
  it("returns what was set under the same repo, key and tag", () => {
    const cache = createCache();
    cache.set("repo-1", "health", "abc123", { score: 7.2 });
    expect(cache.get("repo-1", "health", "abc123")).toEqual({ score: 7.2 });
  });

  it("misses when any part of the key differs", () => {
    const cache = createCache();
    cache.set("repo-1", "health", "abc123", 1);
    expect(cache.get("repo-2", "health", "abc123")).toBeUndefined();
    expect(cache.get("repo-1", "risk", "abc123")).toBeUndefined();
    // A new freshness tag (e.g. a new head commit) never reads the old entry.
    expect(cache.get("repo-1", "health", "def456")).toBeUndefined();
  });

  it("does not collide when parts would concatenate to the same string", () => {
    const cache = createCache();
    cache.set("a b", "c", "t", "first");
    cache.set("a", "b c", "t", "second");
    expect(cache.get("a b", "c", "t")).toBe("first");
    expect(cache.get("a", "b c", "t")).toBe("second");
  });

  it("overwrites an existing entry", () => {
    const cache = createCache();
    cache.set("r", "k", "t", "old");
    cache.set("r", "k", "t", "new");
    expect(cache.get("r", "k", "t")).toBe("new");
  });

  it("drops everything on invalidateAll", () => {
    const cache = createCache();
    cache.set("r", "k1", "t", 1);
    cache.set("r", "k2", "t", 2);
    cache.invalidateAll();
    expect(cache.get("r", "k1", "t")).toBeUndefined();
    expect(cache.get("r", "k2", "t")).toBeUndefined();
  });

  it("keeps separate instances independent", () => {
    const a = createCache();
    const b = createCache();
    a.set("r", "k", "t", 1);
    expect(b.get("r", "k", "t")).toBeUndefined();
  });
});
