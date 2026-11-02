/**
 * The lazy area source: one request per need, a reveal that walks down to a
 * deep link, and files that cover everything below where they were loaded.
 */
import { describe, it, expect, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { AreaTree } from "@repowise-dev/types/areas";
import type { HealthMapFeed } from "@repowise-dev/types/health";

import { useAreaSource } from "../../src/health/map/use-area-source.js";

function n(path: string, depth: number, hasChildren = true) {
  return {
    path,
    name: path.split("/").pop() ?? "",
    depth,
    parent: depth === 0 ? null : path.split("/").slice(0, -1).join("/"),
    has_children: hasChildren,
    direct_file_count: hasChildren ? 0 : 1,
    file_count: 1,
    nloc: 10,
    health: 8,
    band: "good" as const,
    band_counts: { at_risk: 0, needs_work: 0, fair: 0, good: 1, excellent: 0 },
    worst_file: { path: null, score: 8 },
    hotspot_count: 0,
    bus_factor_1_count: 0,
    primary_owner: null,
    line_coverage_pct: null,
    coverage_file_count: 0,
    findings: { critical: 0, high: 0, medium: 0, low: 0 },
    health_delta_30d: null,
  };
}

/** A chain a/b/c/d/e/f below the root, served `depth` levels at a time. */
const CHAIN = ["a", "a/b", "a/b/c", "a/b/c/d", "a/b/c/d/e", "a/b/c/d/e/f"];
function serveTree(root: string, depth: number): Promise<AreaTree> {
  const rootDepth = root ? root.split("/").length : 0;
  const nodes = CHAIN.filter((p) => p.startsWith(root ? `${root}/` : "") && p !== root)
    .map((p) => n(p, p.split("/").length, p !== "a/b/c/d/e/f"))
    .filter((x) => x.depth <= rootDepth + depth);
  return Promise.resolve({
    root: n(root, rootDepth),
    depth,
    nodes,
    delta_window: { target_days: 30, latest_at: null, baseline_at: null },
  });
}

const feed = (paths: string[]) =>
  Promise.resolve({ files: paths.map((file_path) => ({ file_path, nloc: 10, score: 8 })) } as unknown as HealthMapFeed);

describe("useAreaSource", () => {
  it("starts from the root and unfolds a reveal down to a deep area", async () => {
    const getAreaTree = vi.fn(serveTree);
    const { result } = renderHook(() =>
      useAreaSource({ getAreaTree, getAreaFiles: vi.fn(() => feed([])) }, "k"),
    );
    await waitFor(() => expect(result.current.index.areas.has("")).toBe(true));
    act(() => result.current.reveal("a/b/c/d/e/f"));
    await waitFor(() => expect(result.current.index.areas.has("a/b/c/d/e/f")).toBe(true));
    // Root first (two levels), then reveal steps of four from the deepest known.
    expect(getAreaTree.mock.calls[0]).toEqual(["", 2]);
    expect(getAreaTree.mock.calls.some(([root, depth]) => root === "a/b" && depth === 4)).toBe(true);
  });

  it("loads an area's files once, and that load covers what is under it", async () => {
    const getAreaFiles = vi.fn(() => feed(["a/b/x.py", "a/y.py"]));
    const { result } = renderHook(() => useAreaSource({ getAreaTree: serveTree, getAreaFiles }, "k"));
    await waitFor(() => expect(result.current.index.areas.has("")).toBe(true));
    act(() => result.current.request({ children: [], files: ["a"] }));
    act(() => result.current.request({ children: [], files: ["a", "a/b"] }));
    await waitFor(() => expect(result.current.files).toHaveLength(2));
    expect(getAreaFiles).toHaveBeenCalledTimes(1);
    expect(result.current.filesCovered("a/b/c")).toBe(true);
    expect(result.current.filesCovered("z")).toBe(false);
  });

  it("starts over for a new repository or reading", async () => {
    const getAreaTree = vi.fn(serveTree);
    const fetchers = { getAreaTree, getAreaFiles: vi.fn(() => feed([])) };
    const { result, rerender } = renderHook(({ key }) => useAreaSource(fetchers, key), {
      initialProps: { key: "one" },
    });
    await waitFor(() => expect(result.current.index.areas.has("")).toBe(true));
    rerender({ key: "two" });
    await waitFor(() => expect(getAreaTree.mock.calls.filter(([r]) => r === "").length).toBeGreaterThanOrEqual(2));
  });
});
