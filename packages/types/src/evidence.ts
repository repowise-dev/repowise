/**
 * Evidence primitives shared by every renderer-neutral contract (evidence
 * cards, chat view parts): how a value is known, and which entity it points
 * at. Not a published subpath: reached through `@repowise-dev/types/cards`
 * (which re-exports them) and imported by `views.ts`.
 */

/**
 * How a value is known: `measured` from the index, git or a coverage report;
 * `inferred` from the graph or a heuristic; `declared` by the repo's own
 * config or files; `unresolved` when the evidence could not be read.
 */
export const CARD_BASIS_KINDS = ["measured", "inferred", "declared", "unresolved"] as const;
export type CardBasisKind = (typeof CARD_BASIS_KINDS)[number];

export interface CardBasis {
  kind: CardBasisKind;
  /** What produced the value, e.g. `git history`, `get_risk`, `CODEOWNERS`. */
  source: string;
}

export const CARD_REF_TYPES = [
  "repo",
  "file",
  "symbol",
  "module",
  "component",
  "commit",
  "change",
  "decision",
] as const;
export type CardRefType = (typeof CARD_REF_TYPES)[number];

/** An entity a card or view points at. Never a URL; the renderer resolves it. */
export interface CardRef {
  type: CardRefType;
  /** Path for a file, qualified id for a symbol, sha for a commit. */
  id: string;
  /** 1-based line range, for file and symbol refs. */
  start_line?: number;
  end_line?: number;
}
