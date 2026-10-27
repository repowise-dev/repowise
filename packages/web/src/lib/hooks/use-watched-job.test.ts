// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useWatchedJob } from "./use-watched-job";

describe("useWatchedJob", () => {
  it.each(["warning", "failed"] as const)("keeps a %s run on screen until dismissed", (outcome) => {
    const { result } = renderHook(() => useWatchedJob<"sync">());
    act(() => result.current.watch("j1", "sync"));

    act(() => result.current.settle(outcome));
    expect(result.current.watched).toEqual({ id: "j1", action: "sync" });

    act(() => result.current.dismiss());
    expect(result.current.watched).toBeNull();
  });

  it.each(["completed", "cancelled"] as const)("clears a %s run", (outcome) => {
    const { result } = renderHook(() => useWatchedJob<"sync">());
    act(() => result.current.watch("j1", "sync"));

    act(() => result.current.settle(outcome));
    expect(result.current.watched).toBeNull();
  });

  it("does not let a late lookup replace a job the user just started", () => {
    const { result } = renderHook(() => useWatchedJob<"sync">());
    act(() => result.current.watch("mine", "sync"));

    act(() => result.current.watchIfIdle("found-later"));
    expect(result.current.watched?.id).toBe("mine");
  });
});
