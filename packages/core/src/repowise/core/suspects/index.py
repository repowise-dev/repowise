"""Suspects read from an index: the one entry every surface calls.

Candidates are the stored Change Records merged in ``[since, until)``; the
import graph and co-change partners come from the same index; message bodies
and touched symbol names from git. Each row is the change's list fields, its
rank, and every signal that fired with its own rank and evidence.

Symptom files are mapped onto the files tracked at HEAD, so a file renamed or
deleted inside the window may name no file and add no graph or co-change
evidence.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from repowise.core.fts_query import meaningful_terms

from .git_text import _git, read_change_texts
from .rank import Candidate, SuspectContext, Symptom, prepare, rank_suspects

#: The window the measurement used (prereg/suspects.md): 14 days before the symptom.
DEFAULT_WINDOW_DAYS = 14
DEFAULT_LIMIT = 10
#: Said on every answer: the order is a reading order, confirmation is bisect's.
NOTE = (
    "An order to read the changes in, from the evidence each row lists; not a cause "
    "and not a probability. Confirm with `git bisect run`."
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def repo_terms(repo_path: Path | str) -> frozenset[str]:
    """The repo's own name as search words: they match every change."""
    return frozenset(meaningful_terms(Path(repo_path).resolve().name.replace("-", " ")))


def _tracked_files(repo_path: Path) -> frozenset[str]:
    return frozenset((_git(repo_path, "ls-files") or "").splitlines())


async def _imports(session: Any, repo_id: str) -> dict[str, set[str]]:
    from sqlalchemy import select

    from repowise.core.persistence.models import GraphEdge

    rows = await session.execute(
        select(GraphEdge.source_node_id, GraphEdge.target_node_id).where(
            GraphEdge.repository_id == repo_id, GraphEdge.edge_type == "imports"
        )
    )
    adj: dict[str, set[str]] = {}
    for u, v in rows.all():
        if "::" in u or "::" in v or u == v:
            continue
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)
    return adj


def _row(row: Any, attribution: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"id": row.change_id}
    if row.forge_ref:
        out["ref"] = row.forge_ref
    out.update(
        title=row.title,
        merged_at=_aware(row.merged_at).isoformat() if row.merged_at else None,
        category=row.category,
        head_sha=row.head_sha,
        files_changed=row.files_changed,
    )
    if attribution and row.provenance is not None:
        out["provenance"] = row.provenance
    return out


async def find_suspects(
    session: Any,
    repo_id: str,
    repo_path: Path | str,
    symptom: Symptom,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Ranked suspects for *symptom* among changes merged in ``[since, until)``.

    *until* defaults to the symptom time, else now; *since* to 14 days before it.
    """
    from repowise.core.attribution import attribution_enabled
    from repowise.core.ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT
    from repowise.core.persistence.crud import get_co_change_partners, list_changes
    from repowise.core.persistence.crud.changes import ChangeFilter
    from repowise.core.repo_config import load_repo_config

    repo_path = Path(repo_path)
    until = _aware(until or symptom.at or datetime.now(UTC))
    since = _aware(since) if since else until - timedelta(days=DEFAULT_WINDOW_DAYS)
    symptom = Symptom(symptom.text, symptom.files, symptom.trace, symptom.at or until)
    rows, _total = await list_changes(
        session, repo_id, ChangeFilter(since=since, until=until), limit=None
    )
    rows = [r for r in rows if r.merged_at is not None]
    window = {"since": since.isoformat(), "until": until.isoformat()}
    if not rows:
        return {
            "window": window, "candidates": 0, "candidates_capped": False,
            "suspects": [], "note": NOTE,
        }

    commit_limit = int(load_repo_config(repo_path).get("commit_limit") or 0) or _DEFAULT_COMMIT_LIMIT
    files_of = {
        r.change_id: tuple(f["path"] for f in json.loads(r.files_json or "[]")) for r in rows
    }
    texts = await asyncio.to_thread(
        read_change_texts, repo_path, [(r.head_sha, r.files_changed) for r in rows]
    )
    candidates = [
        Candidate(
            r.change_id, r.title, _aware(r.merged_at), files_of[r.change_id],
            *texts.get(r.head_sha, ("", ())),
        )
        for r in rows
    ]
    repo_files = await asyncio.to_thread(_tracked_files, repo_path)
    prepared = prepare(symptom, SuspectContext(repo_files=repo_files))
    named = prepared.files
    ctx = SuspectContext(
        repo_files=repo_files,
        repo_terms=repo_terms(repo_path),
        imports=await _imports(session, repo_id) if named else {},
        co_change=await get_co_change_partners(session, repo_id, list(named)) if named else {},
    )
    ranked = await asyncio.to_thread(
        lambda: rank_suspects(symptom, candidates, ctx, prepared=prepared)
    )
    by_id = {r.change_id: r for r in rows}
    attribution = attribution_enabled(repo_path)
    out = []
    for s in ranked[: max(limit, 0)]:
        row = _row(by_id[s.change_id], attribution)
        row["rank"] = s.rank
        row["signals"] = {
            name: {"rank": sig.rank, "evidence": list(sig.evidence)} for name, sig in s.signals.items()
        }
        out.append(row)
    return {
        "window": window,
        "candidates": len(candidates),
        "candidates_capped": len(rows) >= commit_limit,
        # Changes git could not read: ranked on their title and files alone.
        "texts_missing": sum(1 for r in rows if r.head_sha not in texts),
        "symptom_files": list(named),
        "suspects": out,
        "note": NOTE,
    }


__all__ = ["DEFAULT_LIMIT", "DEFAULT_WINDOW_DAYS", "NOTE", "find_suspects", "repo_terms"]
