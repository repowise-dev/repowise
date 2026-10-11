/**
 * Discriminated union of "entity kinds" addressable across the Repowise UI.
 * Every entity link, hover card, and context drawer entry uses this shape.
 *
 * Kept in `@repowise-dev/ui` (presentational layer) so the hosted frontend
 * inherits the same vocabulary without a data-layer import.
 */
export type EntityKind = "file" | "symbol" | "decision" | "owner" | "commit";

export interface EntityRef {
  kind: EntityKind;
  /**
   * Stable id for the entity in its kind's own namespace:
   *  - file: relative path (e.g. "packages/core/src/.../graph.py")
   *  - symbol: fully-qualified symbol id (e.g. "module.path::name")
   *  - decision: decision record id
   *  - owner: email (preferred) or display name fallback
   *  - commit: 40-char SHA (or short SHA accepted)
   */
  id: string;
  /** Repo scope for entities that are repo-relative. */
  repoId?: string;
}

export interface FileEntityMeta {
  owner?: string | null;
  churnPercentile?: number | null;
  busFactor?: number | null;
  hasDocs?: boolean;
  hasDeadCode?: boolean;
  language?: string | null;
  /** Curated one-line summary from the knowledge graph (never-empty floor). */
  summary?: string | null;
  /** Curated tags (entry_point, barrel, ci, …). */
  tags?: string[];
}

export interface SymbolEntityMeta {
  signature?: string | null;
  complexity?: number | null;
  callerCount?: number | null;
  isAsync?: boolean;
  visibility?: string | null;
}

export interface DecisionEntityMeta {
  status?: string | null;
  stalenessScore?: number | null;
  source?: string | null;
}

export interface OwnerEntityMeta {
  busFactorFiles?: number | null;
  topFiles?: string[];
  email?: string | null;
}

export interface CommitEntityMeta {
  shortSha?: string;
  message?: string | null;
  author?: string | null;
  date?: string | null;
}

export type EntityMeta =
  | { kind: "file"; data?: FileEntityMeta }
  | { kind: "symbol"; data?: SymbolEntityMeta }
  | { kind: "decision"; data?: DecisionEntityMeta }
  | { kind: "owner"; data?: OwnerEntityMeta }
  | { kind: "commit"; data?: CommitEntityMeta };
