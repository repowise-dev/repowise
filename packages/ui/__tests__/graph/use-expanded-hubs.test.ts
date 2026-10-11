import { describe, it, expect } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useExpandedHubs } from "../../src/graph/use-expanded-hubs";

describe("useExpandedHubs", () => {
  it("starts empty", () => {
    const { result } = renderHook(() => useExpandedHubs());
    expect(result.current.expandedHubs).toEqual([]);
  });

  it("toggle expands then collapses a hub", () => {
    const { result } = renderHook(() => useExpandedHubs());
    act(() => result.current.toggleHub(3));
    expect(result.current.expandedHubs).toEqual([3]);
    act(() => result.current.toggleHub(3));
    expect(result.current.expandedHubs).toEqual([]);
  });

  it("expandHub is idempotent and preserves insertion order", () => {
    const { result } = renderHook(() => useExpandedHubs());
    act(() => result.current.expandHub(1));
    act(() => result.current.expandHub(2));
    act(() => result.current.expandHub(1)); // already open
    expect(result.current.expandedHubs).toEqual([1, 2]);
  });

  it("collapseLast removes the most recently expanded hub", () => {
    const { result } = renderHook(() => useExpandedHubs());
    act(() => result.current.expandHub(1));
    act(() => result.current.expandHub(2));
    act(() => result.current.expandHub(3));
    act(() => {
      result.current.collapseLast();
    });
    expect(result.current.expandedHubs).toEqual([1, 2]);
    act(() => {
      result.current.collapseLast();
    });
    expect(result.current.expandedHubs).toEqual([1]);
  });

  it("collapseLast on an empty set is a no-op", () => {
    const { result } = renderHook(() => useExpandedHubs());
    act(() => {
      result.current.collapseLast();
    });
    expect(result.current.expandedHubs).toEqual([]);
  });

  it("collapseHub removes a specific hub", () => {
    const { result } = renderHook(() => useExpandedHubs());
    act(() => result.current.expandHub(1));
    act(() => result.current.expandHub(2));
    act(() => result.current.collapseHub(1));
    expect(result.current.expandedHubs).toEqual([2]);
  });

  it("collapseAll clears everything", () => {
    const { result } = renderHook(() => useExpandedHubs());
    act(() => result.current.expandHub(1));
    act(() => result.current.expandHub(2));
    act(() => result.current.collapseAll());
    expect(result.current.expandedHubs).toEqual([]);
  });
});
