"""Reads of the stored index that the incremental analysis scores against."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._common import LogFn, _noop_log


async def load_stored_git_meta(
    repo_path: Any, *, log: LogFn | None = None
) -> dict[str, dict] | None:
    """Read the persisted per-file git fields the dead-code analyzer scores on.

    Returns ``None`` when the store could not be read, and a mapping (possibly
    an empty one) when it could. The caller has to tell those apart: an empty
    mapping is a repository whose ``git_metadata`` genuinely holds nothing,
    which scores exactly the way a full index would score it, whereas a failed
    read means this run knows less than a full index does and must not
    overwrite what a full index wrote.

    Not every indexed file has a row even on a healthy repository — ``git
    metadata`` covers what ``index_repo`` produced, which excludes skipped
    paths — so a *missing file* is not evidence of a failure and a coverage
    count cannot stand in for one.
    """
    log = log or _noop_log
    # Never create the store as a side effect of reading it. A repo whose
    # wiki.db was deleted to force a rebuild would otherwise be handed an
    # empty one here, and an empty database reads as "indexed" downstream.
    # Same guard, and the same reason, as the head-commit stamper.
    if not (Path(repo_path) / ".repowise" / "wiki.db").is_file():
        log("[yellow]No store to read git metadata from; dead-code scope narrowed[/yellow]")
        return None
    try:
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_dead_code_git_fields,
            get_session,
        )
        from repowise.core.persistence.crud import get_repository_by_path
        from repowise.core.persistence.database import resolve_db_url

        engine = create_engine(resolve_db_url(repo_path))
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo = await get_repository_by_path(session, str(repo_path))
                if repo is None:
                    # The persist path resolves the repository with an upsert,
                    # so a miss here means this run would be writing against a
                    # row that does not exist yet. Unknown, not empty.
                    log(
                        f"[yellow]No stored repository for {repo_path}; "
                        "dead-code scope narrowed[/yellow]"
                    )
                    return None
                return await get_dead_code_git_fields(session, repo.id)
        finally:
            await engine.dispose()
    except Exception as exc:
        log(f"[yellow]Stored git metadata unavailable for dead-code scoring: {exc}[/yellow]")
        return None


async def load_stored_performance_callers(
    repo_path: Any,
    changed_paths: set[str],
    *,
    log: LogFn | None = None,
) -> set[str] | None:
    """Load callers whose persisted performance paths touched changed files.

    The current graph cannot retain an edge to a deleted or renamed symbol.
    One bounded store read supplies that old-side reverse evidence so partial
    analysis can remove stale caller findings. ``None`` means the store was
    unavailable and therefore must not widen authoritative persistence scope.
    """
    log = log or _noop_log
    if not changed_paths:
        return set()
    if not (Path(repo_path) / ".repowise" / "wiki.db").is_file():
        return None
    try:
        import json

        from sqlalchemy import select

        from repowise.core.persistence import create_engine, get_session
        from repowise.core.persistence.crud import get_repository_by_path
        from repowise.core.persistence.database import (
            create_session_factory,
            resolve_db_url,
        )
        from repowise.core.persistence.models import HealthFinding

        engine = create_engine(resolve_db_url(repo_path))
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo = await get_repository_by_path(session, str(repo_path))
                if repo is None:
                    return None
                rows = (
                    await session.execute(
                        select(HealthFinding.file_path, HealthFinding.details_json).where(
                            HealthFinding.repository_id == repo.id,
                            HealthFinding.status == "open",
                            HealthFinding.dimension == "performance",
                        )
                    )
                ).all()
        finally:
            await engine.dispose()
        callers: set[str] = set()
        for file_path, details_json in rows:
            try:
                path = json.loads(details_json or "{}").get("path", ())
            except (TypeError, ValueError, AttributeError):
                continue
            if any(
                isinstance(node, str) and node.split("::", 1)[0] in changed_paths for node in path
            ):
                callers.add(file_path)
        return callers
    except Exception as exc:
        log(f"[yellow]Stored performance callers unavailable: {exc}[/yellow]")
        return None


async def load_stored_function_mod_p80(repo_path: Any, *, log: LogFn | None = None) -> int | None:
    """Load the repo-wide p80 of per-function modification counts.

    Computed from the persisted ``git_function_blame`` rollup, which a FULL
    index writes for every modified function. The incremental health pass
    reuses this value so the Function Hotspot gate is scored against the
    full repo rather than the churn-heavy changed-files subset (issue
    #1484) — a hotspot verdict then no longer flips when a full ``init``
    is later replaced by an incremental ``update``.

    Returns ``None`` when the store is unreadable, the repository row is
    missing, or no blame rollup has been persisted (ESSENTIAL tier, or a
    repo that has never been fully indexed) — callers fall back to the
    walked-set computation in that case.
    """
    log = log or _noop_log
    if not (Path(repo_path) / ".repowise" / "wiki.db").is_file():
        return None
    try:
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.crud import (
            get_git_function_mod_counts,
            get_repository_by_path,
        )
        from repowise.core.persistence.database import resolve_db_url

        engine = create_engine(resolve_db_url(repo_path))
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo = await get_repository_by_path(session, str(repo_path))
                if repo is None:
                    return None
                # What the last full index measured over every walked function.
                stored = getattr(repo, "function_mod_p80", None)
                if stored:
                    return int(stored)
                counts = await get_git_function_mod_counts(session, repo.id)
        finally:
            await engine.dispose()
        if not counts:
            return None
        # Fallback for a store whose last full index predates the column above.
        # The rollup is keyed by ``{path}::{name}``, so a file's same-named
        # functions collapse to one row and this percentile is taken over a
        # population missing those samples. Closer than the changed-files
        # subset, still not the number a full index computes.
        from repowise.core.analysis.health.engine import _percentile_p80

        return _percentile_p80(counts)
    except Exception as exc:
        log(f"[yellow]Stored function-mod percentile unavailable: {exc}[/yellow]")
        return None


async def load_stored_coverage_map(repo_path: Any, *, log: LogFn | None = None) -> dict[str, dict]:
    """Load the persisted coverage map for health scoring on an incremental run.

    The incremental health pass re-scores changed files only, but if it builds
    its ``HealthAnalyzer`` without a ``coverage_map`` every re-analyzed file is
    scored as if no coverage had ever been ingested — and the partial-health
    writer then overwrites the stored ``line_coverage_pct`` with ``None`` for
    exactly the files that just changed, eroding coverage one file per update
    (issue #1739).

    Returns ``{path: coverage-dict}`` by reloading the persisted ``CoverageFile``
    rows, mirroring ``_coverage_for_rescore`` in the CLI update path. Empty dict
    when no coverage is stored, the store is unreadable, or the repository row
    is missing — the analyzer then scores without coverage, matching the
    established safe default.
    """
    log = log or _noop_log

    if not (Path(repo_path) / ".repowise" / "wiki.db").is_file():
        return {}
    try:
        from repowise.core.persistence import (
            create_engine,
            create_session_factory,
            get_session,
        )
        from repowise.core.persistence.crud import (
            get_repository_by_path,
            load_coverage_map,
        )
        from repowise.core.persistence.database import resolve_db_url

        engine = create_engine(resolve_db_url(repo_path))
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo = await get_repository_by_path(session, str(repo_path))
                if repo is None:
                    return {}
                coverage_map = await load_coverage_map(session, repo.id)
        finally:
            await engine.dispose()
        return coverage_map
    except Exception as exc:
        log(f"[yellow]Stored coverage unavailable: {exc}[/yellow]")
        return {}
