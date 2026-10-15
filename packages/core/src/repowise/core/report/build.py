"""Build the assessment document from the registered sections.

The caller opens each repository's index (the CLI does it per repo, single
or workspace) and passes a :class:`ReportContext`; this runs the sections
and assembles one versioned document. Paths in the document are relative to
each repository; no local absolute path is written.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repowise.core.report.model import (
    DOCS_URL,
    SCHEMA_VERSION,
    ReportContext,
    Section,
    check,
    report_sections,
)

logger = logging.getLogger(__name__)

NOTICE = (
    "This report is evidence about code and components, gathered from the index. "
    "It is not a score of people: owners are counted, never ranked or named."
)


def available_sections(report: str = "assessment") -> list[tuple[str, Section]]:
    """Every registered section of the *report* kind, local ones and plugins', in report order."""
    import repowise.core.report.sections  # noqa: F401  (registers the local sections)
    from repowise.core.registry.plugins import load_plugins

    load_plugins()
    pairs = [(name, report_sections.get(name)) for name in report_sections.names]
    return sorted(
        ((n, s) for n, s in pairs if s.report == report), key=lambda p: (p[1].order, p[0])
    )


async def _index_entry(ctx: ReportContext) -> dict[str, Any]:
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository, SnapshotRun

    repo = await ctx.session.get(Repository, ctx.repository_id)
    snap = await ctx.session.scalar(
        select(SnapshotRun)
        .where(SnapshotRun.repository_id == ctx.repository_id)
        .order_by(SnapshotRun.taken_at.desc(), SnapshotRun.id.desc())
        .limit(1)
    )
    return {
        "alias": ctx.alias,
        "name": repo.name if repo else ctx.repo_root.name,
        "head_commit": ctx.head_commit,
        "last_snapshot": (
            {"taken_at": snap.taken_at.isoformat(), "stamp": snap.stamp} if snap else None
        ),
    }


async def collect_repo(
    ctx: ReportContext, sections: list[tuple[str, Section]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``(repos entry, sections)`` for one repository."""
    out: list[dict[str, Any]] = []
    for name, section in sections:
        try:
            checks = await section.collect(ctx)
        except Exception as exc:  # one broken section must not lose the rest
            logger.warning("report section %s failed: %s", name, exc)
            await ctx.session.rollback()  # a failed read leaves the session unusable
            checks = [
                check(
                    name,
                    reason=f"the section failed to collect ({type(exc).__name__})",
                    details=[str(exc)],
                    method=section.summary,
                    basis="unresolved",
                )
            ]
            failed = True
        else:
            failed = False
        if checks is None:
            continue
        doc = {"url": DOCS_URL, "short": section.summary}
        for item in checks:
            item.setdefault("documentation", doc)
        entry = {
            "id": name,
            "title": section.title,
            "summary": section.summary,
            "repo": ctx.alias,
            "checks": checks,
        }
        if failed:
            entry["failed"] = True
        out.append(scrub(entry, ctx.repo_root))
    return await _index_entry(ctx), out


#: Absolute paths that are not the repository's own: a drive path, a UNC
#: share, or a POSIX path under a top-level directory a machine path starts in.
#: Ceiling: a POSIX path under another top-level directory is kept; contract ids
#: such as ``http::GET::/users`` must survive, so a bare ``/x`` cannot be matched.
_ABSOLUTE = re.compile(
    r"(?<!\w)(?:[A-Za-z]:[\\/]|\\\\)[^\s'\"<>|]*"
    r"|(?<![\w:/.])/(?:home|Users|tmp|var|private|mnt|opt|root|srv|etc|usr|Volumes)/"
    r"[^\s'\"<>|]*"
)


def _scrub_text(text: str, roots: tuple[str, ...]) -> str:
    for root in roots:
        text = text.replace(root + "/", "").replace(root + "\\", "").replace(root, ".")
    return _ABSOLUTE.sub("<path>", text)


def scrub(value: Any, repo_root: Path) -> Any:
    """*value* with the repository root made relative and any other absolute
    path replaced, in every string it holds, so a report handed on never
    carries a local path (a catalog read error quotes one, for instance)."""
    resolved = Path(repo_root).resolve()
    forms = {str(repo_root), str(resolved), resolved.as_posix(), Path(repo_root).as_posix()}
    roots = tuple(sorted((f for f in forms if f and f != "."), key=len, reverse=True))

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            return _scrub_text(v, roots)
        if isinstance(v, dict):
            return {walk(k): walk(x) for k, x in v.items()}
        if isinstance(v, list | tuple):
            return [walk(x) for x in v]
        return v

    return walk(value)


def assemble(
    scope: dict[str, Any],
    parts: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """The canonical document. *scope* is ``{"kind": "repo"|"workspace", "name": ...}``."""
    from repowise.core import __version__
    from repowise.core.events.snapshot import current_stamp

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "assessment",
        "generated_at": (generated_at or datetime.now(UTC)).replace(microsecond=0).isoformat(),
        "generator": {"name": "repowise", "version": __version__, "stamp": current_stamp()},
        "scope": scope,
        "notice": NOTICE,
        "repos": [entry for entry, _ in parts],
        "sections": [s for _, sections in parts for s in sections],
    }


__all__ = ["NOTICE", "assemble", "available_sections", "collect_repo", "scrub"]
