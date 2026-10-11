import { apiGet } from "./client";
import type { SearchResultResponse } from "./types";

export async function search(
  query: string,
  opts?: {
    search_type?: "semantic" | "fulltext";
    limit?: number;
    repo_id?: string;
  },
): Promise<SearchResultResponse[]> {
  const params: Record<string, string | number> = {
    query,
    search_type: opts?.search_type ?? "semantic",
    limit: opts?.limit ?? 10,
  };
  if (opts?.repo_id) {
    params.repo_id = opts.repo_id;
  }
  return apiGet<SearchResultResponse[]>("/api/search", params);
}
