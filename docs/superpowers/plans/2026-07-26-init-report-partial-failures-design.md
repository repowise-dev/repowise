# Init Partial-Failure Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `repowise init` clearly report page-generation failures and the recovery command while preserving its successful exit status and zero-failure output.

**Architecture:** The generation phase already writes the current run's failed page IDs to its `JobSystem` checkpoint. Add an optional completion callback to `run_generation`; after `PageGenerator.generate_all` returns, it reads that run's checkpoint and sends the failed IDs to the CLI generation wrapper. The wrapper formats the IDs by their `page_type:` prefix and prints one warning only when the callback reports failures.

**Tech Stack:** Python 3.11+, Click/Rich CLI, pytest, existing JSON-backed `JobSystem`.

---

### Task 1: Expose the current generation run's failure IDs

**Files:**
- Modify: `packages/core/src/repowise/core/pipeline/phases/generation.py:22-156`
- Test: `tests/unit/cli/test_generation_persist.py`

- [ ] **Step 1: Write the failing pipeline callback test**

Add a focused async test that supplies a completion callback to `run_generation`, uses a provider that raises for one page, and asserts the callback receives the failed page ID after generation returns.

```python
failed: list[str] = []

pages = await run_generation(
    ...,
    on_generation_complete=failed.extend,
)

assert pages
assert failed == ["file_page:broken.py"]
```

- [ ] **Step 2: Run the focused test and confirm it fails because the callback is unsupported**

Run: `uv run pytest tests/unit/cli/test_generation_persist.py -q`

Expected: failure reporting that `run_generation()` does not accept `on_generation_complete`.

- [ ] **Step 3: Add the callback without altering generation results**

Extend `run_generation` with `on_generation_complete: Callable[[list[str]], None] | None = None`. After `generate_all` returns, read `job_system.get_checkpoint(...)` for this run and invoke the callback with a copied `failed_page_ids` list. Keep the existing return value as `list[GeneratedPage]` and treat callback failures as non-fatal.

```python
if on_generation_complete is not None:
    try:
        jobs = job_system.list_jobs()
        failed_page_ids = list(jobs[0].failed_page_ids) if jobs else []
        on_generation_complete(failed_page_ids)
    except Exception as exc:
        logger.debug("generation.failure_summary_callback_failed", error=str(exc))
```

- [ ] **Step 4: Run the focused test and confirm it passes**

Run: `uv run pytest tests/unit/cli/test_generation_persist.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the pipeline callback**

```bash
git add packages/core/src/repowise/core/pipeline/phases/generation.py tests/unit/cli/test_generation_persist.py
git commit -m "feat(generation): expose failed page IDs to callers"
```

### Task 2: Render the init warning from the pipeline failure summary

**Files:**
- Modify: `packages/cli/src/repowise/cli/commands/init_cmd/generation.py:184-297`
- Test: `tests/unit/cli/test_generation_persist.py`

- [ ] **Step 1: Write failing formatter tests**

Add tests for a small formatter that verifies: (a) an empty ID list produces no output, and (b) `file_page:src/a.py`, `module_page:core`, and `file_page:src/b.py` produce a warning containing `3 pages failed`, `file_page (2)`, `module_page (1)`, `incomplete`, and `repowise init --resume`.

```python
assert _format_generation_failure_summary([]) is None

summary = _format_generation_failure_summary(
    ["file_page:src/a.py", "module_page:core", "file_page:src/b.py"]
)
assert "3 pages failed" in summary
assert "file_page (2)" in summary
assert "module_page (1)" in summary
assert "repowise init --resume" in summary
```

- [ ] **Step 2: Run the formatter tests and confirm they fail because the formatter is absent**

Run: `uv run pytest tests/unit/cli/test_generation_persist.py -q`

Expected: import or attribute failure for `_format_generation_failure_summary`.

- [ ] **Step 3: Implement warning formatting and wire the callback**

Add `_format_generation_failure_summary(failed_page_ids: list[str]) -> str | None`, grouping each ID on its first `:` and sorting types alphabetically. In `run_repo_generation`, register the callback before calling `run_generation_with_persistence`; after the progress context closes and `result.generated_pages` is assigned, print the warning only if the formatted summary is non-empty.

```python
failure_summary = _format_generation_failure_summary(failed_page_ids)
if failure_summary:
    console.print(f"[yellow]{failure_summary}[/yellow]")
```

The warning must say the wiki is incomplete and name `repowise init --resume`; it must not change the command's exit status or print on a zero-failure run.

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run: `uv run pytest tests/unit/cli/test_generation_persist.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the CLI reporting change**

```bash
git add packages/cli/src/repowise/cli/commands/init_cmd/generation.py tests/unit/cli/test_generation_persist.py
git commit -m "fix(init): report partial page generation failures"
```

### Task 3: Verify the contribution end-to-end

**Files:**
- Verify: `packages/core/src/repowise/core/pipeline/phases/generation.py`
- Verify: `packages/cli/src/repowise/cli/commands/init_cmd/generation.py`
- Verify: `tests/unit/cli/test_generation_persist.py`

- [ ] **Step 1: Run the focused tests**

Run: `uv run pytest tests/unit/cli/test_generation_persist.py -q`

Expected: PASS with no failures.

- [ ] **Step 2: Run generation checkpoint regression tests**

Run: `uv run pytest tests/unit/generation/test_job_system.py -q`

Expected: PASS with no failures.

- [ ] **Step 3: Run static checks on changed files**

Run: `uv run ruff check packages/core/src/repowise/core/pipeline/phases/generation.py packages/cli/src/repowise/cli/commands/init_cmd/generation.py tests/unit/cli/test_generation_persist.py`

Expected: `All checks passed!`.

- [ ] **Step 4: Inspect the final change**

Run: `git diff origin/main --check && git diff origin/main --stat`

Expected: no whitespace errors; only the planned pipeline, CLI, test, and plan files change.

- [ ] **Step 5: Commit the implementation plan**

```bash
git add docs/superpowers/plans/2026-07-26-init-report-partial-failures-design.md
git commit -m "docs: plan init partial-failure reporting"
```
