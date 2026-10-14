/**
 * Change Records: one merged change (PR, MR, Gerrit change or lone commit)
 * reconstructed from git, as the native versioned JSON record.
 *
 * Generated from the server's response models
 * (`packages/server/src/repowise/server/schemas/changes.py`), so the wire
 * shape cannot drift. `provenance` is null while AI attribution is off for
 * the repository; forge metadata fields are null when unknown, never empty.
 */
export type {
  ChangeAuthor,
  ChangeFile,
  ChangeForgeMetadata,
  ChangeIdentity,
  ChangeListResponse,
  ChangeProvenance,
  ChangeRecord,
  ChangeSummary,
} from "./generated/http.js";
