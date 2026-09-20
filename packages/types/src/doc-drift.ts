/**
 * Canonical documentation-drift types.
 *
 * Canonical source: the engine's `DocDriftResponse` / `DocDriftReferencesResponse`
 * (`packages/server/src/repowise/server/schemas/doc_drift.py`), emitted verbatim
 * by the route. Nothing here is a normalisation of the wire: a client that
 * reshapes this contract is a client that recomputes the summary, and the
 * summary is served precisely so it cannot be recomputed from a page.
 *
 * The vocabulary carries a claim, and the claims are not the same size:
 * a *finding* says a document asserts something the repository no longer
 * satisfies, and is filed against the document. A *reference* says only that a
 * document names a file the repository still has. It never says the document
 * describes that file.
 */

/**
 * Why a drift answer could not be given.
 *
 * Not an error state. `not_computed` means the drift pass has never run on
 * this index, which must not render as a repository with clean documentation;
 * `index_predates_doc_drift` means the index is older than the store;
 * `drift_read_failed` is anything else, and is separate because "reindex" is
 * the wrong advice for a locked database.
 *
 * Mirrors `UNAVAILABLE_NOT_COMPUTED` / `UNAVAILABLE_NO_TABLE` /
 * `UNAVAILABLE_READ_FAILED` in `core/analysis/doc_drift/constants.py`, pinned
 * by `tests/unit/doc_drift/test_ts_contract_parity.py`.
 */
export type DocDriftUnavailable =
  | "not_computed"
  | "index_predates_doc_drift"
  | "drift_read_failed";

/**
 * The reference classes the detector ships.
 *
 * `symbol` is deliberately absent: it was measured at a 55-69% flag rate and
 * killed, because backticks in technical prose mean "this is a literal token",
 * not "this is a code symbol".
 */
export type DocDriftKind = "path" | "link" | "anchor" | "command";

/**
 * The one set of confidence boundaries, mirroring the engine.
 *
 * `HIGH` is `HIGH_CONFIDENCE_THRESHOLD` and `MEDIUM` is
 * `REVIEW_CONFIDENCE_THRESHOLD` in `core/analysis/doc_drift/constants.py`,
 * which are themselves held equal to dead code's boundaries so that "high"
 * means one thing to a reader comparing the two reports.
 *
 * Duplicated across languages rather than derived, and pinned by a parity
 * test, because three surfaces disagreeing about where "high" starts is a
 * failure this repository has already had once
 * (`tests/unit/dead_code/test_confidence_parity.py`).
 */
export const DOC_DRIFT_CONFIDENCE = {
  /** At or above this, a finding is near-certain. */
  HIGH: 0.7,
  /** The default floor; below it findings are not fetched. */
  MEDIUM: 0.4,
} as const;

/** Which tier a confidence falls in, using the boundaries above. */
export function docDriftConfidenceTier(confidence: number): "high" | "medium" | "low" {
  if (confidence >= DOC_DRIFT_CONFIDENCE.HIGH) return "high";
  if (confidence >= DOC_DRIFT_CONFIDENCE.MEDIUM) return "medium";
  return "low";
}

/**
 * What each reference class is, in a reader's words.
 *
 * The slugs are an API vocabulary, not English: a table column headed `anchor`
 * tells a reader nothing about a heading link whose heading was renamed.
 * Keyed on every value `DocDriftKind` can take, pinned by the same parity test.
 */
export const DOC_DRIFT_KIND_LABELS: Record<DocDriftKind, string> = {
  path: "File path",
  link: "Link",
  anchor: "Heading link",
  command: "Command",
};

/** Label for one reference class, falling back to the raw slug. */
export function docDriftKindLabel(kind: string): string {
  return DOC_DRIFT_KIND_LABELS[kind as DocDriftKind] ?? kind;
}

export interface DocDriftFinding {
  /**
   * Stable across stores and rebuilds, derived from
   * `(file_path, kind, line_number, target)`. The row's own primary key is an
   * autoincrement integer and is re-minted whenever a store is rebuilt, so it
   * is this id that a client keys triage on.
   */
  id: string;
  /**
   * The *document* that is wrong, and the file a reader has to edit. Never the
   * target it names. Rendering this as the broken code file tells the reader
   * the opposite of the truth.
   */
  file_path: string;
  line_number: number;
  kind: string;
  /** What the document claims exists. */
  target: string;
  confidence: number;
  /** Which named resolution strategy produced this. */
  origin: string;
  /** One human sentence. */
  reason: string;
  /** The reference exactly as written. */
  raw: string;
  /** The source line, trimmed, so a finding can be judged without opening the file. */
  context: string;
  /** Lines a reader can check for themselves. */
  evidence: string[];
}

/**
 * The rollup above a findings list, computed server-side over the same rows.
 *
 * `findings_total` counts every finding matching the query, including any the
 * display cap left out, so a client must not recompute these numbers from the
 * page it holds.
 */
export interface DocDriftSummary {
  findings_total: number;
  /** Distinct documents carrying at least one finding. */
  documents: number;
  confidence: Record<string, number>;
  by_kind: Record<string, number>;
  /**
   * What the counts do and do not cover. Most references in a real tree are
   * uncheckable by design, so no surface may show the counts without it.
   */
  findings_basis: string;
}

export interface DocDriftResponse {
  findings: DocDriftFinding[];
  /** Rows served after the cap; below `summary.findings_total` when capped. */
  findings_emitted: number;
  /** Null exactly when `unavailable` is set. */
  summary: DocDriftSummary | null;
  unavailable: DocDriftUnavailable | null;
}

/** A document that names a file the repository still has. */
export interface DocDriftReference {
  document: string;
  line: number;
  kind: string;
  /** The enclosing heading trail, so a reader can find the passage. */
  section?: string;
}

/**
 * A naming document that carries drift somewhere in it.
 *
 * `findings` counts the whole document. A drifted reference resolves to
 * nothing, so this can never mean "this document's description of your file is
 * wrong", and a surface that words it that way is lying.
 */
export interface DocDriftDocumentDrift {
  document: string;
  findings: number;
}

export interface DocDriftReferencesResponse {
  target_path: string;
  references: DocDriftReference[];
  /** Rows served after the cap; below `references_total` when capped. */
  references_emitted: number;
  /** Every reference to the target, so a capped list cannot read as complete. */
  references_total: number;
  /** Distinct documents naming the target, before any display cap. */
  documents: number;
  documents_with_drift: DocDriftDocumentDrift[];
  /** What a reference claims, and what its absence does not prove. */
  references_basis: string;
  unavailable: DocDriftUnavailable | null;
}
