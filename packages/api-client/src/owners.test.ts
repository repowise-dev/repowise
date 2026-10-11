import { describe, expect, it, vi, beforeEach } from "vitest";

const apiGet = vi.fn();
vi.mock("./client", () => ({
  apiGet: (...args: unknown[]) => apiGet(...args),
}));

import { listAllOwners } from "./owners";

describe("listAllOwners", () => {
  beforeEach(() => {
    apiGet.mockReset();
  });

  it("walks pagination until has_more is false", async () => {
    apiGet
      .mockResolvedValueOnce({
        items: [{ key: "a" }],
        total: 2,
        has_more: true,
        next_offset: 1,
      })
      .mockResolvedValueOnce({
        items: [{ key: "b" }],
        total: 2,
        has_more: false,
        next_offset: null,
      });

    const owners = await listAllOwners({ repoId: "r1", pageSize: 1 });

    expect(owners).toHaveLength(2);
    expect(apiGet).toHaveBeenCalledTimes(2);
  });
});
