/**
 * Canonical symbol types.
 *
 * Canonical source: engine `SymbolResponse`. Some downstream backends emit a
 * leaner shape that omits `repository_id` and `symbol_id` — consumer-side
 * adapters synthesise both before passing data to components.
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

// ---------------------------------------------------------------------------
// Heritage (extends / implements / trait_impl / mixin / overrides)
// ---------------------------------------------------------------------------

/**
 * Heritage relation kinds. Mirrors the engine's edge_type values for
 * heritage edges in the symbol graph plus the raw AST-level "mixin" kind.
 */
export type HeritageKind =
  | "extends"
  | "implements"
  | "trait_impl"
  | "mixin"
  | "method_overrides"
  | "method_implements";

/**
 * A single resolved heritage edge.
 *
 * `child_id` and `parent_id` are symbol-graph node IDs (e.g.
 * `src/app.py::MyClass`). `confidence` is present for resolved relations
 * (0.0–1.0) and absent for raw, unresolved entries lifted directly out of
 * `parsed_files.json::heritage` (in which case only `child_name`,
 * `parent_name`, `kind`, and `line` are meaningful).
 */
export interface HeritageRelation {
  child_id?: string;
  parent_id?: string;
  child_name: string;
  parent_name: string;
  kind: HeritageKind;
  line: number;
  confidence?: number;
}

/**
 * Heritage view for a single symbol — both directions of the relation.
 *
 * `parents` are the relations where this symbol is the child (i.e. what it
 * extends/implements). `children` are the relations where this symbol is the
 * parent (i.e. what extends/implements it).
 */
export interface SymbolHeritage {
  symbol_id: string;
  parents: HeritageRelation[];
  children: HeritageRelation[];
}
