import { describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import type { DocPageSummary } from "@repowise-dev/types/docs";
import type { WebviewHost } from "../../runtime/rpc";
import { usePresent } from "./useDocsData";

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
