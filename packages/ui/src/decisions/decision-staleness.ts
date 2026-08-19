import type { DecisionRecord } from "@repowise-dev/types/decisions";

/**
 * What `staleness_score` is allowed to say about a record, in words.
 *
 * The stored score is a proportion, and its numerator decides the wording.
 * `DecisionExtractor.compute_staleness` counts a file when its last commit
 * postdates the record's birth **and** when the file has no git metadata at
 * all, which that function's own docstring spells out: "the record names
 * something the repository does not track, so it cannot be shown to still
 * hold". The count is therefore files that cannot be shown to be unchanged,
 * not files observed to change, and the sentence has to say both. An earlier
 * cut said only "have changed", which reports a record naming three deleted
 * paths as three files that changed.
 *
 * It is a fact either way, and a fact survives being read out loud. `0.42`
 * does not, which is why both surfaces used to print a percentage nobody
 * could act on.
 *
 * The reason this is a shared helper rather than two render branches is a
 * collision that shipped. A record naming **no** files also scores 0.0,
 * because the question cannot be asked of it at all, and 0.0 rendered as the
 * same dash as a record whose code genuinely had not moved. Two meanings on
 * one encoding, where the reader who correctly infers one has been taught a
 * rule that makes them confidently wrong about the other. `kind` splits them
 * so no caller can accidentally re-merge them.
 *
 * Zero git calls: both inputs are already on the row every list response
 * carries, so this is affordable in a table cell.
 */
export type DecisionStalenessKind =
  | "unscoped"
  | "unscored"
  | "unchanged"
  | "moved";

/**
 * The statuses `recompute_decision_staleness` actually rescores. Everything
 * else keeps whatever was in the column, and the column defaults to 0.0, so a
 * deprecated record or one created since the last index would otherwise have
 * an unset default promoted into the sentence "all N files are unchanged".
 * Asserting a fact nobody measured is the overstatement this file exists to
 * remove, so those records say the question was not asked of them.
 */
const RESCORED_STATUSES = new Set(["active", "proposed"]);

export interface DecisionStaleness {
  kind: DecisionStalenessKind;
  /** Files the record binds to. Zero exactly when `kind` is `unscoped`. */
  fileCount: number;
  /** Files that cannot be shown to be unchanged. Zero unless `moved`. */
  changedCount: number;
  /** The full reading, for a detail surface. */
  sentence: string;
  /** The same fact at table width. Never a bare dash; see above. */
  short: string;
}

/**
 * Describe a record's currency from the fields a list response already holds.
 *
 * Accepts the two fields rather than the whole record so a caller holding a
 * narrower row (the hosted artifact projection carries no `DecisionRecord`)
 * can use it without inventing one.
 */
export function describeStaleness(
  affectedFiles: readonly string[] | null | undefined,
  stalenessScore: number | null | undefined,
  status?: string | null,
): DecisionStaleness {
  const fileCount = affectedFiles?.length ?? 0;
  const score = Number.isFinite(stalenessScore) ? (stalenessScore as number) : 0;

  if (status != null && !RESCORED_STATUSES.has(status)) {
    return {
      kind: "unscored",
      fileCount,
      changedCount: 0,
      sentence: `Staleness is only recomputed for active and proposed records, so it was not measured for this ${status} one.`,
      short: "not scored",
    };
  }

  if (fileCount === 0) {
    return {
      kind: "unscoped",
      fileCount: 0,
      changedCount: 0,
      sentence:
        "This record names no files, so whether the code moved underneath it cannot be checked.",
      short: "no files",
    };
  }

  // "None of its 1 file have changed" is what a template gets you when the
  // singular case is left to a plural suffix. One file is a different
  // sentence, not the same sentence with an `s` removed.
  const one = fileCount === 1;

  if (score <= 0) {
    return {
      kind: "unchanged",
      fileCount,
      changedCount: 0,
      sentence: one
        ? "The one file it names is still tracked and unchanged since it was recorded."
        : `All ${fileCount} files it names are still tracked and unchanged since it was recorded.`,
      short: `0 of ${fileCount}`,
    };
  }

  // A proportion rounds to zero on a wide record that moved by one file, and
  // "0 of 300 have changed" under a non-zero score is the kind of arithmetic
  // that costs a reader their trust in every other number on the page. Floor
  // at one, ceiling at the scope.
  const changedCount = Math.min(
    fileCount,
    Math.max(1, Math.round(score * fileCount)),
  );

  const verb = changedCount === 1 ? "has" : "have";
  const be = changedCount === 1 ? "is" : "are";

  return {
    kind: "moved",
    fileCount,
    changedCount,
    sentence: one
      ? "The one file it names has changed since it was recorded, or is no longer tracked, so it may no longer describe the code."
      : `${changedCount} of its ${fileCount} files ${verb} changed since it was recorded, or ${be} no longer tracked, so parts of it may no longer describe the code.`,
    short: `${changedCount} of ${fileCount}`,
  };
}

/** Convenience for callers holding a whole record. */
export function describeRecordStaleness(
  decision: Pick<
    DecisionRecord,
    "affected_files" | "staleness_score" | "status"
  >,
): DecisionStaleness {
  return describeStaleness(
    decision.affected_files,
    decision.staleness_score,
    decision.status,
  );
}
