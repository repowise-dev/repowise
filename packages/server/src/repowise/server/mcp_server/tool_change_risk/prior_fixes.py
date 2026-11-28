"""Past bug fixes on the lines a change touches."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from repowise.core.analysis.change_risk import (
    FixHistoryUnavailableError,
    fixes_reachable_from,
)
from repowise.core.analysis.prior_fix_impact import (
    FixRecord,
    PriorFixFile,
    PriorFixImpact,
    dominant_file,
    parse_old_ranges,
    summarize_prior_fixes,
    unavailable_prior_fixes,
    unsupported_prior_fixes,
)

# Patchable names are read off the package at call time so patches reach them.
from repowise.server.mcp_server import tool_change_risk as _pkg
from repowise.server.mcp_server._helpers import (
    is_missing_table,
)

# Named for the tool module so log records keep their source.
log = structlog.get_logger("repowise.server.mcp_server.tool_change_risk")


#: Cap on the per-file prior-fix list, matching ``_IMPACTED_TESTS_LIMIT`` so both
#: per-file blocks in this response stay the same size.
_PRIOR_FIXES_LIMIT = 10


async def _prior_fixes_block(
    ctx: Any, changed: dict[str, set[int]], *, history_ref: str | None = None
) -> dict[str, Any] | None:
    """Past bug fixes that landed on the lines this change touches, or ``None``.

    Aggregate only. No inducing commit is named: attribution is precise enough
    to count fixes, not to accuse the commit that caused one.

    ``overlapping_lines`` is the honest weak signal and is labelled as such. A
    fix's stored ranges are line numbers on ITS parent commit while the change's
    are line numbers now, so any commit in between shifts them; the count says
    "this neighbourhood has been patched before", never "this exact line". The
    per-file fix count beside it carries no such caveat.

    Silent (``None``) when the index has no fix events for these files at all,
    so a repo without the feature grows no noise block. A read that *failed* is
    not silent: it renders with ``status: "unavailable"``, because "we could not
    look" and "nothing has broken here" are opposite claims about the change.
    """
    impact = await _pkg._read_prior_fixes(ctx, changed, history_ref=history_ref)
    if impact.status == "unsupported" or (impact.status == "available" and impact.is_empty):
        return None
    if impact.status != "available":
        return {
            "status": impact.status,
            "reason": impact.reason,
            "summary": (
                "The bug-fix record for these files could not be read, so this "
                "change is not cleared of a fix-prone history."
            ),
        }

    shown = impact.files[:_PRIOR_FIXES_LIMIT]
    block = {
        "files": [_prior_fix_row(f) for f in shown],
        "truncated": len(impact.files) > _PRIOR_FIXES_LIMIT,
        "total_fixes": impact.total_fixes,
        "files_with_fixes": impact.files_with_fixes,
        "changed_lines_in_fixed_files": impact.changed_lines_in_fixed_files,
        "line_overlap": impact.line_overlap_basis,
        "summary": (
            f"{impact.total_fixes} past bug-fix commit(s) touched "
            f"{impact.files_with_fixes} of the changed file(s). "
            "Line overlap is approximate (past ranges are numbered on their own "
            "parent commit); the per-file counts are not."
        ),
    }
    # Only over the files actually returned, so the sentence never names a path
    # the reader cannot find anywhere else in the block.
    top = dominant_file(shown)
    if top is not None:
        block["concentration"] = (
            f"{top.file_path} carries {top.share_of_change:.0%} of the changed lines "
            f"and {top.fix_count} past bug fix(es)."
        )
    return block


def _prior_fix_row(entry: PriorFixFile) -> dict[str, Any]:
    """One core row on the wire, keeping the historical key set and order."""
    row: dict[str, Any] = {
        "file_path": entry.file_path,
        "fix_count": entry.fix_count,
        "overlapping_lines": entry.overlapping_lines,
        "changed_lines": entry.changed_lines,
        "share_of_change": entry.share_of_change,
    }
    # Absent rather than null when no event carried a date, as before.
    if entry.last_fix_days_ago is not None:
        row["last_fix_days_ago"] = entry.last_fix_days_ago
    return row


async def _read_prior_fixes(
    ctx: Any, changed: dict[str, set[int]], *, history_ref: str | None = None
) -> PriorFixImpact:
    """Collect fix events from the index. Collection only; core summarizes.

    With *history_ref* (the score's own fix-history anchor) only fixes in that
    ref's history count, so a reviewed commit and its successors in the index
    are not read back as the ground it stood on.

    The four ways this can come back empty are not one state. An index that
    predates fix events has nothing to read (``unsupported``), while a query
    that failed means the record exists and was not read (``unavailable``).
    Collapsing those is how a failure reads as a clean bill.
    """
    from repowise.core.persistence.database import get_session
    from repowise.core.persistence.models import FixEvent

    session_factory = getattr(ctx, "session_factory", None)
    if session_factory is None:
        return unsupported_prior_fixes("this repository has no index to read a fix record from")
    if not changed:
        return unsupported_prior_fixes("no changed lines were counted for this change")

    try:
        async with get_session(session_factory) as session:
            repo_id = (await _pkg._get_repo(session)).id
            res = await session.execute(
                select(FixEvent).where(
                    FixEvent.repository_id == repo_id,
                    FixEvent.file_path.in_(list(changed)),
                    FixEvent.shape_kind == "code_fix",
                )
            )
            events = list(res.scalars().all())
    except LookupError:
        return unsupported_prior_fixes("this repository is not indexed")
    except OperationalError as exc:
        # An index built before fix events existed has no table to read. That is
        # silence, not an error the caller should have to handle -- but a locked
        # or unreadable database is a real failure and falls through below.
        if is_missing_table(exc):
            return unsupported_prior_fixes("this index predates the bug-fix record")
        return unavailable_prior_fixes(_read_failure(exc))
    except SQLAlchemyError as exc:
        return unavailable_prior_fixes(_read_failure(exc))

    if history_ref is not None:
        try:
            events = await _events_before(ctx, events, history_ref)
        except FixHistoryUnavailableError as exc:
            return unavailable_prior_fixes(f"could not place fixes in history: {exc}")

    return summarize_prior_fixes(
        (
            FixRecord(
                fix_sha=event.fix_sha,
                file_path=event.file_path,
                old_ranges=parse_old_ranges(event.old_ranges_json),
                committed_at=event.committed_at,
            )
            for event in events
        ),
        changed,
    )


async def _events_before(ctx: Any, events: list[Any], history_ref: str) -> list[Any]:
    """The fix events in *history_ref*'s history, so a review never counts itself."""
    kept = await asyncio.to_thread(
        fixes_reachable_from, str(ctx.path), history_ref, (event.fix_sha for event in events)
    )
    return [event for event in events if event.fix_sha in kept]


def _read_failure(exc: SQLAlchemyError) -> str:
    """Name the failure without quoting the driver.

    Driver text can carry connection-string fragments, and this string goes out
    on the wire. The class of failure is what a caller can act on; the detail
    belongs in the server log, where it already is.
    """
    log.warning("prior_fixes_read_failed", error=str(exc))
    return f"the fix record could not be read ({type(exc).__name__})"
