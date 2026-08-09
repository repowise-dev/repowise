---
title: CLI Config Warning Surfacing - Plan
type: fix
date: 2026-08-09
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

## Goal Capsule

Fix issue #852: configuration errors and minor faults swallowed silently during CLI init/update/search now surface as explicit user-facing warnings. The fix targets the highest-user-impact swallow sites ranked in research: the embedder mock fallback (init/update/generation), the update vector-store build, the full-rescore env interval, init's index-only decision-provider path, and search's semantic fallback. Authority: issue #852; no prior plan exists. Stop conditions: every target swallow site emits a warning visible in default (non-verbose) CLI output; existing tests pass; no exit-code or config-schema changes.

## Product Contract

### Summary

Repowise's CLI drops user-relevant configuration faults. A bad `OLLAMA_EMBEDDING_TIMEOUT` value silently swaps semantic search to mock vectors. A failed vector-store build silently disables decision dedup on update. Invalid `REPOWISE_FULL_RESCORE_INTERVAL_DAYS` silently defaults. The CLI's structlog warnings never reach users: `configure_cli_logging()` filters WARNING out unless `-v` is passed. This plan routes the user-relevant swallow sites through the CLI's existing visible-warning mechanisms (rich console `[yellow]Warning:[/yellow]`, the update `degraded` list).

### Problem Frame

Issue #852 (spun out of #847): "There are cases where configuration errors or minor faults are silently swallowed during the update pipeline, preventing users from realizing their configuration is invalid. We need to ensure these surface as explicit CLI warnings." Confirmed instances, all on current main:

- `packages/cli/src/repowise/cli/providers/embedders.py:141-144` — `build_embedder()` catches every exception from `get_embedder()` and returns `MockEmbedder()` with no signal. `_embedder_kwargs()` (lines 24, 26) raises `ValueError` for `OLLAMA_EMBEDDING_DIMS`/`OLLAMA_EMBEDDING_TIMEOUT` non-numeric values, landing in that silent fallback. `resolve_embedder_for_repo()` (lines 122-125) swallows config-load failures to `pinned = None` with no signal.
- `packages/cli/src/repowise/cli/commands/update_cmd/incremental.py:26-32` — `_build_update_vector_store()` returns `None` on any failure; the decision upsert then runs without a vector store, silently losing semantic dedup/supersession.
- `packages/cli/src/repowise/cli/commands/update_cmd/persistence.py:1066-1073` — `_full_rescore_interval_days()` swallows `ValueError` from a malformed `REPOWISE_FULL_RESCORE_INTERVAL_DAYS` and silently uses the 7-day default.
- `packages/cli/src/repowise/cli/commands/init_cmd/command.py:1123-1140` — the index-only branch swallows `resolve_provider()` failure for the decision-extraction provider with `except Exception: pass`; decision extraction silently never runs.
- `packages/cli/src/repowise/cli/commands/search_cmd.py:167-168` — semantic search failure is swallowed (`except Exception: pass`) and the command silently falls back to full-text search.

