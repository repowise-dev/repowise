// ---------------------------------------------------------------------------
// Symbols
// ---------------------------------------------------------------------------

export interface SymbolImportanceComponents {
  file_pagerank: number;
  visibility_factor: number;
  complexity_norm: number;
  kind_boost: number;
  is_entry_point: boolean;
}

export interface SymbolResponse {
  id: string;
  repository_id: string;
  file_path: string;
  symbol_id: string;
  name: string;
  qualified_name: string;
  kind: string;
  signature: string;
  start_line: number;
  end_line: number;
  docstring: string | null;
  visibility: string;
  is_async: boolean;
  complexity_estimate: number;
  language: string;
  parent_name: string | null;
  importance_score?: number | null;
  importance_components?: SymbolImportanceComponents | null;
  file_pagerank?: number | null;
  is_entry_point?: boolean | null;
  file_churn_percentile?: number | null;
  file_is_hotspot?: boolean | null;
}
