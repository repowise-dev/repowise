"""One workspace update pass: select stale members, update them in parallel, run the hooks."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

# The shared single-flight guards: ``update_single_repo_index`` bypasses the CLI
# lock acquisition, so the workspace pass takes the per-repo lock itself.
from repowise.core.update_lock import (
    lock_age_seconds as _lock_age_seconds,
)
from repowise.core.update_lock import (
    release_update_lock as _release_lock,
)
from repowise.core.update_lock import (
    release_workspace_lock as _release_workspace_lock,
)
from repowise.core.update_lock import (
    try_acquire_update_lock as _try_acquire_lock,
)
from repowise.core.update_lock import (
    update_workspace_lock as _try_acquire_workspace_lock,
)

from ...docs_mode import docs_mode_state_fields
from ...ingestion.change_detector import has_working_tree_changes
from ...ingestion.git_indexer import (
    GIT_HISTORY_VERSION,
    GIT_HISTORY_VERSION_KEY,
    git_history_stale,
)
from ...ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT
from ..config import RepoEntry, WorkspaceConfig
from .phases import analysis_config_changed, run_cross_repo_hooks
from .repo import RepoUpdateResult, _has_persisted_repo_index, update_single_repo_index
from .state import (
    check_repo_staleness,
    clear_stale_update_pending,
    read_repo_state,
    reconcile_repo_head_commit,
    stamp_workspace_entry,
    stored_index_counts,
    sync_workspace_state_from_disk,
)

_log = logging.getLogger("repowise.workspace.update")


_MAX_CONCURRENT_UPDATES = 4


class _StaleRepo(NamedTuple):
    """A workspace member selected for this update pass."""

    alias: str
    path: Path
    new_head: str
    first_time: bool


@dataclass
class _RepoUpdatePass:
    """What every per-repo update in one workspace pass shares."""

    ws_config: WorkspaceConfig
    semaphore: asyncio.Semaphore
    commit_depth: int
    exclude_patterns: list[str] | None
    include_working_tree: bool
    on_repo_start: Callable[[str], None] | None
    on_repo_done: Callable[[RepoUpdateResult], None] | None


async def update_workspace(
    workspace_root: Path,
    ws_config: WorkspaceConfig,
    *,
    repo_filter: str | None = None,
    only_aliases: set[str] | None = None,
    run_hooks: bool = True,
    dry_run: bool = False,
    commit_depth: int = _DEFAULT_COMMIT_LIMIT,
    exclude_patterns: list[str] | None = None,
    include_working_tree: bool = False,
    restamp_up_to_date: bool = True,
    on_repo_start: Callable[[str], None] | None = None,
    on_repo_done: Callable[[RepoUpdateResult], None] | None = None,
) -> list[RepoUpdateResult]:
    """Update stale repos in the workspace (index-only).

    Args:
        workspace_root: Path to the workspace root directory.
        ws_config: Loaded workspace configuration.
        repo_filter: If set, only update this repo alias.
        only_aliases: If set, restrict the run to this subset of aliases (on
            top of ``repo_filter``). The CLI uses this to hand the fast
            index-only path just the repos it isn't updating via the
            single-repo docs path, so the two orchestrators never touch the
            same repo in one workspace run.
        run_hooks: When False, skip the cross-repo analysis hooks at the end.
            The CLI sets this so it can run them once over the union of
            index-only and docs repos, instead of on a partial set here.
        dry_run: If True, detect staleness but don't actually update.
        commit_depth: Max commits to analyze per file.
        exclude_patterns: Gitignore-style patterns to exclude.
        include_working_tree: Count uncommitted work as staleness, and index
            it. Off for every commit-anchored caller (hooks, webhooks, a
            manual sync); ``repowise watch --workspace`` sets it, because a
            repo it is watching normally has no new commits at all.
        restamp_up_to_date: Advance each up-to-date member's DB freshness
            stamp. The CLI turns it off: it has already restamped the members
            whose fingerprint moved, and a restamp here would rewrite every
            member's DB, and so its editor files, on every change.
        on_repo_start: Called with alias when a repo update begins.
        on_repo_done: Called with result when a repo update finishes.

    Returns:
        List of :class:`RepoUpdateResult` for each repo.
    """
    results: list[RepoUpdateResult] = []
    entries = _entries_for_run(ws_config, repo_filter, only_aliases)

    # A child repo may have been updated on its own; read its state.json first.
    sync_workspace_state_from_disk(workspace_root, ws_config)

    stale_repos = await _select_stale_repos(
        workspace_root, entries, include_working_tree, results, restamp_up_to_date
    )
    # An edited contracts: or conformance: block is work too, with no repo stale.
    if dry_run or not (
        stale_repos or (run_hooks and analysis_config_changed(ws_config, workspace_root))
    ):
        return results

    # Workspace single-flight: concurrent hook-triggered passes defer to the
    # one holding this lock by leaving a pending marker per stale repo, so the
    # running pass rolls forward instead of a second full pass starting.
    workspace_owner = _try_acquire_workspace_lock(workspace_root)
    if workspace_owner is not None:
        if not stale_repos:
            return results
        return _defer_to_running_workspace_update(workspace_owner, stale_repos)

    # The workspace lock is held until every member has been attempted.
    try:
        update_pass = _RepoUpdatePass(
            ws_config=ws_config,
            semaphore=asyncio.Semaphore(_MAX_CONCURRENT_UPDATES),
            commit_depth=commit_depth,
            exclude_patterns=exclude_patterns,
            include_working_tree=include_working_tree,
            on_repo_start=on_repo_start,
            on_repo_done=on_repo_done,
        )
        update_results = await asyncio.gather(
            *[_update_stale_repo(update_pass, repo) for repo in stale_repos],
            return_exceptions=True,
        )
        changed_aliases = _collect_update_results(update_results, results)
        await _finish_workspace_pass(ws_config, workspace_root, changed_aliases, run_hooks)
        return results
    finally:
        _release_workspace_lock(workspace_root)


def _entries_for_run(
    ws_config: WorkspaceConfig,
    repo_filter: str | None,
    only_aliases: set[str] | None,
) -> list[RepoEntry]:
    """The workspace entries this run considers, after both filters."""
    entries = ws_config.repos
    if repo_filter:
        entry = ws_config.get_repo(repo_filter)
        if entry is None:
            available = ", ".join(ws_config.repo_aliases())
            raise ValueError(f"Unknown repo '{repo_filter}'. Available: {available}")
        entries = [entry]

    if only_aliases is not None:
        entries = [e for e in entries if e.alias in only_aliases]
    return entries


async def _select_stale_repos(
    workspace_root: Path,
    entries: list[RepoEntry],
    include_working_tree: bool,
    results: list[RepoUpdateResult],
    restamp_up_to_date: bool = True,
) -> list[_StaleRepo]:
    """Return the stale entries; append a skipped result for every other one."""
    stale_repos: list[_StaleRepo] = []
    for entry in entries:
        abs_path = (workspace_root / entry.path).resolve()
        if not abs_path.is_dir():
            results.append(
                RepoUpdateResult(
                    alias=entry.alias,
                    updated=False,
                    skipped_reason="missing_directory",
                )
            )
            continue

        is_stale, current_head = await _repo_is_stale(abs_path, include_working_tree)
        if not is_stale:
            # Nothing to regenerate, but the DB freshness stamp can still be
            # behind; reconcile it so the server stops reporting "index behind checkout".
            if restamp_up_to_date:
                await reconcile_repo_head_commit(abs_path, current_head)
            results.append(
                RepoUpdateResult(
                    alias=entry.alias,
                    updated=False,
                    skipped_reason="up_to_date",
                )
            )
            continue

        # First-time indexing runs the full pipeline; the `.repowise/` dir is
        # created on demand by ``update_single_repo_index`` (resolve_db_url)
        # and ``state.json`` is written by ``_write_repo_state``.
        first_time = not (abs_path / ".repowise").is_dir()
        stale_repos.append(_StaleRepo(entry.alias, abs_path, current_head or "", first_time))
    return stale_repos


async def _repo_is_stale(abs_path: Path, include_working_tree: bool) -> tuple[bool, str | None]:
    """Return ``(is_stale, current_head)`` for one workspace member."""
    from ...repo_config import config_fingerprint

    # Check staleness against stored commit in state.json
    state = read_repo_state(abs_path)
    stored_commit = state.get("last_sync_commit")

    is_stale, current_head, _commits_behind = check_repo_staleness(
        abs_path,
        stored_commit,
    )
    stored_config_fp = state.get("config_fingerprint")
    config_state_missing = stored_config_fp is None and await _has_persisted_repo_index(abs_path)
    if not is_stale and (
        config_state_missing
        or (stored_config_fp is not None and stored_config_fp != config_fingerprint(abs_path))
    ):
        is_stale = True

    # A watched repo's changes are uncommitted by definition, so the
    # commit-to-commit staleness check above says "up to date" for exactly
    # the work the watcher woke up for.
    if not is_stale and include_working_tree and has_working_tree_changes(abs_path):
        is_stale = True

    # Rows written under older git history rules: one full walk refreshes
    # them, as the single-repo update does, even with HEAD unmoved.
    if not is_stale and git_history_stale(state) and await _has_persisted_repo_index(abs_path):
        is_stale = True
    return is_stale, current_head


def _defer_to_running_workspace_update(
    workspace_owner: dict[str, Any], stale_repos: list[_StaleRepo]
) -> list[RepoUpdateResult]:
    """Queue every stale repo for the workspace update already in flight."""
    age = _lock_age_seconds(workspace_owner)
    _log.info(
        "workspace_update: deferring — a workspace update is already "
        "in flight (pid=%s elapsed=%ss); queuing %d stale repo(s) for it",
        workspace_owner.get("pid"),
        int(age) if age is not None else "?",
        len(stale_repos),
    )
    # Record pending so the running workspace update rolls forward to the
    # latest HEAD of each member. Mirrors the per-repo deferral in
    # ``_defer_in_flight_repo``.
    for _alias, path, new_head, _first_time in stale_repos:
        if new_head:
            with suppress(OSError):
                (path / ".repowise").mkdir(parents=True, exist_ok=True)
                (path / ".repowise" / ".update.pending").write_text(new_head, encoding="utf-8")
    return [
        RepoUpdateResult(
            alias=alias,
            updated=False,
            skipped_reason="in_flight",
            lock_age_seconds=age,
        )
        for alias, _path, _head, _first_time in stale_repos
    ]


async def _update_stale_repo(update_pass: _RepoUpdatePass, repo: _StaleRepo) -> RepoUpdateResult:
    """Update one stale repo under the pass's concurrency limit."""
    alias, path, new_head, first_time = repo
    async with update_pass.semaphore:
        try:
            if update_pass.on_repo_start:
                update_pass.on_repo_start(alias)

            # Ensure the .repowise/ dir exists before the pipeline runs so
            # first-time indexing has a place to put wiki.db and state.json.
            (path / ".repowise").mkdir(parents=True, exist_ok=True)

            # Per-repo single-flight lock. The post-commit hook fires a
            # new ``repowise update`` for every commit; without this guard,
            # rapid-fire commits race on save_state, each pass starts from
            # the same stale base, and the wiki never converges to HEAD.
            # Check + acquire are one atomic exclusive create.
            existing = _try_acquire_lock(path, new_head)
            if existing is not None:
                return _defer_in_flight_repo(alias, path, new_head, existing)

            try:
                update_started = time.monotonic()
                result = await update_single_repo_index(
                    path,
                    commit_depth=update_pass.commit_depth,
                    exclude_patterns=update_pass.exclude_patterns,
                    include_working_tree=update_pass.include_working_tree,
                )
                if result.updated and result.phase_timings is None:
                    result.phase_timings = {"run": time.monotonic() - update_started}
            finally:
                _release_lock(path)
            result.alias = alias
            result.first_time_indexed = first_time and result.updated

            if result.updated and new_head:
                _write_repo_state(path, alias, new_head, first_time, result)
                # Keep the DB freshness stamp in lockstep with last_sync_commit.
                # The no-relevant-changes incremental path returns updated=True
                # without re-running DB persistence, so the row would otherwise
                # lag HEAD; a no-op when persistence already stamped it.
                await reconcile_repo_head_commit(path, new_head)
                # Drop any stale pending marker now that we've advanced to
                # new_head (a bailed sibling update may have written one).
                clear_stale_update_pending(path, new_head)

            # Update workspace config entry
            if result.updated:
                # Report what the store holds, as `workspace list` does; the
                # run's own parse figures are 0/0 when nothing needed re-parsing.
                counts = await stored_index_counts(path)
                if counts is not None:
                    result.file_count, result.symbol_count = counts
                entry = update_pass.ws_config.get_repo(alias)
                if entry is not None:
                    stamp_workspace_entry(entry, path, new_head)
        except Exception as exc:
            result = RepoUpdateResult(
                alias=alias,
                updated=False,
                error=str(exc),
            )

        if update_pass.on_repo_done:
            try:
                update_pass.on_repo_done(result)
            except Exception as exc:
                # Do not retry a callback that may already have emitted output.
                # Keep the original update error if reporting it also failed.
                _log.warning("Workspace update callback failed for %s", alias, exc_info=True)
                if result.error is None:
                    result = RepoUpdateResult(alias=alias, updated=False, error=str(exc))

        return result


