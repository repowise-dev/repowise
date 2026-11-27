import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { DocPageSummary } from "@repowise-dev/types/docs";
import type { WebviewHost } from "../../runtime/rpc";
import { useDocsData, usePresent } from "./useDocsData";

const OVERVIEW = {
  id: "ov",
  page_type: "repo_overview",
  title: "Repository Overview: demo",
  target_path: "",
} as unknown as DocPageSummary;

describe("usePresent", () => {
  it("reports a page fetch that fails instead of dropping it", async () => {
    const pageById = vi.fn().mockRejectedValue(new Error("Not found"));
    const host = { api: { pageById } } as unknown as WebviewHost;
    const { result } = renderHook(() => usePresent(host, [OVERVIEW]));

    expect(result.current.presentable).toBe(true);
    await act(() => result.current.open());

    expect(pageById).toHaveBeenCalledWith("ov");
    expect(result.current.model).toBeNull();
    expect(result.current.error).toBe("Not found");
  });
});

describe("useDocsData", () => {
  afterEach(() => {
    delete (globalThis as Record<string, unknown>).acquireVsCodeApi;
  });

  it("reopens on the page last read and keeps the next one", async () => {
    let state: unknown = { docsPageId: "p2" };
    (globalThis as Record<string, unknown>).acquireVsCodeApi = () => ({
      postMessage: () => {},
      getState: () => state,
      setState: (next: unknown) => {
        state = next;
      },
    });
    const pageById = vi.fn().mockImplementation((id: string) => Promise.resolve({ ...OVERVIEW, id }));
    const host = {
      api: { pageTree: vi.fn().mockResolvedValue([OVERVIEW]), pageById },
    } as unknown as WebviewHost;
    const { result } = renderHook(() => useDocsData(host, {}, 0));

    await waitFor(() => expect(result.current.selectedPageId).toBe("p2"));
    expect(pageById).toHaveBeenCalledWith("p2");

    act(() => result.current.setSelectedPageId("ov"));
    await waitFor(() => expect(state).toEqual({ docsPageId: "ov" }));
  });
});
