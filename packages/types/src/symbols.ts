/**
 * Canonical symbol types.
 *
 * Canonical source: OSS engine `SymbolResponse`. Hosted's `SymbolEntryBackend`
 * is identical except missing `repository_id` and `symbol_id` — adapters
 * synthesise both.
 */

export type SymbolKind =
  | "function"
  | "method"
  | "class"
  | "interface"
  | "struct"
  | "enum"
  | "trait"
  | "module"
  | "variable"
  | "type"
  | string;

export type SymbolVisibility = "public" | "private" | "protected" | string;

/** Renamed `CodeSymbol` to avoid shadowing the global `Symbol`. */
export interface CodeSymbol {
  id: string;
  repository_id: string;
  file_path: string;
  symbol_id: string;
  name: string;
  qualified_name: string;
  kind: SymbolKind;
  signature: string;
  start_line: number;
  end_line: number;
  docstring: string | null;
  visibility: SymbolVisibility;
  is_async: boolean;
  complexity_estimate: number;
  language: string;
  parent_name: string | null;
}

export interface SymbolList {
  total: number;
  symbols: CodeSymbol[];
}
