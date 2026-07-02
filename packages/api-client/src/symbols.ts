import type { SymbolDetailResponse } from "@repowise-dev/types/symbols";
import { apiGet } from "./client";
import type { Paginated, SymbolResponse } from "./types";

export type SymbolSortKey = "importance" | "name" | "complexity" | "kind";

export interface SymbolListParams {
  repo_id: string;
  q?: string;
  kind?: string;
  language?: string;
  visibility?: string;
  file_path?: string;
  in_hot_files?: boolean;
  in_entry_points?: boolean;
  sort?: SymbolSortKey;
  limit?: number;
  offset?: number;
}

/** Paginated symbol search — preferred entry point. */
export async function listSymbolsPage(
  params: SymbolListParams,
): Promise<Paginated<SymbolResponse>> {
  const p: Record<string, string | number | boolean | undefined> = { ...params };
  return apiGet<Paginated<SymbolResponse>>("/api/symbols", p);
}

/**
 * Back-compat: returns just the items array. Existing callers that don't
 * (yet) render pagination keep working but get importance-ranked output
 * by default.
 */
export async function listSymbols(params: SymbolListParams): Promise<SymbolResponse[]> {
  const page = await listSymbolsPage(params);
  return page.items;
}

export async function lookupSymbolByName(
  name: string,
  repoId: string,
): Promise<SymbolResponse[]> {
  return apiGet<SymbolResponse[]>(`/api/symbols/by-name/${encodeURIComponent(name)}`, {
    repo_id: repoId,
  });
}

export async function getSymbolById(symbolDbId: string): Promise<SymbolResponse> {
  return apiGet<SymbolResponse>(`/api/symbols/${encodeURIComponent(symbolDbId)}`);
}

/** Symbol entity-page aggregate: row + blame + callers/callees + decisions. */
export async function getSymbolDetail(
  repoId: string,
  symbolId: string,
): Promise<SymbolDetailResponse> {
  return apiGet<SymbolDetailResponse>("/api/symbols/detail", {
    repo_id: repoId,
    symbol_id: symbolId,
  });
}
