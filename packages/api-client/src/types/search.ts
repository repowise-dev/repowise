// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

export interface SearchRequest {
  query: string;
  search_type?: "semantic" | "fulltext";
  limit?: number;
}

export interface SearchResultResponse {
  page_id: string;
  title: string;
  page_type: string;
  target_path: string;
  score: number;
  snippet: string;
  search_type: string;
  /**
   * True for deterministic template pages (the coverage tail) — drives the
   * "Auto" badge on the result card. Optional so older payloads type-check.
   */
  is_deterministic?: boolean;
}
