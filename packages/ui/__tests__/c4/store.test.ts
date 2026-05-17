import { describe, it, expect } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useC4Store } from "../../src/c4/store/use-c4-store";

describe("useC4Store", () => {
  it("defaults to L2 with no container or selection", () => {
    const { result } = renderHook(() => useC4Store());
    expect(result.current.level).toBe(2);
    expect(result.current.activeContainerId).toBeNull();
    expect(result.current.selectedNodeId).toBeNull();
  });

  it("honors initialLevel and initialContainerId", () => {
    const { result } = renderHook(() =>
      useC4Store({ initialLevel: 3, initialContainerId: "pkg:packages/core" }),
    );
    expect(result.current.level).toBe(3);
    expect(result.current.activeContainerId).toBe("pkg:packages/core");
  });

  it("drillIntoContainer moves to L3 and clears selection", () => {
    const { result } = renderHook(() => useC4Store());
    act(() => result.current.selectNode("pkg:foo"));
    act(() => result.current.drillIntoContainer("pkg:packages/core"));
    expect(result.current.level).toBe(3);
    expect(result.current.activeContainerId).toBe("pkg:packages/core");
    expect(result.current.selectedNodeId).toBeNull();
  });

  it("drillOut steps L3 → L2 → L1 and drops the container", () => {
    const { result } = renderHook(() =>
      useC4Store({ initialLevel: 3, initialContainerId: "pkg:x" }),
    );
    act(() => result.current.drillOut());
    expect(result.current.level).toBe(2);
    expect(result.current.activeContainerId).toBeNull();
    act(() => result.current.drillOut());
    expect(result.current.level).toBe(1);
    act(() => result.current.drillOut()); // no-op at L1
    expect(result.current.level).toBe(1);
  });

  it("setLevel(2) from L3 drops the active container", () => {
    const { result } = renderHook(() =>
      useC4Store({ initialLevel: 3, initialContainerId: "pkg:x" }),
    );
    act(() => result.current.setLevel(2));
    expect(result.current.level).toBe(2);
    expect(result.current.activeContainerId).toBeNull();
  });

  it("fires onChange on every transition", () => {
    const calls: number[] = [];
    const { result } = renderHook(() =>
      useC4Store({ onChange: (s) => calls.push(s.level) }),
    );
    act(() => result.current.setLevel(1));
    act(() => result.current.drillIntoContainer("pkg:a"));
    expect(calls).toEqual([1, 3]);
  });
});
