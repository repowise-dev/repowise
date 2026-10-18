"""The figures Overview's explore cards and inventory share with their pages.

Each count is read through the function the destination page leads with, so
the Overview figure and the page figure cannot disagree:

- decisions: ``count_decisions_by_lane``, the Decisions page lede;
- refactoring: the stored rollup the Refactoring page lede reads;
- ownership: files with bus factor exactly 1, the Contributors page's
  "Sole-owned files";
- security: matches split by :func:`security_path_class`, the Security page's
  "in shipped source" versus tests and docs;
- coverage: the newest ingested report, the Tests page lede.

Every read is a grouped count, a primary-key read or one narrow column, and a
store that fails costs its own block rather than the page.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence import crud
from repowise.core.persistence.crud.analysis.coverage import load_newest_ingest
from repowise.core.persistence.models import GitMetadata, SecurityFinding
from repowise.server.services.refactoring_health import RefactoringHealthService

# Mirrors `securityPathClass` in packages/ui/src/security/posture.tsx, which
# the Security page uses for its lede. Change both together.
_TEST_SEGMENTS = frozenset(
    {
        "test",
        "tests",
        "__tests__",
        "__test__",
        "testdata",
        "e2e",
        "spec",
        "specs",
        "fixtures",
        "__fixtures__",
    }
)
_DOCS_SEGMENTS = frozenset({"docs", "doc", "documentation", "example", "examples"})
_MOCK_SEGMENT = re.compile(r"^(__)?mocks?(__)?$")
_TEST_FILE = re.compile(r"(^test_|_test\.|\.test\.|\.spec\.|_spec\.)", re.IGNORECASE)
_DOCS_FILE = re.compile(r"\.(md|mdx|rst|adoc)$", re.IGNORECASE)


def security_path_class(path: str) -> str:
    """``source``, ``test`` or ``docs``, judged from the path."""
    parts = path.replace("\\", "/").lower().split("/")
    name = parts[-1] if parts else ""
    if any(p in _TEST_SEGMENTS or _MOCK_SEGMENT.match(p) for p in parts) or _TEST_FILE.search(name):
        return "test"
    if any(p in _DOCS_SEGMENTS for p in parts) or _DOCS_FILE.search(name):
        return "docs"
    return "source"


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
    summary = await RefactoringHealthService(session, repo_id, repo_id).summary()
    if summary.get("status") != "available":
        return None
    return {
        "opportunities_total": int(summary.get("opportunities_total") or 0),
        "files_total": int(summary.get("files_total") or 0),
    }


async def _ownership(session: AsyncSession, repo_id: str) -> dict[str, int]:
    # Exactly 1, credited to a primary owner, as the owners rollup counts it:
    # a bus factor of 0 means no indexed history, not risk.
    sole = await session.scalar(
        select(func.count()).where(
            crud.code_file_rows(repo_id),
            GitMetadata.bus_factor == 1,
            GitMetadata.primary_owner_name.is_not(None),
            GitMetadata.primary_owner_name != "",
        )
    )
    return {"sole_owned_files": int(sole or 0)}


async def _security(session: AsyncSession, repo_id: str) -> dict[str, int]:
    rows = (
        await session.execute(
            select(SecurityFinding.file_path, SecurityFinding.severity).where(
                SecurityFinding.repository_id == repo_id
            )
        )
    ).all()
    source = [sev for path, sev in rows if security_path_class(path or "") == "source"]
    return {
        "total": len(rows),
        "source": len(source),
        "source_high": sum(1 for sev in source if sev == "high"),
        "elsewhere": len(rows) - len(source),
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
            out[key] = await read(session, repo_id)
        except Exception:
            out[key] = None
    return out