def _defer_in_flight_repo(
    alias: str, path: Path, new_head: str, existing: dict[str, Any]
) -> RepoUpdateResult:
    """Leave a pending marker for the update that already holds this repo's lock."""
    age = _lock_age_seconds(existing)
    target_short = (existing.get("target_commit") or "")[:8]
    _log.info(
        "workspace_update: skipping %s — update already in flight "
        "(pid=%s target=%s elapsed=%ss)",
        alias,
        existing.get("pid"),
        target_short,
        int(age) if age is not None else "?",
    )
    # Record pending so the running update can roll forward.
    with suppress(OSError):
        (path / ".repowise" / ".update.pending").write_text(new_head, encoding="utf-8")
    return RepoUpdateResult(
        alias=alias,
        updated=False,
        skipped_reason="in_flight",
        lock_age_seconds=age,
    )


def _write_repo_state(
    path: Path, alias: str, new_head: str, first_time: bool, result: RepoUpdateResult
) -> None:
    """Advance ``state.json`` to *new_head* after a successful repo update."""
    state_path = path / ".repowise" / "state.json"
    state = read_repo_state(path)

    # Falsy, not absent: an explicit null survives a
    # membership test and strands the pointer permanently.
    if not state.get("last_docs_commit") and state.get("last_sync_commit"):
        state["last_docs_commit"] = state["last_sync_commit"]

    state["last_sync_commit"] = new_head
    if result.kg_state:
        state["knowledge_graph"] = result.kg_state
    if result.working_tree_paths is not None:
        state["working_tree_paths"] = result.working_tree_paths
    if result.phase_timings is not None:
        state["phase_timings"] = result.phase_timings
    if result.git_history_refreshed:
        state[GIT_HISTORY_VERSION_KEY] = GIT_HISTORY_VERSION
    # Stamp the config fingerprint so the drift check in
    # update_single_repo_index stays calibrated (and legacy repos
    # without one stop re-triggering the full re-index).
    with suppress(Exception):
        from ...repo_config import (
            config_dependency_fingerprints,
            config_fingerprint,
            load_repo_config,
        )

        state["config_fingerprint"] = config_fingerprint(path)
        state["config_dependency_fingerprints"] = config_dependency_fingerprints(
            path, config=load_repo_config(path)
        )
    # Mark first-time so downstream tooling (status, doctor) can
    # distinguish a never-indexed repo from one that's been
    # updated at least once.
    if first_time and "docs_mode" not in state and "docs_enabled" not in state:
        # This path indexes only; nothing renders pages here, not
        # even from templates.
        state.update(docs_mode_state_fields("none"))
        state["docs_skip_reason"] = (
            "first-time index via update; run "
            "`repowise update --repo " + alias + " --docs` to generate docs"
        )
    from ...fsutils import atomic_write_text

    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(state_path, _json.dumps(state, indent=2))


