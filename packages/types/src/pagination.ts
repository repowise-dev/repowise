/**
 * Canonical pagination envelope used by all list endpoints (git, symbols,
 * dead-code, etc.). Mirrors `repowise.server.schemas.Paginated[T]`.
 *
 * Clients should read `total` as authoritative ("there are N matching rows
 * server-side") and use `next_offset` to fetch further pages. When
 * `has_more` is false, the list is complete.
 */
export interface Paginated<T> {
  items: T[];
  total: number;
  has_more: boolean;
  next_offset: number | null;
}
