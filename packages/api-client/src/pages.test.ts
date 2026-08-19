import { describe, expect, it, vi, beforeEach } from "vitest";

const apiPost = vi.fn();
const apiGet = vi.fn();
vi.mock("./client", () => ({
  apiGet: (...args: unknown[]) => apiGet(...args),
  apiPatch: vi.fn(),
  apiPost: (...args: unknown[]) => apiPost(...args),
}));

import { listAllPages, regeneratePage } from "./pages";

describe("listAllPages", () => {
  beforeEach(() => {
    apiGet.mockReset();
  });

  it("asks for full rows when no fields option is given", async () => {
    apiGet.mockResolvedValue([]);
    await listAllPages("r1");
    expect(apiGet.mock.calls[0]![1]).toMatchObject({
      repo_id: "r1",
      fields: "full",
    });
  });

  it("passes fields=summary through on every page of the listing", async () => {
    // A full batch means there may be more, so it pages again; the second
    // batch is short and ends the loop.
    const batch = Array.from({ length: 2000 }, (_, i) => ({ id: `p${i}` }));
    apiGet.mockResolvedValueOnce(batch).mockResolvedValueOnce([{ id: "last" }]);

    const all = await listAllPages("r1", { fields: "summary" });

    expect(all).toHaveLength(2001);
    expect(apiGet.mock.calls).toHaveLength(2);
    for (const call of apiGet.mock.calls) {
      expect(call[1]).toMatchObject({ fields: "summary" });
    }
    // Sequential, not fanned out: offsets follow one another.
    expect(apiGet.mock.calls.map((c) => (c[1] as { offset: number }).offset)).toEqual([
      0, 2000,
    ]);
  });

  it("keeps full listings on the smaller batch, where a row is ~15x heavier", async () => {
    apiGet.mockResolvedValue([]);
    await listAllPages("r1");
    expect(apiGet.mock.calls[0]![1]).toMatchObject({ limit: 500, fields: "full" });
  });
});

describe("regeneratePage", () => {
  beforeEach(() => {
    apiPost.mockReset();
    apiPost.mockResolvedValue({ job_id: "j1" });
  });

  it("sends only page_id when no style override is given", async () => {
    await regeneratePage("file_page:src/main.py");
    const params = apiPost.mock.calls[0]![3];
    expect(params).toEqual({ page_id: "file_page:src/main.py" });
    expect(params).not.toHaveProperty("style");
  });

  it("includes the style param for a per-page override", async () => {
    await regeneratePage("file_page:src/main.py", { style: "caveman" });
    expect(apiPost.mock.calls[0]![3]).toEqual({
      page_id: "file_page:src/main.py",
      style: "caveman",
    });
  });

  it("omits the style param for an empty override", async () => {
    await regeneratePage("file_page:src/main.py", { style: "" });
    expect(apiPost.mock.calls[0]![3]).toEqual({ page_id: "file_page:src/main.py" });
  });

  it("includes the cascade param when given", async () => {
    await regeneratePage("file_page:src/main.py", { cascade: "dependents" });
    expect(apiPost.mock.calls[0]![3]).toEqual({
      page_id: "file_page:src/main.py",
      cascade: "dependents",
    });
  });
});
