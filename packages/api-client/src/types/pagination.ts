/**
 * Pagination envelope returned by list endpoints. Mirrors
 * ``repowise.server.schemas.Paginated[T]``.
 */
export interface Paginated<T> {
  items: T[];
  total: number;
  has_more: boolean;
  next_offset: number | null;
}