def _collect_update_results(
    update_results: list[RepoUpdateResult | BaseException],
    results: list[RepoUpdateResult],
) -> list[str]:
    """Append each gathered result to *results*; return the updated aliases."""
    for r in update_results:
        if isinstance(r, BaseException) and not isinstance(r, Exception):
            raise r

    changed_aliases: list[str] = []
    for r in update_results:
        if isinstance(r, Exception):
            results.append(
                RepoUpdateResult(
                    alias="unknown",
                    updated=False,
                    error=str(r),
                )
            )
        else:
            results.append(r)
            if r.updated:
                changed_aliases.append(r.alias)
    return changed_aliases


async def _finish_workspace_pass(
    ws_config: WorkspaceConfig,
    workspace_root: Path,
    changed_aliases: list[str],
    run_hooks: bool,
) -> None:
    """Save the updated workspace config, then run the cross-repo hooks.

    The hooks run when a repo changed or the cross-repo settings did.
    """
    if changed_aliases:
        ws_config.save(workspace_root)

    # ``run_hooks`` lets the CLI defer these so they run once over the union
    # of index-only and docs repos, rather than on this partial set.
    if run_hooks and (changed_aliases or analysis_config_changed(ws_config, workspace_root)):
        await run_cross_repo_hooks(ws_config, workspace_root, changed_aliases)
