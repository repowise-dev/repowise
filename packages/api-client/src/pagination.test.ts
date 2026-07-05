import { describe, expect, it, vi } from "vitest";
import { fetchAllPaginated } from "./pagination";
import type { Paginated } from "./types/pagination";

function page(
  items: number[],
  total: number,
  hasMore: boolean,
  nextOffset: number | null,
): Paginated<number> {
  return { items, total, has_more: hasMore, next_offset: nextOffset };
}

describe("fetchAllPaginated", () => {
  it("follows next_offset until has_more is false", async () => {
    const fetchPage = vi
      .fn<(offset: number, limit: number) => Promise<Paginated<number>>>()
      .mockResolvedValueOnce(page([1, 2], 4, true, 2))
      .mockResolvedValueOnce(page([3, 4], 4, false, null));

    const items = await fetchAllPaginated({ fetchPage, pageSize: 2 });

    expect(items).toEqual([1, 2, 3, 4]);
    expect(fetchPage).toHaveBeenNthCalledWith(1, 0, 2);
    expect(fetchPage).toHaveBeenNthCalledWith(2, 2, 2);
  });

  it("respects maxItems", async () => {
    const fetchPage = vi
      .fn<(offset: number, limit: number) => Promise<Paginated<number>>>()
      .mockResolvedValueOnce(page([1, 2, 3], 10, true, 3));

    const items = await fetchAllPaginated({ fetchPage, pageSize: 3, maxItems: 2 });

    expect(items).toEqual([1, 2]);
    expect(fetchPage).toHaveBeenCalledTimes(1);
  });

  it("returns an empty list when the first page is empty", async () => {
    const fetchPage = vi
      .fn<(offset: number, limit: number) => Promise<Paginated<number>>>()
      .mockResolvedValueOnce(page([], 0, false, null));

    await expect(fetchAllPaginated({ fetchPage })).resolves.toEqual([]);
  });

  it("stops when has_more is true but next_offset does not advance", async () => {
    const fetchPage = vi
      .fn<(offset: number, limit: number) => Promise<Paginated<number>>>()
      .mockResolvedValueOnce(page([1], 10, true, 0));

    await expect(fetchAllPaginated({ fetchPage })).resolves.toEqual([1]);
    expect(fetchPage).toHaveBeenCalledTimes(1);
  });

  it("stops on an empty page that still claims has_more", async () => {
    const fetchPage = vi
      .fn<(offset: number, limit: number) => Promise<Paginated<number>>>()
      .mockResolvedValueOnce(page([], 10, true, 5));

    await expect(fetchAllPaginated({ fetchPage })).resolves.toEqual([]);
    expect(fetchPage).toHaveBeenCalledTimes(1);
  });
});
