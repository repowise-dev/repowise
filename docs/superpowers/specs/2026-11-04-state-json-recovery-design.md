# State JSON Recovery Design

**Goal:** Make corrupted, non-object, empty, and missing `.repowise/state.json` files recoverable without crashing commands, while keeping `repowise doctor` diagnostic and actionable.

## Context

`load_state()` is shared by many CLI commands. It currently parses `state.json` directly, so malformed JSON escapes from the helper and aborts `doctor`, `init`, and `update`. When the file is missing, doctor reports a generic failure without a repair command. The fix must preserve the existing dictionary-returning API for normal callers.

## Design

Add a state-reading helper that returns a structured result containing the parsed mapping, file status, and an optional error string. It distinguishes:

- missing file;
- valid JSON object, including an empty object;
- invalid JSON, invalid UTF-8, or a JSON value whose top level is not an object;
- other operating-system errors, which continue to propagate because they indicate a permissions or filesystem problem rather than recoverable state corruption.

Keep `load_state(repo_path)` as the compatibility wrapper. It uses the structured reader and returns the mapping for a valid object, or `{}` for missing/corrupt state. Existing callers therefore retain their current contract and do not need a broad refactor.

Update the doctor state row to consume the structured result. Missing state reports a missing file, while malformed/non-object state reports an unreadable file with the path and parse/type reason. Both failures carry `fix="repowise init"`. A valid empty object remains a failed empty-state row but is also actionable with the same fix. A valid non-empty object keeps the current `last_sync` detail.

## Error Handling

Only parse and decoding failures, plus non-object JSON, are downgraded to recoverable state corruption. `OSError` from reading the file propagates so doctor and other commands do not hide permission or filesystem failures.

## Tests

Add state-helper tests for missing, valid object, empty object, malformed JSON, invalid UTF-8, non-object JSON, and propagated OS errors. Add doctor tests asserting the row detail, failure level, stable id, and `repowise init` fix for missing, empty, and corrupt state files. Preserve existing healthy-state and no-index behavior.
