"""Workspace-level update orchestration.

Handles staleness detection, parallel multi-repo updates, and cross-repo
analysis hooks (Phase 3).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import WorkspaceConfig

_log = logging.getLogger("repowise.workspace.update")

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class RepoUpdateResult:
    """Result of updating a single repo within a workspace."""

    alias: str
    updated: bool  # True if an update was performed
    skipped_reason: str | None = None  # "up_to_date", "missing_directory", etc.
    file_count: int = 0
    symbol_count: int = 0
    error: str | None = None
    first_time_indexed: bool = False  # True if this run was a first-time index


# ---------------------------------------------------------------------------
# Staleness detection
# ---------------------------------------------------------------------------


def get_head_commit(repo_path: Path) -> str | None:
    """Return the current HEAD commit SHA via git, or ``None``."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def count_commits_between(repo_path: Path, base: str, head: str) -> int:
    """Return the number of commits between *base* and *head*, or 0 on error."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{base}..{head}"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return 0


def read_state_commit(repo_path: Path) -> str | None:
    """Return ``last_sync_commit`` from ``<repo>/.repowise/state.json`` or None."""
    import json as _json

    state_path = repo_path / ".repowise" / "state.json"
    if not state_path.is_file():
        return None
    try:
        data = _json.loads(state_path.read_text(encoding="utf-8"))
        sha = data.get("last_sync_commit")
        return str(sha) if sha else None
    except Exception:
        return None


def sync_workspace_state_from_disk(
    workspace_root: Path,
    ws_config: WorkspaceConfig,
    *,
    save_if_changed: bool = True,
) -> list[str]:
    """Refresh ``WorkspaceConfig`` entries from each repo's on-disk
    ``state.json``.

    A child repo can be updated outside the workspace orchestrator (the
    user runs ``repowise update`` inside the child dir directly), which
    drifts ``RepoEntry.last_commit_at_index`` away from the actual
    ``state.json`` value. Call this before any workspace-level decision
    that reads from ``ws_config`` so we never act on stale info.

    Returns the list of aliases that changed.
    """
    changed: list[str] = []
    for entry in ws_config.repos:
        abs_path = (workspace_root / entry.path).resolve()
        if not abs_path.is_dir():
            continue
        disk_commit = read_state_commit(abs_path)
        if disk_commit is not None and disk_commit != entry.last_commit_at_index:
            entry.last_commit_at_index = disk_commit
            changed.append(entry.alias)
    if changed and save_if_changed:
        try:
            ws_config.save(workspace_root)
        except Exception:
            # Saving is best-effort — the in-memory sync still happened.
            _log.warning("Could not persist synced workspace config", exc_info=True)
    return changed


def check_repo_staleness(
    repo_path: Path,
    last_commit: str | None,
) -> tuple[bool, str | None, int]:
    """Check if a repo has new commits since *last_commit*.

    Returns ``(is_stale, current_head, commits_behind)``.
    """
    current_head = get_head_commit(repo_path)
    if current_head is None:
        return False, None, 0

    if last_commit is None:
        # Never indexed — treat as stale
        return True, current_head, 0

    if current_head == last_commit:
        return False, current_head, 0

    behind = count_commits_between(repo_path, last_commit, current_head)
    return True, current_head, behind


# ---------------------------------------------------------------------------
# Single-repo update (index-only)
# ---------------------------------------------------------------------------


async def update_single_repo_index(
    repo_path: Path,
    *,
    commit_depth: int = 500,
    exclude_patterns: list[str] | None = None,
    progress: Any | None = None,
) -> RepoUpdateResult:
    """Re-run the ingestion pipeline (index-only) for a single repo.

    This refreshes graph, git stats, dead code, and decisions — everything
    except wiki pages. Used by workspace update when no LLM provider is set.
    """
    from ..pipeline import run_pipeline
    from ..persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from ..persistence.database import resolve_db_url
    from ..pipeline.persist import persist_pipeline_result

    alias = repo_path.name

    try:
        result = await run_pipeline(
            repo_path,
            commit_depth=commit_depth,
            exclude_patterns=exclude_patterns,
            generate_docs=False,
            progress=progress,
        )

        # Persist to repo-local DB
        url = resolve_db_url(repo_path)
        engine = create_engine(url)
        await init_db(engine)
        sf = create_session_factory(engine)

        async with get_session(sf) as session:
            repo = await upsert_repository(
                session,
                name=result.repo_name,
                local_path=str(repo_path),
            )
            await persist_pipeline_result(result, session, repo.id)

        await engine.dispose()

        return RepoUpdateResult(
            alias=alias,
            updated=True,
            file_count=result.file_count,
            symbol_count=result.symbol_count,
        )
    except Exception as exc:
        return RepoUpdateResult(
            alias=alias,
            updated=False,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Workspace update orchestration
# ---------------------------------------------------------------------------

_MAX_CONCURRENT_UPDATES = 4


async def update_workspace(
    workspace_root: Path,
    ws_config: WorkspaceConfig,
    *,
    repo_filter: str | None = None,
    dry_run: bool = False,
    commit_depth: int = 500,
    exclude_patterns: list[str] | None = None,
    on_repo_start: Callable[[str], None] | None = None,
    on_repo_done: Callable[[RepoUpdateResult], None] | None = None,
) -> list[RepoUpdateResult]:
    """Update stale repos in the workspace.

    Args:
        workspace_root: Path to the workspace root directory.
        ws_config: Loaded workspace configuration.
        repo_filter: If set, only update this repo alias.
        dry_run: If True, detect staleness but don't actually update.
        commit_depth: Max commits to analyze per file.
        exclude_patterns: Gitignore-style patterns to exclude.
        on_repo_start: Called with alias when a repo update begins.
        on_repo_done: Called with result when a repo update finishes.

    Returns:
        List of :class:`RepoUpdateResult` for each repo.
    """
    results: list[RepoUpdateResult] = []
    # (alias, path, new_head, first_time)
    stale_repos: list[tuple[str, Path, str, bool]] = []

    # Step 1: Determine which repos are stale
    entries = ws_config.repos
    if repo_filter:
        entry = ws_config.get_repo(repo_filter)
        if entry is None:
            available = ", ".join(ws_config.repo_aliases())
            raise ValueError(f"Unknown repo '{repo_filter}'. Available: {available}")
        entries = [entry]

    # Step 0: Sync ``last_commit_at_index`` from each repo's state.json so
    # the workspace config doesn't drift when a child repo is updated
    # outside the workspace orchestrator (e.g. ``repowise update`` run
    # inside the child dir directly).
    sync_workspace_state_from_disk(workspace_root, ws_config)

    for entry in entries:
        abs_path = (workspace_root / entry.path).resolve()
        if not abs_path.is_dir():
            results.append(RepoUpdateResult(
                alias=entry.alias, updated=False, skipped_reason="missing_directory",
            ))
            continue

        # Check staleness against stored commit in state.json
        import json
        state_path = abs_path / ".repowise" / "state.json"
        stored_commit = None
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                stored_commit = state.get("last_sync_commit")
            except Exception:
                pass

        is_stale, current_head, commits_behind = check_repo_staleness(
            abs_path, stored_commit,
        )

        if not is_stale:
            results.append(RepoUpdateResult(
                alias=entry.alias, updated=False, skipped_reason="up_to_date",
            ))
            continue

        # First-time indexing path: previously this short-circuited with
        # ``skipped_reason="not_indexed"``, leaving newly-added workspace
        # repos in a half-broken state. Now we run the full pipeline; the
        # `.repowise/` dir is created on demand by ``update_single_repo_index``
        # (resolve_db_url) and ``state.json`` is written below.
        first_time = not (abs_path / ".repowise").is_dir()
        stale_repos.append((entry.alias, abs_path, current_head or "", first_time))

    if dry_run or not stale_repos:
        return results

    # Step 2: Update stale repos (parallel with concurrency limit)
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_UPDATES)

    async def _update_one(
        alias: str, path: Path, new_head: str, first_time: bool
    ) -> RepoUpdateResult:
        async with semaphore:
            if on_repo_start:
                on_repo_start(alias)

            # Ensure the .repowise/ dir exists before the pipeline runs so
            # first-time indexing has a place to put wiki.db and state.json.
            (path / ".repowise").mkdir(parents=True, exist_ok=True)

            result = await update_single_repo_index(
                path,
                commit_depth=commit_depth,
                exclude_patterns=exclude_patterns,
            )
            result.alias = alias
            result.first_time_indexed = first_time and result.updated

            # Update state.json with new commit
            if result.updated and new_head:
                import json as _json
                state_path = path / ".repowise" / "state.json"
                state: dict[str, Any] = {}
                if state_path.is_file():
                    try:
                        state = _json.loads(state_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                state["last_sync_commit"] = new_head
                # Mark first-time so downstream tooling (status, doctor) can
                # distinguish a never-indexed repo from one that's been
                # updated at least once.
                if first_time and "docs_enabled" not in state:
                    state["docs_enabled"] = False
                    state["docs_skip_reason"] = (
                        "first-time index via update; run "
                        "`repowise update --repo " + alias + " --docs` to generate docs"
                    )
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(
                    _json.dumps(state, indent=2), encoding="utf-8",
                )

            # Update workspace config entry
            if result.updated:
                entry = ws_config.get_repo(alias)
                if entry is not None:
                    entry.indexed_at = datetime.now(timezone.utc).isoformat()
                    entry.last_commit_at_index = new_head

            if on_repo_done:
                on_repo_done(result)

            return result

    update_results = await asyncio.gather(
        *[
            _update_one(alias, path, head, first_time)
            for alias, path, head, first_time in stale_repos
        ],
        return_exceptions=True,
    )

    changed_aliases: list[str] = []
    for r in update_results:
        if isinstance(r, Exception):
            results.append(RepoUpdateResult(
                alias="unknown", updated=False, error=str(r),
            ))
        else:
            results.append(r)
            if r.updated:
                changed_aliases.append(r.alias)

    # Step 3: Save workspace config with updated timestamps
    if changed_aliases:
        ws_config.save(workspace_root)

    # Step 4: Run cross-repo hooks (Phase 3/4 placeholder)
    if changed_aliases:
        await run_cross_repo_hooks(ws_config, workspace_root, changed_aliases)

    return results


# ---------------------------------------------------------------------------
# Cross-repo hooks (Phase 3/4 placeholder)
# ---------------------------------------------------------------------------


async def run_cross_repo_hooks(
    ws_config: WorkspaceConfig,
    workspace_root: Path,
    changed_repos: list[str],
) -> None:
    """Run cross-repo analysis after workspace repos are updated.

    Detects cross-repo co-changes (files committed by the same author within
    a time window across repos) and package/manifest dependencies. Results are
    persisted to ``.repowise-workspace/cross_repo_edges.json`` and loaded by
    the MCP server's :class:`CrossRepoEnricher` at startup.
    """
    if len(ws_config.repos) < 2:
        return

    from .cross_repo import run_cross_repo_analysis

    try:
        await run_cross_repo_analysis(ws_config, workspace_root, changed_repos)
    except Exception:
        _log.warning("Cross-repo analysis failed", exc_info=True)

    # Phase 4: Contract extraction
    from .contracts import run_contract_extraction

    try:
        await run_contract_extraction(ws_config, workspace_root, changed_repos)
    except Exception:
        _log.warning("Contract extraction failed", exc_info=True)
