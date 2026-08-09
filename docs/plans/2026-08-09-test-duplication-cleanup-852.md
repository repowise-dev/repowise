---
title: Test Duplication Cleanup for #852 Findings - Plan
type: refactor
date: 2026-08-09
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

## Goal Capsule

Resolve the repowise-bot code-health findings on PR #1373: all 8 introduced findings are duplication inside `tests/unit/cli/test_852_config_warnings.py` (7 "duplicated assertion block" findings plus one "dry violation" reporting 62% of the file duplicated). Collapse the repeated CLI-invocation-and-exit-code assertion pattern into one local helper so the within-file duplication drops below the bot's threshold. Test-only refactor; no production behavior changes.

## Product Contract

### Summary

The bot report for PR #1373 lists 8 introduced findings, all in `tests/unit/cli/test_852_config_warnings.py`:

- `:215` — dry violation: "62% of file duplicated; worst clone shares 17 lines" (same file)
- `:83`, `:167`, `:185`, `:217`, `:237`, `:267`, `:314` — "assertion block at lines X-Y is duplicated" (same file)

Every flagged block is the same shape:

```python
    result = CliRunner().invoke(cli, [...])
    assert result.exit_code == 0, result.output
    assert "<fragment>" in result.output
```

The invocation args and asserted fragments differ per test; the `CliRunner().invoke` + `assert exit_code == 0` pair is identical across all tests.

### Requirements

- R1. The repeated `CliRunner().invoke(...)` + `assert result.exit_code == 0, result.output` pair must exist exactly once in `tests/unit/cli/test_852_config_warnings.py`.
- R2. Each test keeps its own invocation args and its own output-fragment assertions; only the shared mechanics are extracted.
- R3. Test behavior is unchanged: every test still asserts exit code 0 and the same output fragments; no test is removed or weakened.
- R4. No production code changes.

### Scope Boundaries

In scope: `tests/unit/cli/test_852_config_warnings.py` only. Out of scope: the `_git`/`_make_git_repo`/`_index_full` helper block (mirrored in five other CLI test modules by established convention — pre-existing pattern, not introduced by this PR); the other test modules' own duplication; production code.

## Planning Contract

### Key Technical Decisions

- KTD1. **One local `_invoke_ok` helper, not cross-module imports.** Add `def _invoke_ok(args: list[str]) -> Result` that runs `CliRunner().invoke(cli, args)` and asserts `result.exit_code == 0, result.output`, returning the result. Rationale: the repo's CLI-test convention is per-file helpers (six modules define their own `_make_git_repo`); importing helpers from `test_update_e2e` would set a new precedent and couple modules. R1, R3.
- KTD2. **Keep per-test fragment assertions inline.** Each test still asserts its specific fragments (`"degraded step(s)"`, `"OLLAMA_EMBEDDING_TIMEOUT"`, etc.) after `_invoke_ok`. Rationale: the fragments are the test's actual claims; folding them into the helper would turn it into a magic string bag and weaken failure messages. R2.

### Assumptions

- A1. The bot's duplication metric re-scans on the next PR update; the extraction is expected to clear all 8 findings.

### Sequencing

Single unit; no dependencies.

## Implementation Units

### U1. Extract the shared invoke-and-assert helper

- Goal: collapse the repeated invocation/exit-code assertion into one helper (R1, R2).
- Requirements: R1, R2, R3.
- Files: `tests/unit/cli/test_852_config_warnings.py`.
- Approach:
  - Add, next to the existing helpers block:

    ```python
    def _invoke_ok(args: list[str]) -> Result:
        """Run the CLI and fail the test unless it exits 0."""
        from click.testing import Result

        result = CliRunner().invoke(cli, args)
        assert result.exit_code == 0, result.output
        return result
    ```

    (`Result` is imported at module top; adjust imports accordingly.)
  - Replace every `result = CliRunner().invoke(cli, [...])` + `assert result.exit_code == 0, result.output` pair with `result = _invoke_ok([...])`, keeping the subsequent fragment assertions verbatim.
- Test scenarios:
  1. All existing tests still pass with identical assertions (`pytest tests/unit/cli/test_852_config_warnings.py`).
  2. The `_invoke_ok` failure path still reports the CLI output in the assertion message (a deliberate failing test is NOT added — the existing `exit_code == 0, result.output` message shape is preserved by construction).
- Verification: `uv run pytest tests/unit/cli/test_852_config_warnings.py`; `uv run ruff check tests/unit/cli/test_852_config_warnings.py`.

## Verification Contract

- `uv run pytest tests/unit/cli/test_852_config_warnings.py` — all tests pass, count unchanged (14).
- `uv run ruff check tests/unit/cli/test_852_config_warnings.py` — clean.
- Full CLI suite sanity: `uv run pytest tests/unit/cli -q` (the only changed file is the test module).
- Bot re-check: the repowise-bot comment on PR #1373 should no longer list the 8 duplication findings after the next index.

## Definition of Done

- Global: R1-R4 hold; `test_852_config_warnings.py` passes with the same test count; ruff clean; no production files touched; no dead code left (the helper is used by every test).
- Per unit: U1's verification commands pass.
- Cleanup: no leftover duplicate assertion blocks remain in the file.
