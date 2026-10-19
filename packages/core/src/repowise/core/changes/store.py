"""Keeps the ``changes`` table in step with each index and update."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import structlog

from .reconstruct import ReconstructedChange, reconstruct_changes
from .source import ChangeMetadata, resolve_source

logger = structlog.get_logger(__name__)

#: Files kept on a row; ``files_changed`` carries the full count past it.
MAX_STORED_FILES = 1000


def change_provenance(
    commits: list[str], labels: Mapping[str, tuple[str | None, str | None]]
) -> tuple[str, int, int]:
    """``(label, agent commits, known commits)`` over a change's commits.

    Agent when any commit is an agent's, human-attested only when every commit
    is, unknown otherwise. A commit missing from ``git_commits`` is unknown.
    """
    from repowise.core.attribution import AGENT, HUMAN_ATTESTED, UNKNOWN, commit_label

    states = [commit_label(*labels[s]) if s in labels else UNKNOWN for s in commits]
    agent = states.count(AGENT)
    known = agent + states.count(HUMAN_ATTESTED)
    if agent:
        label = AGENT
    elif commits and known == len(commits):
        label = HUMAN_ATTESTED
    else:
        label = UNKNOWN
    return label, agent, known


def change_row(
    change: ReconstructedChange,
    *,
    provenance: tuple[str, int, int] | None,
    source: str,
    metadata: ChangeMetadata | None,
) -> dict[str, Any]:
    meta = metadata or ChangeMetadata()

    def _json(value: tuple[str, ...] | None) -> str | None:
        return None if value is None else json.dumps(list(value))

    return {
        "change_id": change.change_id,
        "identity_kind": change.identity_kind,
        "identity_value": change.identity_value,
        "shape": change.shape,
        "forge": change.forge,
        "forge_ref": change.forge_ref,
        "title": change.title,
        "merged_at": change.merged_at,
        "head_sha": change.head_sha,
        "author_name": change.author_name,
        "author_email": change.author_email,
        "commit_shas_json": json.dumps(change.commit_shas),
        "files_json": json.dumps(
            [{"path": p, "added": a, "deleted": d} for p, a, d in change.files[:MAX_STORED_FILES]]
        ),
        "files_changed": len(change.files),
        "lines_added": change.lines_added,
        "lines_deleted": change.lines_deleted,
        "category": change.category,
        "provenance": provenance[0] if provenance else None,
        "provenance_agent": provenance[1] if provenance else None,
        "provenance_known": provenance[2] if provenance else None,
        "metadata_source": source,
        "reviewers_json": _json(meta.reviewers),
        "approvals": meta.approvals,
        "labels_json": _json(meta.labels),
        "linked_issues_json": _json(meta.linked_issues),
    }


def _read_changes(
    repo_path: Path, limit: int, since: str | None, taken: dict[str, str]
) -> list[ReconstructedChange] | None:
    import git as gitpython

    from repowise.core.forges import detect_forge
    from repowise.core.ingestion.git_indexer.indexer import GitIndexer

    repo = gitpython.Repo(repo_path, search_parent_directories=True)
    try:
        # A partial clone would fetch blobs for --numstat: never from here.
        if GitIndexer._partial_clone_filter(repo) is not None:
            return []
        return reconstruct_changes(
            repo, detect_forge(repo_path), limit=limit, since_sha=since, taken=taken
        )
    finally:
        with contextlib.suppress(Exception):
            repo.close()


async def refresh_changes(
    session: Any, repo_id: str, repo_path: Path | str, *, rebuild: bool
) -> int:
    """Rebuild (full index) or extend (update) the repository's changes.

    The window is the repo's ``commit_limit`` first-parent commits, like the
    commit table. An update reads only changes after the newest stored one and
    rebuilds when that one is no longer on HEAD's history. Provenance is read
    from the stored commit labels, so it runs after those are written, and is
    left out while attribution is off. Returns how many rows were written;
    failures are logged and leave the table as it was.
    """
    from repowise.core.attribution import attribution_enabled
    from repowise.core.ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT
    from repowise.core.persistence.crud import (
        delete_changes,
        get_change_heads,
        get_commit_labels,
        get_newest_change_head,
        upsert_changes,
    )
    from repowise.core.repo_config import load_repo_config

    repo_path = Path(repo_path)
    try:
        limit = int(load_repo_config(repo_path).get("commit_limit") or _DEFAULT_COMMIT_LIMIT)
        since = None if rebuild else await get_newest_change_head(session, repo_id)
        taken = {} if since is None else await get_change_heads(session, repo_id)
        changes = await asyncio.to_thread(_read_changes, repo_path, limit, since, taken)
        if changes is None:
            rebuild, since = True, None
            changes = await asyncio.to_thread(_read_changes, repo_path, limit, None, {})
        if since is None:
            rebuild = True
        changes = changes or []

        labels: dict[str, tuple[str | None, str | None]] | None = None
        if attribution_enabled(repo_path):
            labels = await get_commit_labels(
                session, repo_id, [s for c in changes for s in c.commit_shas]
            )
        source_name, source = resolve_source()
        metadata = await asyncio.to_thread(source.metadata, repo_path, changes) if changes else {}
        rows = [
            change_row(
                c,
                provenance=change_provenance(c.commit_shas, labels) if labels is not None else None,
                source=source_name,
                metadata=metadata.get(c.change_id),
            )
            for c in changes
        ]
        if rebuild:
            await delete_changes(session, repo_id)
        if rows:
            await upsert_changes(session, repo_id, rows)
        return len(rows)
    except Exception as exc:
        logger.warning("changes_refresh_failed", repo_id=repo_id, error=str(exc))
        return 0


async def reconcile_categories(session: Any, repo_id: str) -> int:
    """Re-label commits and changes the current classifier reads differently.

    Pure regex over stored subjects and titles, so an update keeps both
    columns in step with the classifier without a re-index.
    """
    from sqlalchemy import update

    from repowise.core.ingestion.git_indexer._constants import classify_commit_category
    from repowise.core.persistence.crud import get_category_inputs, upsert_git_commits_bulk
    from repowise.core.persistence.models import Change

    commits, changes = await get_category_inputs(session, repo_id)
    commit_rows = [
        {"sha": sha, "category": label}
        for sha, subject, category in commits
        if (label := classify_commit_category(subject or "")) != category
    ]
    if commit_rows:
        await upsert_git_commits_bulk(session, repo_id, commit_rows)
    moved = 0
    for change_id, title, category in changes:
        label = classify_commit_category(title or "")
        if label != category:
            moved += 1
            await session.execute(
                update(Change)
                .where(Change.repository_id == repo_id, Change.change_id == change_id)
                .values(category=label)
            )
    return len(commit_rows) + moved


__all__ = [
    "MAX_STORED_FILES",
    "change_provenance",
    "change_row",
    "reconcile_categories",
    "refresh_changes",
]
