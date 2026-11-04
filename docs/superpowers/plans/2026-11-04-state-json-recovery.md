# State JSON Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover gracefully from missing or corrupt `.repowise/state.json`, and make doctor report the cause with an actionable `repowise init` fix.

**Architecture:** Add a structured reader in the state helper module, preserving `load_state(repo_path) -> dict` as a compatibility wrapper. Doctor consumes the structured result to distinguish missing, empty, and unreadable state without changing its JSON row schema.

**Tech Stack:** Python 3.11+, pytest, Click `CliRunner`.

---

### Files

- Modify `packages/cli/src/repowise/cli/_helpers/state.py`: define the typed read result and structured reader; keep `load_state` as a wrapper.
- Modify `packages/cli/src/repowise/cli/helpers.py`: re-export the structured reader for doctor, following the existing helper exports.
- Modify `packages/cli/src/repowise/cli/commands/doctor_cmd/_checks/core.py`: render distinct state failures and attach the init fix.
- Modify `tests/unit/cli/test_helpers.py`: pin helper behavior for state file cases.
- Modify `tests/unit/cli/test_doctor_check_order.py` and `tests/unit/cli/test_doctor_check_ids.py`: pin doctor row and JSON payload behavior.

### Task 1: Pin structured state-read behavior

**Files:** `tests/unit/cli/test_helpers.py`

- [x] Add tests for missing, valid object, empty object, malformed JSON, invalid UTF-8, and non-object JSON. Assert the structured reader reports the corresponding status and that `load_state()` retains its dict-only contract (`{}` for absent or corrupt state).
- [x] Add a test that patches `Path.read_text` to raise `PermissionError` and asserts the structured reader and `load_state()` propagate the OS error.
- [x] Run `uv run pytest tests/unit/cli/test_helpers.py::TestStateFile ... -q`; the regression tests first failed because `read_state` was missing, then passed after implementation.

### Task 2: Implement the compatibility reader

**Files:** `packages/cli/src/repowise/cli/_helpers/state.py`, `packages/cli/src/repowise/cli/helpers.py`

- [x] Define a small immutable result type with `state`, `status`, and optional `error` fields. Use statuses `ok`, `missing`, `empty`, and `unreadable`.
- [x] Implement one read path: catch `FileNotFoundError` as `missing`; catch JSON/UTF-8 decode errors and non-dict JSON as `unreadable`; return `empty` for `{}`; propagate other `OSError` exceptions.
- [x] Implement `load_state()` by returning the structured result's mapping, which is `{}` for missing/unreadable files.
- [x] Re-export the structured reader from `repowise.cli.helpers`.
- [x] Run the state helper regression tests and confirm all cases pass.

### Task 3: Make doctor state failures actionable

**Files:** `tests/unit/cli/test_doctor_check_order.py`, `tests/unit/cli/test_doctor_check_ids.py`, `packages/cli/src/repowise/cli/commands/doctor_cmd/_checks/core.py`

- [x] Add doctor-row tests for missing, empty, malformed, and non-object state. Assert `id == "state-json"`, `level == "fail"`, and `fix == "repowise init"`; assert missing and unreadable details differ, and unreadable detail includes the path and reason.
- [x] Add a `doctor --format json` regression test for corrupt state. Parse the command output as JSON and assert the state row includes the unreadable detail and init fix.
- [x] Run the new doctor tests and confirm they first fail on the missing structured reader / old row behavior.
- [x] Update `_state_row()` to consume the structured read result. Keep the healthy `last_sync` detail unchanged, report missing and unreadable distinctly, report empty as empty, and attach `fix="repowise init"` to each unsuccessful state row.
- [x] Run focused doctor tests and confirm the row behavior passes.

### Task 4: Verify existing caller compatibility

- [x] Run the focused helper and doctor suites: 51 passed.
- [ ] Run the CLI package unit suite with `uv run pytest tests/unit/cli -q` to cover existing `load_state()` consumers, including init/update paths. Attempted, but stopped at 66% after failures/errors appeared in unrelated test modules.
- [x] Run the changed-file Ruff check; all checks passed.
- [x] Run `git diff --check`; no whitespace errors.

