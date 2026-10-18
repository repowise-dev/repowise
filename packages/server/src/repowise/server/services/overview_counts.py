"""The figures Overview's explore cards and inventory share with their pages.

Each count is read through the function the destination page leads with, so
the Overview figure and the page figure cannot disagree:

- decisions: ``count_decisions_by_lane``, the Decisions page lede;
- refactoring: the stored rollup the Refactoring page lede reads;
- ownership: files with bus factor exactly 1, the Contributors page's
  "Sole-owned files";
- security: :func:`security_class_counts`, the split the Security page lede
  reads, "in shipped source" versus tests and docs;
- coverage: the newest ingested report, the Tests page lede.

The reads are grouped counts, a primary-key read and the decision lanes'
three-column scan the Decisions page already pays. Each runs in a savepoint,
so a store that fails costs its own block rather than the page.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.refactoring.serving import summary_payload
from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis.coverage import load_newest_ingest
from repowise.core.persistence.crud.analysis.refactoring_opportunities import (
    get_refactoring_summary,
)
from repowise.core.persistence.models import GitMetadata
from repowise.server.services.security_classes import security_class_counts


async def _decisions(session: AsyncSession, repo_id: str) -> dict[str, int]:
    lanes = await crud.count_decisions_by_lane(session, repo_id)
    return {
        "total": int(lanes.get("total", 0)),
        "accepted": int(
            lanes.get("active", 0) + lanes.get("needs_review", 0) + lanes.get("uncheckable", 0)
        ),
        "candidates": int(lanes.get("candidates", 0)),
        "needs_review": int(lanes.get("needs_review", 0)),
    }


async def _refactoring(session: AsyncSession, repo_id: str) -> dict[str, int] | None:
    # The rollup the Refactoring lede reads, without the gate breakdown the
    # page adds beside it.
    summary = summary_payload(await get_refactoring_summary(session, repo_id))
    if summary.get("status") != "available":
        return None
    return {
        "opportunities_total": int(summary.get("opportunities_total") or 0),
        "files_total": int(summary.get("files_total") or 0),
    }


async def _ownership(session: AsyncSession, repo_id: str) -> dict[str, int]:
    # Exactly 1, credited to a primary owner (named or by email), as the
    # owners rollup counts it: a bus factor of 0 means no indexed history.
    sole = await session.scalar(
        select(func.count()).where(
            crud.code_file_rows(repo_id),
            GitMetadata.bus_factor == 1,
            or_(GitMetadata.primary_owner_name != "", GitMetadata.primary_owner_email != ""),
        )
    )
    return {"sole_owned_files": int(sole or 0)}


async def _security(session: AsyncSession, repo_id: str) -> dict[str, int]:
    counts = await security_class_counts(session, repo_id)
    classes = counts["class_counts"]
    return {
        "total": counts["total"],
        "source": classes["source"],
        "source_high": counts["source_high"],
        "elsewhere": classes["test"] + classes["docs"],
    }


async def _coverage(session: AsyncSession, repo_id: str) -> dict[str, Any] | None:
    ingest = await load_newest_ingest(session, repo_id)
    if ingest is None or ingest.line_coverage_pct is None:
        return None
    return {
        "line_coverage_pct": float(ingest.line_coverage_pct),
        "ingested_at": ingest.ingested_at.isoformat() if ingest.ingested_at else None,
    }


_BLOCKS: tuple[tuple[str, Callable[[AsyncSession, str], Awaitable[Any]]], ...] = (
    ("decisions", _decisions),
    ("refactoring", _refactoring),
    ("ownership", _ownership),
    ("security", _security),
    ("coverage", _coverage),
)


async def explore_counts(session: AsyncSession, repo_id: str) -> dict[str, Any]:
    """One block per page; ``None`` where the store is absent or failed."""
    out: dict[str, Any] = {}
    for key, read in _BLOCKS:
        try:
            async with session.begin_nested():
                out[key] = await read(session, repo_id)
        except Exception:
            out[key] = None
    return out
