/**
 * Helpers for walking paginated list endpoints that return the shared
 * `Paginated<T>` envelope from the repowise REST API.
 */

import type { Paginated } from "./types/pagination";

export interface FetchAllPaginatedOptions<T> {
  /** Fetch one page starting at `offset` with the given `limit`. */
  fetchPage: (offset: number, limit: number) => Promise<Paginated<T>>;
  /** Page size passed to `fetchPage` on each request. Defaults to 100. */
  pageSize?: number;
  /** Optional hard cap on the number of items collected. */
  maxItems?: number;
}

/**
 * Collect every item from a paginated endpoint by following `has_more` /
 * `next_offset` until the list is complete or `maxItems` is reached.
 */
export async function fetchAllPaginated<T>({
  fetchPage,
  pageSize = 100,
  maxItems,
}: FetchAllPaginatedOptions<T>): Promise<T[]> {
  const items: T[] = [];
  let offset = 0;

  while (true) {
    const page = await fetchPage(offset, pageSize);
    items.push(...page.items);

    if (maxItems !== undefined && items.length >= maxItems) {
      return items.slice(0, maxItems);
    }
    if (!page.has_more || page.next_offset == null) {
      break;
    }
    offset = page.next_offset;
  }

  return items;
}
