"""Impacted tests: the selection ``repowise impacted-tests`` makes for the change."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

import structlog
from sqlalchemy.exc import SQLAlchemyError

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_change_risk as _pkg
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._call_tasks import start_call_task
from repowise.server.mcp_server._test_selection import (
    UNAVAILABLE_REASON,
    SelectionUnavailableError,
    selection_block,
)

# Named for the tool module so log records keep their source.
log = structlog.get_logger("repowise.server.mcp_server.tool_change_risk")


#: Cap on the line-precise impacted-test list, matching the get_risk directive's
#: ``tests_to_run`` cap so both surfaces stay glanceable. The tail goes to the
#: omission store, so ``truncated: true`` is recoverable rather than a dead end.
_IMPACTED_TESTS_LIMIT = 10


def _empty_impacted(status: str, summary: str) -> dict[str, Any]:
    """Uniform impacted-tests block for the paths where no selection was made.

    Nothing vouches for a subset there, so the block says ``run_all`` with the
    reason, like a selection that cannot vouch for one. ``basis`` is ``none``
    and nothing is named, so there is no ``tests_to_run_kind``, ``map_present``
    or ``line_coverage``.
    """
    return {
        "status": status,
        "run_all": True,
        "reasons": [summary, UNAVAILABLE_REASON],
        "basis": "none",
        "tests_to_run": [],
        "total": 0,
        "truncated": False,
        "summary": summary,
    }


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

    The same collection and fail-closed selection (``test_collection`` then
    ``select_tests``) over the whole change, every touched path included, so a
    manifest, a conftest or a test that walks the source tree weighs here as it
    does in CI. The caller's suffix, include and exclude filters do not narrow
    it; only *owned_excludes*, repowise's own output, leaves it, as it leaves
    every other block. ``line_coverage`` classifies the scored files' lines against
    the per-test map, on the side of the diff the map was measured at.
    Any failure degrades to a ``status`` string with ``run_all`` true and never
    raises, so it cannot fail the surrounding score.
    """
    from repowise.core.analysis.changed_lines import change_set
    from repowise.core.analysis.missing_test_signal import detect_missing_tests
    from repowise.core.analysis.test_collection import query_lines
    from repowise.core.persistence.database import get_session

    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is None:
        return _empty_impacted(
            "no_index", "No index; run `repowise init` to enable impacted tests."
        )
    if changed_error is not None:
        return _empty_impacted(*changed_error)
    try:
        change = await asyncio.to_thread(
            partial(
                change_set,
                str(ctx.path),
                None if working_tree else revspec or "HEAD",
                working_tree=working_tree,
                exclude_patterns=owned_excludes,
            )
        )
    except Exception:  # git refused the change, or could not run
        return _empty_impacted("unknown", "Could not read the change from git.")
    try:
        result, selection, _ = await _pkg.select_change_tests(ctx.path, session_factory, change)
        block = selection_block(
            result,
            selection,
            collector,
            limit=_IMPACTED_TESTS_LIMIT,
            label="impacted_tests",
            detail=detail,
        )
        if not result["map_empty"]:
            lines = query_lines(change, result["measured_commit"])
            async with get_session(session_factory) as session:
                repo_id = (await _pkg._get_repo(session)).id
                report = await detect_missing_tests(
                    session, repo_id, {p: lines[p] for p in changed if p in lines}
                )
            block["line_coverage"] = _serialize_missing(report)
    except SelectionUnavailableError as exc:
        return _empty_impacted(exc.status, str(exc))
    except LookupError:
        return _empty_impacted("no_index", "No indexed repository; run `repowise init`.")
    except SQLAlchemyError:
        return _empty_impacted("unknown", "Could not read the index.")
    except Exception as exc:  # a selection bug must not take the score with it
        log.warning("impacted_tests_failed", error=f"{type(exc).__name__}: {exc}")
        return _empty_impacted("unknown", f"Selecting tests failed ({type(exc).__name__}).")
    return block
