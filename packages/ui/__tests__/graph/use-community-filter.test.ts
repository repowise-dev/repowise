import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import Graph from "graphology";
import { useCommunityFilter } from "../../src/graph/use-community-filter";
import type { SigmaNodeAttributes, SigmaEdgeAttributes } from "../../src/graph/sigma/types";

/** A graph of file nodes, one per community id given. */
function graphOf(communityIds: number[]) {
  const g = new Graph<SigmaNodeAttributes, SigmaEdgeAttributes>();
  communityIds.forEach((cid, i) => {
    g.addNode(`f${i}.ts`, {
      x: 0,
      y: 0,
      size: 1,
      color: "#000",
      label: `f${i}.ts`,
      nodeType: "file",
      communityId: cid,
    } as SigmaNodeAttributes);
  });
  return g;
}

describe("useCommunityFilter", () => {
  it("ignores a community with nothing on the canvas", () => {
    // The key used to list communities from the repo-wide summary, so clicking
    // one that was not drawn added a phantom id: nothing dimmed and the swatch
    // stayed filled.
    const { result } = renderHook(() => useCommunityFilter(graphOf([0, 1, 2])));
    act(() => result.current.handleCommunityToggle(99));
    expect(result.current.activeCommunities).toBeNull();
    expect(result.current.communityDimmedNodes).toBeNull();
  });

  it("does not let an id from a graph that is gone un-filter the canvas", () => {
    // The drawn set changes on a signal toggle, a module filter, an ego depth
    // and a load-more. A leftover id made `next.size` match `allCommunityIds`
    // and reset the filter to "show all" — on a click whose label said hide.
    const { result, rerender } = renderHook(
      ({ ids }: { ids: number[] }) => useCommunityFilter(graphOf(ids)),
      { initialProps: { ids: [0, 1, 2, 3] } },
    );
    act(() => result.current.handleCommunityToggle(0));
    expect(result.current.activeCommunities).toEqual(new Set([1, 2, 3]));

    // Community 3 leaves the canvas; the stale id stays in state, invisibly.
    rerender({ ids: [1, 2] });
    act(() => result.current.handleCommunityToggle(1));
    expect(result.current.activeCommunities).toEqual(new Set([2]));
  });

  it("still collapses to no filter once every drawn community is back on", () => {
    const { result } = renderHook(() => useCommunityFilter(graphOf([0, 1])));
    act(() => result.current.handleCommunityToggle(0));
    expect(result.current.activeCommunities).toEqual(new Set([1]));
    act(() => result.current.handleCommunityToggle(0));
    expect(result.current.activeCommunities).toBeNull();
  });

  it("reports the communities that are actually drawn", () => {
    const { result } = renderHook(() => useCommunityFilter(graphOf([4, 4, 7])));
    expect(result.current.drawnCommunityIds).toEqual(new Set([4, 7]));
  });
});
