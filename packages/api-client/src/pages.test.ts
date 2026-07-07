import { describe, expect, it, vi, beforeEach } from "vitest";

const apiPost = vi.fn();
vi.mock("./client", () => ({
  apiGet: vi.fn(),
  apiPatch: vi.fn(),
  apiPost: (...args: unknown[]) => apiPost(...args),
}));

import { regeneratePage } from "./pages";

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
    await regeneratePage("file_page:src/main.py", "caveman");
    expect(apiPost.mock.calls[0]![3]).toEqual({
      page_id: "file_page:src/main.py",
      style: "caveman",
    });
  });

  it("omits the style param for an empty override", async () => {
    await regeneratePage("file_page:src/main.py", "");
    expect(apiPost.mock.calls[0]![3]).toEqual({ page_id: "file_page:src/main.py" });
  });
});