The CLI already has the right mechanisms; the swallow sites simply do not use them: `helpers.py:770-776` renders `[yellow]Warning:[/yellow]` via `err_console` (canonical spelling); the update path collects `degraded` entries (`update_cmd/command.py:1085`) rendered by `reporting.py:161-178` and carried in `--progress json` `done` events. The server MCP layer already solves the embedder case with an `_embedder_status` envelope (`mcp_server/_server.py:169-222`, consumers `_meta.py:331-355`) — the CLI side never received the same treatment (issue #826/#324 fixed only the server path).

### Requirements

**Embedder fallback transparency**

- R1. `build_embedder()` must record the failure reason whenever it falls back to `MockEmbedder` for a requested non-mock embedder, so call sites can surface it.
- R2. `_embedder_kwargs()` must name the offending environment variable in the error it raises (e.g. `OLLAMA_EMBEDDING_TIMEOUT must be a number, got 'abc'`).
- R3. Every CLI command that requests an embedder and receives a degraded (mock) embedder must emit one visible `Warning:` line naming the embedder and the reason, without changing behavior or exit codes.

**Update pipeline visibility**

- R4. `update` must append a `degraded` entry when the decision vector store fails to build, so the failure rides the existing completion panel and `--progress json` `done` event.
- R5. `update` must emit one visible warning when `REPOWISE_FULL_RESCORE_INTERVAL_DAYS` is not a valid number, naming the variable and the fallback.

**Init and search**

- R6. `init --index-only` must emit one visible warning when the decision-extraction provider cannot be resolved, instead of silently skipping decision extraction.
- R7. `search` must emit one visible warning when semantic search fails, before falling back to full-text search.

**Cross-cutting**

- R8. No warning may change exit codes, config parsing, or embedder selection; warnings are additive.
- R9. Warnings must be visible in default CLI output (no `-v`), consistent with the existing `[yellow]Warning:[/yellow]` spelling.

### Scope Boundaries

In scope: CLI-facing surfaces listed in R1-R7, tests, and the `docs/reference/CONFIG.md` env-var notes. Out of scope: core pipeline internals (`packages/core/src/repowise/core/pipeline/*` swallow sites — server-side and library consumers own their own surfacing), structlog-only sites in `persist.py`/`resume/controller.py` (deliberate best-effort by design), the server MCP embedder envelope (already shipped as #324), exit-code changes, and new configuration keys.

### Sources

- Issue #852 (origin, spun from #847).
- Issue #826/#324 fix (server-side `_embedder_status` pattern at `packages/server/src/repowise/server/mcp_server/_server.py:169-222`) — the model for the CLI embedder status.
- `packages/cli/src/repowise/cli/_setup.py:35-38` — structlog WARNING is filtered out in default mode; the reason console warnings are required.
- `packages/cli/src/repowise/cli/commands/update_cmd/reporting.py:161-178` — `degraded` list rendering.

## Planning Contract

### Key Technical Decisions

- KTD1. **Warning mechanism: rich console, not structlog.** Use the existing `console`/`err_console` singletons (`helpers.py:46-47`) with the canonical `[yellow]Warning:[/yellow] {text}` spelling (`helpers.py:773`). Rationale: structlog WARNING events are filtered out by `configure_cli_logging()` unless `-v` is set (`_setup.py:35-38`), so they never satisfy R9. For the update path, append to the existing `degraded: list[str]` (`update_cmd/command.py:1085`) so failures also ride the completion panel and `--progress json` `done` event (R4).
- KTD2. **Embedder degradation reason rides on the returned instance.** `build_embedder()` keeps its return type (a single embedder object) and attaches `fallback_reason: str | None` to the returned `MockEmbedder` instance on failure; a helper (`embedder_degraded_warning(embedder, requested)`) returns the canonical warning line or `None`. Rationale: `build_embedder()` is called deep in generation and decision wiring; changing its signature to return a tuple would ripple through every call site and the server. Attaching a reason to the instance plus a caller-owned print helper mirrors the server `_embedder_status` semantics (KTD1's warning rendering stays at CLI call sites that own UX). R1, R3.
- KTD3. **Warnings are one-shot, not per-call.** Each surface warns at most once per command invocation (init header, update degraded entry, search fallback), never inside loops. Rationale: R8 — additive visibility; a per-page warning storm in generation would be noise, not signal.

### Assumptions

- A1. `MockEmbedder` instances are plain objects without `__slots__`; setting an attribute on the returned instance is safe. If this fails, use a module-level `_last_embedder_reason` accessor instead.
- A2. The degraded entry for the update vector store follows the existing `degraded.append(f"<Step name>: {exc}")` shape (`update_cmd/command.py:1459`).
- A3. The index-only decision-provider site (`init_cmd/command.py:1123-1140`) keeps its fallback behavior (index-only proceeds); only the warning is added.

### Sequencing

U1 (embedder reason + env naming) precedes U2 (init/update call sites consume it) and U3 (search consumes it). U4 (docs) is independent and can land last.

## Implementation Units

### U1. Embedder fallback reason and env-var validation

- Goal: make embedder degradation observable (R1, R2).
- Requirements: R1, R2, R3 (partial — mechanism only).
- Files: `packages/cli/src/repowise/cli/providers/embedders.py`, `tests/unit/cli/test_shared_helpers.py`, `tests/unit/cli/test_embedder_resolution.py`.
- Approach:
  - `_embedder_kwargs()`: wrap `int(dimensions)`/`float(timeout)` conversions so a `ValueError` re-raises with the variable name and raw value; stop converting silently.
  - `build_embedder()`: on exception, `embedder = MockEmbedder(); embedder.fallback_reason = f"{embedder_name_resolved}: {exc}"`; return it. The `"mock"` fast path and success path leave `fallback_reason` `None` (use `setattr`-safe pattern; if MockEmbedder defines `__slots__`, store on the instance via a `fallback_reason` attribute added to `repowise.core.providers.embedding.base.MockEmbedder` instead).
  - `resolve_embedder_for_repo()`: on config-load failure, keep `pinned = None` but attach the failure to the same surfacing path (e.g. set the module-level `_last_embedder_reason` used by the helper) so a corrupt `config.yaml` warns instead of silently unpinning.
  - Add `embedder_degraded_warning(embedder, requested) -> str | None` returning `f"[yellow]Warning:[/yellow] {requested} embedder unavailable — falling back to mock: {reason}"` (or `None`).
- Test scenarios:
  1. `OLLAMA_EMBEDDING_TIMEOUT=abc` → `build_embedder("ollama")` returns `MockEmbedder` with a `fallback_reason` mentioning `OLLAMA_EMBEDDING_TIMEOUT` and the value `abc`.
  2. `OLLAMA_EMBEDDING_DIMS=abc` → same, mentioning `OLLAMA_EMBEDDING_DIMS`.
  3. Unknown embedder name → fallback reason mentions the name.
  4. `embedder_name_resolved == "mock"` and successful build → `fallback_reason is None`; `embedder_degraded_warning` returns `None`.
  5. Existing pinned test `test_build_embedder_falls_back_to_mock` (`test_shared_helpers.py:72-77`) keeps passing or is updated to assert the reason field.
- Verification: `pytest tests/unit/cli/test_shared_helpers.py tests/unit/cli/test_embedder_resolution.py`.

### U2. Init and update warnings

- Goal: surface embedder and pipeline degradations in init and update (R3, R4, R5, R6).
- Requirements: R3, R4, R5, R6.
- Files: `packages/cli/src/repowise/cli/commands/init_cmd/command.py`, `packages/cli/src/repowise/cli/commands/update_cmd/incremental.py`, `packages/cli/src/repowise/cli/commands/update_cmd/persistence.py`, `packages/cli/src/repowise/cli/commands/update_cmd/command.py`, tests: `tests/unit/cli/test_update_e2e.py`, `tests/unit/cli/test_init_ux.py`.
- Approach:
  - Init (`init_cmd/command.py`): where the header prints the embedder name, call `embedder_degraded_warning()` and render via `console.print` when non-`None` (KTD3: once per run). In the index-only branch (lines 1123-1140), replace `except Exception: pass` with a single `console.print("[yellow]Warning:[/yellow] Decision extraction unavailable — <reason>")`.
  - Update (`update_cmd/incremental.py`): `_build_update_vector_store(repo_path, cfg, degraded)` — add the `degraded` list parameter; on exception append `f"Decision vector store: {exc}"` (mirroring `command.py:1459` shape); call site `command.py:1468` passes `degraded`. No other behavior change.
  - Update (`update_cmd/persistence.py`): in `_full_rescore_interval_days()`, on `ValueError` print one `[yellow]Warning:[/yellow] REPOWISE_FULL_RESCORE_INTERVAL_DAYS must be a number (got '<raw>'); using 7-day default` via `console` (module already imports the shared console) — KTD3: at most once; simplest correct place is the except branch itself (called once per run).
  - Update (`update_cmd/command.py`): where the update builds/uses an embedder, emit the degraded warning once via the helper when the requested embedder is non-mock.
- Test scenarios:
  1. Update with `OLLAMA_EMBEDDING_TIMEOUT=abc` and `--embedder ollama` → `result.output` contains `Warning:` and `OLLAMA_EMBEDDING_TIMEOUT` (CliRunner pattern from `test_update_e2e.py:229-255`).
  2. `_build_update_vector_store` failure (monkeypatch `build_vector_store` to raise) → completion output contains `degraded step(s)` and the step name (pattern from `test_update_degrades_visibly_when_a_step_fails`).
  3. `REPOWISE_FULL_RESCORE_INTERVAL_DAYS=abc` → output contains `Warning:` and the variable name; `=7` keeps working (no warning).
  4. Init index-only with a provider that fails to resolve → output contains `Warning:` and `Decision extraction`; exit code unchanged.
- Verification: `pytest tests/unit/cli/test_update_e2e.py tests/unit/cli/test_init_ux.py`.

### U3. Search semantic fallback warning

- Goal: search warns when semantic search degrades to full-text (R7).
- Requirements: R7.
- Files: `packages/cli/src/repowise/cli/commands/search_cmd.py`, `tests/unit/cli/test_search_cmd.py` (or the existing search test module — locate it during implementation).
- Approach: in `_search_semantic` (lines ~155-175), replace `except Exception: pass` with capture of `exc`; print once per command run: `[yellow]Warning:[/yellow] Semantic search unavailable — <exc>; using full-text search.` — then the existing FTS fallback proceeds unchanged.
- Test scenarios:
  1. Semantic path raising (monkeypatch `LanceDBVectorStore.search`) → output contains `Warning:` and `full-text`.
  2. Semantic success → no warning in output.
- Verification: `pytest tests/unit/cli -k search`.

### U4. Documentation notes

- Goal: document that misconfigured embedder/rescore env vars produce warnings (R9 consistency).
- Files: `docs/reference/CONFIG.md` (env-var table lines ~628-638), `docs/CHANGELOG.md`.
- Approach: add one line to the `OLLAMA_EMBEDDING_TIMEOUT`/`REPOWISE_FULL_RESCORE_INTERVAL_DAYS` entries noting the CLI warns on invalid values; add a CHANGELOG entry under "Fixed" referencing #852.
- Test scenarios: none (docs-only); verify rendered markdown reads correctly.
- Verification: `git diff --stat` review; markdown lint if configured.

## Verification Contract

- Unit: `pytest tests/unit/cli` — the full CLI suite (embedder, update, init, search tests).
- Targeted: the commands listed under each unit.
- Lint: `ruff check packages/cli` (repo uses ruff; check `Makefile` for the exact invocation).
- Type: `mypy packages/cli` only if configured in the repo's CI (check `pyproject.toml`); otherwise skip.
- Behavior protection: warnings are additive — assert no existing CLI test changes exit codes; existing swallow-site tests (e.g. `test_build_embedder_falls_back_to_mock`) are updated to assert the reason field rather than removed.

## Definition of Done

- Global: R1-R9 hold; full `pytest tests/unit/cli` passes; `ruff check` clean; no exit-code or config-schema changes; no dead-end code left in the diff.
- Per unit: each unit's test scenarios pass with the targeted test commands listed above.
- Cleanup: no temporary instrumentation, print statements beyond the intended warnings, or unused helpers remain.
