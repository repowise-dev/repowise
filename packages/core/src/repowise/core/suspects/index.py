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
from .rank import (
    Candidate,
    PreparedSymptom,
    SuspectContext,
    Symptom,
    prepare,
    rank_suspects,
    resolvable,
    resolve_trace,
)

#: The window the measurement used (prereg/suspects.md): 14 days before the symptom.
DEFAULT_WINDOW_DAYS = 14
DEFAULT_LIMIT = 10
#: Said on every answer: the order is a reading order, confirmation is bisect's.
NOTE = (
    "An order to read the changes in, from the evidence each row lists; not a cause "
    "and not a probability. Confirm with `git bisect run`."
)


def _note(since: datetime, rows: list[dict[str, Any]], capped: bool, commit_limit: int) -> str:
    """:data:`NOTE`, plus how to read further back when this window is not enough.

    A window that may start before the oldest stored change needs a deeper index;
    one where no listed row has evidence beyond its merge time (``recency`` fires
    on every candidate) needs an earlier ``since``. The CLI, REST and chat take
    ``since``; MCP does not, so the note names the argument and gives the CLI
    command as the step that works from every surface. Re-indexing is the CLI's.
    """
    if capped:
        return (
            f"{NOTE} This window holds at least `commit_limit` ({commit_limit}) changes, "
            "so older changes in it may not be indexed: re-index with "
            f"`repowise init --commit-limit` above {commit_limit} to read them."
        )
    if any(set(r["signals"]) - {"recency"} for r in rows):
        return NOTE
    return (
        f"{NOTE} No change listed has evidence beyond its merge time, and none merged "
        f"before {since:%Y-%m-%d} was read: the cause may be older. Read an earlier window with `since`, "
        "or run `repowise suspects --since <an earlier date>` with the same --symptom, "
        "--files or --trace."
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def repo_terms(repo_path: Path | str) -> frozenset[str]:
    """The repo's own name as search words: they match every change."""
    return frozenset(meaningful_terms(Path(repo_path).resolve().name.replace("-", " ")))


def _tracked_files(repo_path: Path) -> frozenset[str]:
    return frozenset((_git(repo_path, "ls-files") or "").splitlines())


async def _imports(session: Any, repo_id: str) -> dict[str, set[str]]:
    from repowise.core.persistence.crud.graph import get_file_import_edges

    adj: dict[str, set[str]] = {}
    for u, v in await get_file_import_edges(session, repo_id):
        if u == v:
            continue
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)
    return adj


async def _resolve_trace(
    session: Any, repo_id: str, repo_path: Path, prepared: PreparedSymptom
) -> PreparedSymptom:
    """*prepared* with trace frames joined to the indexed symbols they ran in.

    Unchanged when the index records no commit or git cannot say which trace
    files differ from it: a frame is joined only against the code that ran.
    ``head_commit`` is the commit the last index run stamped; an index whose
    symbols were written from another commit is not detected here.
    """
    from sqlalchemy import select

    from repowise.core.frame_resolver import checkout_reader, load_frame_symbols
    from repowise.core.persistence.models import Repository

    files = list(prepared.trace_files)
    if not files:
        return prepared
    head = (
        await session.execute(select(Repository.head_commit).where(Repository.id == repo_id))
    ).scalar()
    if not head:
        return prepared
    changed = await asyncio.to_thread(
        _git, repo_path, "--literal-pathspecs", "diff", "--name-only", "--relative", head, "--", *files
    )
    if changed is None:
        return prepared
    loaded, origins = await load_frame_symbols(session, repo_id, files)
    # Every trace file maps to the repo already, so one with no symbols is indexed.
    symbols = {f: [] for f in files} | loaded

    return await asyncio.to_thread(
        lambda: resolve_trace(
            prepared,
            root=str(repo_path),
            symbols_by_file=symbols,
            dirty_paths=set(changed.splitlines()),
            origins=origins,
            read_lines=checkout_reader(repo_path),
        )
    )


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
            "suspects": [], "note": _note(since, [], False, 0),
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
    if any(resolvable(f) for f in prepared.frames):
        prepared = await _resolve_trace(session, repo_id, repo_path, prepared)
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
    capped = len(rows) >= commit_limit
    return {
        "window": window,
        "candidates": len(candidates),
        "candidates_capped": capped,
        # Changes git could not read: ranked on their title and files alone.
        "texts_missing": sum(1 for r in rows if r.head_sha not in texts),
        "symptom_files": list(named),
        "suspects": out,
        "note": _note(since, out, capped, commit_limit),
    }


__all__ = ["DEFAULT_LIMIT", "DEFAULT_WINDOW_DAYS", "NOTE", "find_suspects", "repo_terms"]
