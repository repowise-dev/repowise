"""Workspace-level update orchestration.

Handles staleness detection, parallel multi-repo updates, and cross-repo
analysis hooks (Phase 3).
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import sqlite3
import subprocess
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import WorkspaceConfig

_log = logging.getLogger("repowise.workspace.update")


# ---------------------------------------------------------------------------
# Per-repo update lock — duplicated from cli/helpers.py to avoid a core →
# cli import. The format and stale-after threshold MUST stay in sync with
# the canonical helpers; both versions are tiny and rarely change. This
# lives in core/ so ``update_single_repo_index`` (which workspace updates
# call directly, bypassing the CLI lock acquisition in update_cmd.py) is
# itself single-flight per repo.
# ---------------------------------------------------------------------------

_LOCK_FILENAME = ".update.lock"
_LOCK_STALE_AFTER_SECONDS = 30 * 60


def _merged_repo_excludes(
    repo_path: Path,
    extra_exclude_patterns: list[str] | None = None,
) -> list[str]:
    from ..repo_config import load_repo_config

    patterns: list[str] = list(load_repo_config(repo_path).get("exclude_patterns") or [])
    db_path = repo_path / ".repowise" / "wiki.db"
    if db_path.is_file():
        try:
            with sqlite3.connect(str(db_path)) as conn:
                row = conn.execute("SELECT settings_json FROM repositories LIMIT 1").fetchone()
            if row and row[0]:
                settings = _json.loads(row[0])
                if isinstance(settings, dict):
                    for value in settings.get("exclude_patterns") or []:
                        if isinstance(value, str) and value not in patterns:
                            patterns.append(value)
        except Exception:
            pass
    for pattern in extra_exclude_patterns or []:
        if pattern not in patterns:
            patterns.append(pattern)
    return patterns


def _lock_path(repo_path: Path) -> Path:
    return repo_path / ".repowise" / _LOCK_FILENAME


def _read_lock(repo_path: Path) -> dict[str, Any] | None:
    """Return a live lock payload, or None when absent / stale / unreadable.

    Mirrors ``cli/helpers.read_update_lock``: a lock is stale past the
    wall-clock window, or immediately when its owning PID is positively
    dead / recycled (so a crashed update can't block the repo for 30 min).
    Unknown probe results fall back to the wall clock.
    """
    from repowise.core.procutils import pid_alive, process_create_token

    path = _lock_path(repo_path)
    if not path.exists():
        return None
    try:
        payload = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    started = payload.get("started_at")
    if not isinstance(started, (int, float)):
        return None
    if time.time() - started > _LOCK_STALE_AFTER_SECONDS:
        return None

    pid = payload.get("pid")
    if isinstance(pid, int) and pid > 0:
        alive = pid_alive(pid)
        if alive is False:
            return None
        if alive is True:
            stored_token = payload.get("pid_create_token")
            if isinstance(stored_token, str) and stored_token:
                current_token = process_create_token(pid)
                if current_token is not None and current_token != stored_token:
                    return None
    return payload


def _acquire_lock(repo_path: Path, target_commit: str | None) -> None:
    """Best-effort write of the lock file. Caller still must release."""
    from repowise.core.procutils import process_create_token

    try:
        (repo_path / ".repowise").mkdir(parents=True, exist_ok=True)
        _lock_path(repo_path).write_text(
            _json.dumps(
                {
                    "pid": os.getpid(),
                    "pid_create_token": process_create_token(os.getpid()),
                    "target_commit": target_commit,
                    "started_at": time.time(),
                }
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def _release_lock(repo_path: Path) -> None:
    with suppress(OSError):
        _lock_path(repo_path).unlink(missing_ok=True)


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
    # state.json "knowledge_graph" summary block when this run refreshed the
    # KG; None means the persisted block is still current. State-file writes
    # stay with the caller (_update_one), so the block rides on the result.
    kg_state: dict[str, Any] | None = None


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


def commit_exists(repo_path: Path, sha: str) -> bool:
    """Return True when *sha* resolves to a commit in *repo_path*.

    The incremental path must verify this itself: ``ChangeDetector`` returns
    an **empty** diff for unresolvable refs, which would masquerade as "no
    changes" and let the caller bump ``last_sync_commit`` past commits that
    were never indexed (e.g. after a rebase or an aggressive ``git gc``).
    """
    try:
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=str(repo_path),
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def read_repo_state(repo_path: Path) -> dict[str, Any]:
    """Return the parsed ``<repo>/.repowise/state.json``, or ``{}``."""
    state_path = repo_path / ".repowise" / "state.json"
    if not state_path.is_file():
        return {}
    try:
        data = _json.loads(state_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_state_commit(repo_path: Path) -> str | None:
    """Return ``last_sync_commit`` from ``<repo>/.repowise/state.json`` or None."""
    sha = read_repo_state(repo_path).get("last_sync_commit")
    return str(sha) if sha else None


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


async def reconcile_repo_head_commit(repo_path: Path, head: str | None) -> None:
    """Advance the DB freshness for *repo_path* after a sync-check to *head*.

    The "up to date" skip and the no-relevant-changes incremental path bump
    ``state.json``'s ``last_sync_commit`` but never re-run the DB persistence
    that stamps the ``repositories`` row. The server reads both the indexed
    commit (``/api/repos``, MCP ``_meta``) and the "indexed at" time (the health
    overview's ``last_indexed_at`` fallback) from that row, not from
    ``state.json`` — so an un-reconciled row keeps the "index behind checkout"
    signal stuck and the freshness timestamp frozen at the last full index even
    after a successful update.

    Stamps ``head_commit`` only on drift (avoids needless churn), but always
    advances ``updated_at`` so the freshness time reflects the latest
    sync-check — a routine ``repowise update`` that finds nothing to do still
    counts as "verified current now". A no-op when the repo row is absent.
    """
    if not head or not (repo_path / ".repowise" / "wiki.db").is_file():
        return
    from ..persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
    )
    from ..persistence.crud import get_repository_by_path
    from ..persistence.database import resolve_db_url

    url = resolve_db_url(repo_path)
    engine = create_engine(url)
    try:
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is not None:
                if repo.head_commit != head:
                    repo.head_commit = head
                repo.updated_at = datetime.now(UTC)
                await session.flush()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Single-repo update (index-only)
# ---------------------------------------------------------------------------


async def _incremental_repo_update(
    repo_path: Path,
    *,
    state: dict[str, Any],
    base_ref: str,
    exclude_patterns: list[str] | None = None,
) -> RepoUpdateResult | None:
    """Refresh an already-indexed repo through the incremental update path.

    Mirrors the single-repo ``repowise update --index-only`` flow: diff
    ``base_ref..HEAD``, rebuild the graph (parse-cache backed), re-index git
    metadata for the changed files only, run partial health/dead-code
    analysis, and upsert the results. State-file updates stay with the
    caller (``update_workspace``'s ``_update_one``).

    Returns ``None`` when the diff contains deletions or renames: the
    incremental persistence is upsert-only, so rows for removed paths would
    linger in graph/health tables forever. The full pipeline's
    delete-then-insert persistence prunes them — the caller runs it instead.

    Raises on failure — the caller falls back to the full pipeline.
    """
    from ..ingestion.change_detector import ChangeDetector
    from ..pipeline.incremental import (
        persist_incremental_index,
        rebuild_graph_and_git,
        run_partial_analysis,
    )
    from ..pipeline.phases.git import drop_transient_git_signals

    alias = repo_path.name
    head = get_head_commit(repo_path) or "HEAD"

    detector = ChangeDetector(repo_path)
    file_diffs = detector.get_changed_files(base_ref, head)
    if not file_diffs:
        # New commits but nothing the index cares about changed (merge/empty
        # commits, or every change excluded). Report success so the caller
        # bumps ``last_sync_commit`` instead of re-diffing forever.
        return RepoUpdateResult(alias=alias, updated=True)

    if any(fd.status in ("deleted", "renamed") for fd in file_diffs):
        # Upsert-only persistence can't remove rows for paths that no longer
        # exist; hand off to the full pipeline so its prune pass cleans up.
        _log.info(
            "workspace_update: %s has deleted/renamed files — using the full "
            "pipeline so stale index rows are pruned",
            alias,
        )
        return None

    # Per-repo config, like the single-repo update path. The workspace-level
    # ``exclude_patterns`` (when provided) apply on top.
    from ..repo_config import load_repo_config

    cfg = load_repo_config(repo_path)
    merged_excludes = _merged_repo_excludes(repo_path, exclude_patterns)

    (
        parsed_files,
        _source_map,
        graph_builder,
        _structure,
        file_count,
        git_meta_map,
    ) = await rebuild_graph_and_git(
        repo_path,
        file_diffs,
        cfg,
        merged_excludes,
        git_tier=state.get("git_tier"),
        include_submodules=bool(state.get("include_submodules", False)),
        include_nested_repos=bool(state.get("include_nested_repos", False)),
        log=_log.info,
    )

    partial_health_report, dead_code_report = run_partial_analysis(
        repo_path, graph_builder, git_meta_map, parsed_files, file_diffs, log=_log.info
    )

    # Partial health has consumed the per-file ``BlameIndex``; drop it before
    # the metadata reaches persistence so the transient, non-serializable
    # object can never leak downstream (mirrors the CLI update path).
    drop_transient_git_signals(list(git_meta_map.values()))

    # Refresh the knowledge graph (layers/tour/entry points) when the graph
    # shape changed — previously init-only, so workspace member repos served
    # a stale orientation snapshot forever (#669).
    from ..pipeline.incremental import refresh_knowledge_graph

    kg = await refresh_knowledge_graph(
        repo_path,
        parsed_files,
        graph_builder,
        _structure,
        git_meta_map,
        dead_code_report,
        prior_fingerprint=(state.get("knowledge_graph") or {}).get("fingerprint"),
        log=_log.info,
    )

    await persist_incremental_index(
        repo_path,
        graph_builder,
        git_meta_map,
        dead_code_report,
        partial_health_report,
        [fd.path for fd in file_diffs],
        current_graph_file_paths={pf.file_info.path for pf in parsed_files},
        knowledge_graph_result=kg,
        log=_log.info,
    )

    kg_state: dict[str, Any] | None = None
    if kg is not None:
        from ..analysis.knowledge_graph import build_kg_state, save_knowledge_graph_json

        try:
            save_knowledge_graph_json(repo_path, kg)
            kg_state = build_kg_state(kg)
        except Exception:
            _log.warning("knowledge-graph.json export failed for %s", alias, exc_info=True)

    return RepoUpdateResult(
        alias=alias,
        updated=True,
        file_count=file_count,
        symbol_count=sum(len(pf.symbols) for pf in parsed_files),
        kg_state=kg_state,
    )


async def update_single_repo_index(
    repo_path: Path,
    *,
    commit_depth: int = 500,
    exclude_patterns: list[str] | None = None,
    progress: Any | None = None,
) -> RepoUpdateResult:
    """Refresh the index for a single repo.

    Already-indexed repos (a persisted ``last_sync_commit`` whose commit
    still resolves, plus an existing ``wiki.db``) go through the incremental
    update path — changed-files diff, partial analysis, upsert persistence.
    Never-indexed repos, and any incremental failure, run the full
    ingestion pipeline instead (index-only — no wiki pages).
    """
    from ..persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from ..persistence.database import resolve_db_url
    from ..pipeline import run_pipeline
    from ..pipeline.persist import persist_pipeline_result

    alias = repo_path.name
    state = read_repo_state(repo_path)
    base_ref = state.get("last_sync_commit")
    merged_excludes = _merged_repo_excludes(repo_path, exclude_patterns)

    if (
        base_ref
        and (repo_path / ".repowise" / "wiki.db").is_file()
        and commit_exists(repo_path, str(base_ref))
    ):
        try:
            incremental_result = await _incremental_repo_update(
                repo_path,
                state=state,
                base_ref=str(base_ref),
                exclude_patterns=exclude_patterns,
            )
            if incremental_result is not None:
                return incremental_result
            # None → the diff needs the full pipeline's prune pass.
        except Exception:
            _log.warning(
                "Incremental update failed for %s — falling back to the full pipeline",
                repo_path,
                exc_info=True,
            )

    try:
        result = await run_pipeline(
            repo_path,
            commit_depth=commit_depth,
            exclude_patterns=merged_excludes or None,
            include_submodules=bool(state.get("include_submodules", False)),
            include_nested_repos=bool(state.get("include_nested_repos", False)),
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

        # Export the pipeline's freshly built KG so the artifact matches the
        # rows just persisted — the workspace add path already does this; the
        # update-triggered full index previously skipped it.
        kg_state: dict[str, Any] | None = None
        kg = getattr(result, "knowledge_graph_result", None)
        if kg is not None:
            from ..analysis.knowledge_graph import build_kg_state, save_knowledge_graph_json

            try:
                save_knowledge_graph_json(repo_path, kg)
                kg_state = build_kg_state(kg)
            except Exception:
                _log.warning("knowledge-graph.json export failed for %s", alias, exc_info=True)

        return RepoUpdateResult(
            alias=alias,
            updated=True,
            file_count=result.file_count,
            symbol_count=result.symbol_count,
            kg_state=kg_state,
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
            results.append(
                RepoUpdateResult(
                    alias=entry.alias,
                    updated=False,
                    skipped_reason="missing_directory",
                )
            )
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

        is_stale, current_head, _commits_behind = check_repo_staleness(
            abs_path,
            stored_commit,
        )

        if not is_stale:
            # Nothing to regenerate, but the DB freshness stamp can still be
            # behind (e.g. a row left drifted by a pre-fix run). Reconcile it so
            # the server's /api/repos no longer reports "index behind checkout".
            await reconcile_repo_head_commit(abs_path, current_head)
            results.append(
                RepoUpdateResult(
                    alias=entry.alias,
                    updated=False,
                    skipped_reason="up_to_date",
                )
            )
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

            # Per-repo single-flight check. The post-commit hook fires a
            # new ``repowise update`` for every commit; without this guard,
            # rapid-fire commits race on save_state, each pass starts from
            # the same stale base, and the wiki never converges to HEAD.
            existing = _read_lock(path)
            if existing is not None:
                elapsed = int(time.time() - existing.get("started_at", time.time()))
                target_short = (existing.get("target_commit") or "")[:8]
                _log.info(
                    "workspace_update: skipping %s — update already in flight "
                    "(pid=%s target=%s elapsed=%ds)",
                    alias,
                    existing.get("pid"),
                    target_short,
                    elapsed,
                )
                # Record pending so the running update can roll forward.
                with suppress(OSError):
                    (path / ".repowise" / ".update.pending").write_text(new_head, encoding="utf-8")
                return RepoUpdateResult(
                    alias=alias,
                    updated=False,
                    skipped_reason="in_flight",
                )

            _acquire_lock(path, new_head)
            try:
                result = await update_single_repo_index(
                    path,
                    commit_depth=commit_depth,
                    exclude_patterns=exclude_patterns,
                )
            finally:
                _release_lock(path)
            result.alias = alias
            result.first_time_indexed = first_time and result.updated

            # Update state.json with new commit
            if result.updated and new_head:
                import json as _json

                state_path = path / ".repowise" / "state.json"
                state: dict[str, Any] = {}
                if state_path.is_file():
                    with suppress(Exception):
                        state = _json.loads(state_path.read_text(encoding="utf-8"))
                state["last_sync_commit"] = new_head
                if result.kg_state:
                    state["knowledge_graph"] = result.kg_state
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
                    _json.dumps(state, indent=2),
                    encoding="utf-8",
                )
                # Keep the DB freshness stamp in lockstep with last_sync_commit.
                # The no-relevant-changes incremental path returns updated=True
                # without re-running DB persistence, so the row would otherwise
                # lag HEAD; a no-op when persistence already stamped it.
                await reconcile_repo_head_commit(path, new_head)

            # Update workspace config entry
            if result.updated:
                entry = ws_config.get_repo(alias)
                if entry is not None:
                    entry.indexed_at = datetime.now(UTC).isoformat()
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

    from .breaking_change import run_breaking_change_detection
    from .conformance import run_conformance_check
    from .contracts import ContractStore, load_contract_store, run_contract_extraction
    from .cross_repo import CrossRepoOverlay, run_cross_repo_analysis
    from .system_graph import SystemGraph, run_system_graph_build

    overlay = CrossRepoOverlay()
    try:
        overlay = await run_cross_repo_analysis(ws_config, workspace_root, changed_repos)
    except Exception:
        _log.warning("Cross-repo analysis failed", exc_info=True)

    # Snapshot the contracts as they stand on disk BEFORE extraction overwrites
    # them — this is the cheapest honest "previous" state for the breaking-change
    # diff (no contract history needed; the last index is the baseline).
    previous_store = load_contract_store(workspace_root) or ContractStore()

    # Contract extraction (overwrites contracts.json).
    store = ContractStore()
    try:
        store = await run_contract_extraction(ws_config, workspace_root, changed_repos)
    except Exception:
        _log.warning("Contract extraction failed", exc_info=True)

    # System graph — the normalized service-granular structure every workspace
    # view reads. Built last so it folds in the contracts and overlay above.
    system_graph: SystemGraph | None = None
    try:
        system_graph = await run_system_graph_build(ws_config, workspace_root, store, overlay)
    except Exception:
        _log.warning("System graph build failed", exc_info=True)

    # Breaking-change guard — diff the previous (on-disk) contracts against the
    # freshly extracted set and persist the impacted-consumer report.
    try:
        run_breaking_change_detection(workspace_root, previous_store, store)
    except Exception:
        _log.warning("Breaking-change detection failed", exc_info=True)

    # Conformance + cycles — check declared dependency rules and detect circular
    # service dependencies over the freshly-built system graph.
    if system_graph is not None:
        try:
            run_conformance_check(ws_config, workspace_root, system_graph)
        except Exception:
            _log.warning("Conformance check failed", exc_info=True)
