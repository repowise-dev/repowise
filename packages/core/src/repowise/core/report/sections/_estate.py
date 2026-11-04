"""What the estate sections share: the missing-artifact item, the evidence wording
and the git facts read from each member's index."""

from __future__ import annotations

from typing import Any

from repowise.core.report.model import WorkspaceReportContext, check


def missing_graph(name: str, method: str) -> list[dict[str, Any]]:
    """The one item a section gives when the workspace has no stored system graph."""
    return [
        check(
            name,
            reason=(
                "no system graph is stored for this workspace; 'repowise update --workspace' "
                "builds it once two or more member repos are indexed"
            ),
            method=method,
            basis="unresolved",
        )
    ]


def evidence_note(evidence: Any) -> str:
    """``""`` when the graph's evidence is measured, else ``"; <state> evidence (<codes>)"``.

    The reasons are spelled out once, in the estate summary's ``evidence`` item.
    """
    if evidence is None or evidence.state == "measured":
        return ""
    codes = ", ".join(r.code.replace("_", " ") for r in evidence.reasons)
    return f"; {evidence.state} evidence ({codes})"


#: Files listed per repo for a churn or bug-magnet roll-up; the rest are counted.
TOP_FILES = 5


async def _git_facts(session: Any, repository_id: str) -> dict[str, Any]:
    from sqlalchemy import func, select

    from repowise.core.persistence.crud import code_file_rows
    from repowise.core.persistence.models import GitCommit, GitMetadata

    commits, newest = (
        await session.execute(
            select(func.count(), func.max(GitCommit.committed_at)).where(
                GitCommit.repository_id == repository_id
            )
        )
    ).one()
    files, with_fixes = (
        await session.execute(
            select(func.count(), func.count().filter(GitMetadata.prior_defect_count > 0)).where(
                code_file_rows(repository_id)
            )
        )
    ).one()
    hot = (
        await session.execute(
            select(GitMetadata.file_path)
            .where(code_file_rows(repository_id), GitMetadata.is_hotspot.is_(True))
            .order_by(GitMetadata.temporal_hotspot_score.desc(), GitMetadata.file_path)
        )
    ).scalars().all()
    magnets = (
        await session.execute(
            select(GitMetadata.file_path, GitMetadata.prior_defect_count, GitMetadata.last_fix_at)
            .where(code_file_rows(repository_id), GitMetadata.bug_magnet.is_(True))
            .order_by(GitMetadata.fix_mass.desc(), GitMetadata.file_path)
        )
    ).all()
    return {
        "commits": int(commits or 0),
        "newest_commit": newest.date().isoformat() if newest else None,
        "files": int(files or 0),
        "files_with_fixes": int(with_fixes or 0),
        "hotspots": len(hot),
        "top_hotspots": list(hot[:TOP_FILES]),
        "bug_magnets": len(magnets),
        "top_bug_magnets": [
            {
                "file": path,
                "fixes": int(fixes or 0),
                "last_fix": last.date().isoformat() if last else None,
            }
            for path, fixes, last in magnets[:TOP_FILES]
        ],
    }


async def git_history(ctx: WorkspaceReportContext) -> dict[str, Any]:
    """``alias -> git facts`` per indexed member (an exception when its index was unreadable):
    commits indexed and the newest one's date, code files, churn hotspots and bug magnets
    with their top files."""
    return await ctx.each_index("git_history", _git_facts)


def unread(rows: dict[str, Any]) -> list[str]:
    """Members whose index could not be read, as ``alias (error)``."""
    return [f"{a} ({type(v).__name__})" for a, v in sorted(rows.items()) if isinstance(v, Exception)]
