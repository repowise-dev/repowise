"""Patch coverage: the share of changed lines the stored coverage ran."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from repowise.core.analysis.change_risk.features import revspec_head

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_change_risk as _pkg
from repowise.server.mcp_server._budget import OmissionCollector

from .changed import _change_base
from .impacted import _IMPACTED_TESTS_LIMIT


async def _patch_coverage_block(
    ctx: Any,
    changed: dict[str, set[int]],
    changed_error: tuple[str, str] | None,
    revspec: str | None,
    result: Any,
    collector: OmissionCollector,
    *,
    label: str | None = None,
) -> dict[str, Any] | None:
    """Share of the change's executable lines the stored coverage ran, or ``None``.

    The same computation ``repowise coverage check`` gates on, read from the
    coverage the index stores. ``None`` when there is no index, no stored
    coverage, or no readable change: ``impacted_tests`` already says why.
    ``files`` lists only the files that need attention, riskiest first, capped;
    the totals and ``file_counts`` still count every file. ``coverage.ignore``,
    ``coverage.min_coverable_lines`` and the path-scoped ``coverage.gates``
    apply as they do in the CLI gate; path gates read ``no_data`` on stale
    coverage or invalid config, which ``scope.config_errors`` names. Each row
    carries its file's risk, from the index and the checkout's git history, and
    ``hints``: the test file to extend per uncovered range. ``project``
    compares the ingest measured at the change's base commit with the current
    one, when both exist (repo-wide totals only, never gated here).

    With no revspec, *changed* is what a push would bring (the caller diffs
    from the merge-base with the push base, *label* names it). For uncommitted
    work no commit names that code, so freshness is by time: ``current`` when
    the last coverage ingest came after the newest edit to the changed files.
    """
    from repowise.core import git_refs
    from repowise.core.analysis.health.coverage import configured_coverage
    from repowise.core.analysis.health.coverage.freshness import newest_mtime
    from repowise.core.analysis.patch_coverage import (
        assess_risks,
        attach_risk,
        read_git_fix_history,
        stored_patch_coverage,
    )
    from repowise.core.persistence.database import get_session

    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is None or changed_error is not None or not changed:
        return None
    head_commit = mtime = None
    if result.working_tree:
        mtime = await asyncio.to_thread(newest_mtime, ctx.path, changed)
    else:
        head_commit = git_refs.resolve(str(ctx.path), revspec_head(revspec)) or None
    base_commit = await asyncio.to_thread(
        _change_base, str(ctx.path), revspec, label, result.working_tree
    )
    index: dict = {}
    try:
        async with get_session(session_factory) as session:
            repo_id = (await _pkg._get_repo(session)).id
            patch = await stored_patch_coverage(
                session,
                repo_id,
                changed,
                label=label or result.features.ref,
                head_commit=head_commit,
                config=configured_coverage(ctx.path),
                working_tree_mtime=mtime,
                repo_path=str(ctx.path),
            )
            if patch is not None:
                patch, index = await _enrich_patch(
                    session, repo_id, patch, base_commit, str(ctx.path), head_commit, result
                )
    except (LookupError, SQLAlchemyError):
        return None
    if patch is None:
        return None
    # Git after the session closes: no subprocess while a connection is held.
    git = await asyncio.to_thread(
        read_git_fix_history, str(ctx.path), revspec, working_tree=result.working_tree
    )
    patch = attach_risk(patch, assess_risks([f.file_path for f in patch.files], git, index))
    block = patch.to_dict()
    block["files"] = _attention_files(patch, collector)
    return block


async def _enrich_patch(
    session: Any,
    repo_id: str,
    patch: Any,
    base_commit: str | None,
    repo_path: str,
    head_commit: str | None,
    result: Any,
) -> tuple[Any, dict]:
    """The patch with its history delta and test hints, plus the index facts read."""
    from repowise.core.analysis.patch_coverage import (
        attach_hints,
        attach_history_delta,
        read_index_facts,
        read_test_hints,
    )

    # An unreadable index row leaves risk to git alone and hints null.
    index: dict = {}
    patch = await attach_history_delta(session, repo_id, patch, base_commit)
    with contextlib.suppress(SQLAlchemyError):
        index = await read_index_facts(session, repo_id, [f.file_path for f in patch.files])
    # Advice: read_test_hints logs any failure and returns None.
    hints = await read_test_hints(
        session,
        repo_id,
        patch,
        repo_path=repo_path,
        head_commit=head_commit,
        working_tree=result.working_tree,
    )
    if hints is not None:
        patch = attach_hints(patch, hints)
    return patch, index


def _attention_files(patch: Any, collector: OmissionCollector) -> list[dict]:
    """The files needing attention, riskiest first, capped; the tail goes to *collector*."""
    from repowise.core.analysis.patch_coverage import attention_rows

    rows = [f.to_dict() for f in attention_rows(patch)]
    if len(rows) > _IMPACTED_TESTS_LIMIT:
        collector.add(
            f"patch_coverage.files beyond cap={_IMPACTED_TESTS_LIMIT} "
            f"({len(rows) - _IMPACTED_TESTS_LIMIT} dropped)",
            [row["file_path"] for row in rows[_IMPACTED_TESTS_LIMIT:]],
        )
    return rows[:_IMPACTED_TESTS_LIMIT]
