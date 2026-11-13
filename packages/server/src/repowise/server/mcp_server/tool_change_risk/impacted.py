"""Impacted tests: the selection ``repowise impacted-tests`` makes for the change."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

import structlog

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_change_risk as _pkg
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._call_tasks import start_call_task
from repowise.server.mcp_server._test_selection import (
    change_tests_block,
    degraded_block,
)
from repowise.server.mcp_server._test_selection import unselected_block as _empty_impacted

# Named for the tool module so log records keep their source.
log = structlog.get_logger("repowise.server.mcp_server.tool_change_risk")


#: Cap on the line-precise impacted-test list, matching the get_risk directive's
#: ``tests_to_run`` cap so both surfaces stay glanceable. The tail goes to the
#: omission store, so ``truncated: true`` is recoverable rather than a dead end.
_IMPACTED_TESTS_LIMIT = 10


def _serialize_missing(report: Any) -> dict[str, Any]:
    """Render the detect_missing_tests dataclass buckets as JSON-ready dicts."""
    return {
        "untested_changes": [
            {
                "source_file": u.source_file,
                "uncovered_lines": u.uncovered_lines,
                "changed_line_count": u.changed_line_count,
            }
            for u in report.untested_changes
        ],
        "stale_test_candidates": [
            {
                "source_file": s.source_file,
                "covering_test_files": s.covering_test_files,
                "covering_test_ids_without_file": s.covering_test_ids_without_file,
            }
            for s in report.stale_test_candidates
        ],
        "covered": list(report.covered),
        # ``no_data`` is "file not in the map" = unknown, never "untested".
        "no_coverage_data": list(report.no_data),
    }


def _start_selection(ctx: Any, revspec: str | None) -> asyncio.Task | None:
    """Begin a committed change's test selection while git scores it, or ``None``.

    The tests block asks for the same change later and joins this selection
    (:func:`select_change_tests` shares one already running), so the two
    overlap instead of running one after the other. Uncommitted work waits for
    the score, which decides whether there is any. Whatever way the calling
    request ends, this task ends with it; a selection another call still waits
    on keeps running.
    """
    if not revspec or getattr(ctx, "session_factory", None) is None:
        return None
    return start_call_task(_select_committed(ctx, revspec))


async def _select_committed(ctx: Any, revspec: str) -> None:
    from repowise.core.analysis.change_risk.service import owned_patterns, riskignore_patterns
    from repowise.core.analysis.changed_lines import change_set

    try:
        owned = owned_patterns(await asyncio.to_thread(riskignore_patterns, str(ctx.path)))
        change = await asyncio.to_thread(
            partial(change_set, str(ctx.path), revspec, exclude_patterns=owned)
        )
        await _pkg.select_change_tests(ctx.path, ctx.session_factory, change)
    except Exception as exc:  # the tests block asks again and reports the failure
        log.debug("impacted_tests_head_start_failed", error=f"{type(exc).__name__}: {exc}")


async def _impacted_tests_block(
    ctx: Any,
    changed: dict[str, set[int]],
    changed_error: tuple[str, str] | None,
    collector: OmissionCollector,
    *,
    revspec: str | None = None,
    working_tree: bool = False,
    owned_excludes: tuple[str, ...] = (),
    detail: bool = True,
) -> dict[str, Any]:
    """The tests the change needs, decided as ``repowise impacted-tests`` decides them.

    The shared :func:`change_tests_block` (the same one ``repowise risk`` and
    the Review page read), plus ``line_coverage``: the scored files' lines
    classified against the per-test map, on the side of the diff the map was
    measured at. Any failure degrades to a ``status`` string with ``run_all``
    true and never raises, so it cannot fail the surrounding score.
    """
    from repowise.core.analysis.missing_test_signal import detect_missing_tests
    from repowise.core.analysis.test_collection import query_lines
    from repowise.core.persistence.database import get_session

    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is not None and changed_error is not None:
        return _empty_impacted(*changed_error)
    block, made = await change_tests_block(
        getattr(ctx, "path", None),
        session_factory,
        revspec,
        collector,
        limit=_IMPACTED_TESTS_LIMIT,
        label="impacted_tests",
        working_tree=working_tree,
        owned_excludes=owned_excludes,
        detail=detail,
        agent=True,
    )
    if made is None:
        return block
    result, change = made
    if result["map_empty"]:
        return block
    try:
        lines = query_lines(change, result["measured_commit"])
        async with get_session(session_factory) as session:
            repo_id = (await _pkg._get_repo(session)).id
            report = await detect_missing_tests(
                session, repo_id, {p: lines[p] for p in changed if p in lines}
            )
    except Exception as exc:
        return degraded_block(exc)
    block["line_coverage"] = _serialize_missing(report)
    return block
