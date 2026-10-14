/**
 * Change Records: one merged change (PR, MR, Gerrit change or lone commit)
 * reconstructed from git, as the native versioned JSON record.
 *
 * Generated from the server's response models
 * (`packages/server/src/repowise/server/schemas/changes.py`), so the wire
 * shape cannot drift. `provenance` is null while AI attribution is off for
 * the repository; forge metadata fields are null when unknown, never empty.
 *
 * `ChangeEvidence` is the record's evidence checklist (twin of
 * `repowise.core.changes.evidence`): one `EvidenceItem` per kind in
 * `EVIDENCE_KINDS`, each with a `status` and the `basis` it rests on. The
 * risk score is one item, never the headline.
 */
export type {
  ChangeAuthor,
  ChangeEvidence,
  ChangeFile,
  ChangeForgeMetadata,
  ChangeIdentity,
  ChangeListResponse,
  ChangeProvenance,
  ChangeRecord,
  ChangeSummary,
  EvidenceItem,
} from "./generated/http.js";

/** Bumped when an evidence field is renamed or removed or changes meaning. */
export const EVIDENCE_SCHEMA_VERSION = 1;

/** Every item a checklist carries, in order. */
export const EVIDENCE_KINDS = [
  "components_touched",
  "owners",
  "suggested_reviewers",
  "reaching_tests",
  "contracts",
  "cross_repo_consumers",
  "decisions_crossed",
  "health_delta",
  "provenance",
  "risk_score",
] as const;
export type EvidenceKind = (typeof EVIDENCE_KINDS)[number];
