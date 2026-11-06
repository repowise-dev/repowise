"""A directory target read from the indexed files under it.

Git metadata is stored per file, so a directory has no row of its own: every
directory answer (get_why's origin, get_context's module card) is built from
its files' rows. Per-file commit counts are never summed, since a commit that
touches two files would count twice.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import GitMetadata
from repowise.server.mcp_server._helpers import (
    LIKE_ESCAPE,
    _build_origin_story,
    _link_decisions_to_commits,
    escape_like,
    is_excluded,
)

#: Newest stored commits a directory origin lists, across its files; the same
#: five the origin cap in tool_why/caps.py serves, so none is built to be dropped.
_DIRECTORY_KEY_COMMITS = 5


def _dir_prefix(path: str) -> str:
    """*path* as a directory prefix of indexed file paths; ``""`` for the root."""
    clean = path.replace("\\", "/").removeprefix("./").strip("/")
    return "" if clean in ("", ".") else clean + "/"


def _rows_under(path: str, all_git_meta: list, exclude_spec: Any) -> list:
    """The git rows of the non-excluded files under *path* as a directory."""
    prefix = _dir_prefix(path)
    return [
        m
        for m in all_git_meta
        if m.file_path.startswith(prefix) and not is_excluded(m.file_path, exclude_spec)
    ]


async def _git_rows_under(
    session: AsyncSession, repository_id: Any, path: str, exclude_spec: Any, *columns: Any
) -> list:
    """*columns* (``file_path`` first) of the non-excluded files under *path*."""
    res = await session.execute(
        select(*columns).where(
            GitMetadata.repository_id == repository_id,
            GitMetadata.file_path.like(f"{escape_like(_dir_prefix(path))}%", escape=LIKE_ESCAPE),
        )
    )
    # SQLite's LIKE ignores case; _rows_under's exact prefix decides.
    return _rows_under(path, list(res.all()), exclude_spec)


async def _indexed_under(
    session: AsyncSession, repository_id: Any, path: str, exclude_spec: Any
) -> bool:
    """Whether a non-excluded indexed file lives under *path*, reading paths only."""
    return bool(
        await _git_rows_under(session, repository_id, path, exclude_spec, GitMetadata.file_path)
    )


def _date_or_unknown(pick: Any, stamps: list) -> str:
    """``pick`` (min or max) of the known *stamps* as a date, else ``"unknown"``."""
    known = [t for t in stamps if t]
    return pick(known).strftime("%Y-%m-%d") if known else "unknown"


def _newest_distinct_commits(file_metas: list) -> list[dict]:
    """Every file's stored significant commits, one row per SHA, newest first."""
    by_sha: dict[str, dict] = {}
    for m in file_metas:
        for c in json.loads(m.significant_commits_json or "[]"):
            if c.get("sha"):
                by_sha.setdefault(c["sha"], c)
    return sorted(by_sha.values(), key=lambda c: c.get("date", ""), reverse=True)


def _build_directory_origin(
    dir_path: str,
    file_metas: list,
    governing_decisions: list[dict],
) -> dict:
    """The origin of a directory from its files' stored git metadata.

    Git metadata is stored per file, so a directory has no commit total, owner
    or age of its own: summing per-file counts would count a shared commit once
    per file. Only what holds across files is served: the date span and the
    stored significant commits, one row per SHA.
    """
    if not file_metas:
        return _build_origin_story(dir_path, None, governing_decisions)
    first_date = _date_or_unknown(min, [m.first_commit_at for m in file_metas])
    last_date = _date_or_unknown(max, [m.last_commit_at for m in file_metas])
    key_commits = _newest_distinct_commits(file_metas)[:_DIRECTORY_KEY_COMMITS]
    return {
        "available": True,
        "scope": "directory",
        "files_with_history": len(file_metas),
        "first_commit": first_date,
        "last_commit": last_date,
        "key_commits": key_commits,
        # Over the served commits only, so a large directory stays bounded.
        "linked_decisions": _link_decisions_to_commits(governing_decisions, key_commits),
        "summary": (
            f"{dir_path} is a directory: {len(file_metas)} file(s) under it have "
            f"git history, first commit {first_date}, last commit {last_date}. "
            "History is stored per file, so there is no commit count or owner for "
            "the directory itself; pass a file under it for that file's origin."
        ),
    }
