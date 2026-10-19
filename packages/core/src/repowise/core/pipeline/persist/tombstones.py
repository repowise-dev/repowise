"""Tombstoning and stale-marking of wiki pages whose source left the index."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import structlog

# Patchable steps are looked up on the package at call time, so a patch on
# ``repowise.core.pipeline.persist`` reaches this module too.
from repowise.core.pipeline import persist as _persist

from .prune import _prune_stale_file_rows

logger = structlog.get_logger("repowise.core.pipeline.persist")


#: Candidate paths per spotlight query: each path is one ``LIKE`` term, and
#: SQLite caps how deep an expression tree may grow.
_SPOTLIGHT_PATHS_PER_QUERY = 200

# A second, stdlib logger for the one message here that has to survive
# ``repowise init``. ``configure_cli_logging`` pins both ``repowise.core`` and
# structlog to ERROR for the progress-bar commands, so anything below ERROR is
# discarded in the exact command that writes the index. The refusal below is
# logged through this at ERROR for that reason; the routine counts stay on
# structlog with their siblings.
_log = logging.getLogger("repowise.core.pipeline.persist")

# Max page ids per UPDATE ... IN (...) so a large cascade cannot exceed
# SQLite's bound-variable limit on the local CLI store.
_STALE_ID_CHUNK = 500


def tombstone_candidates(file_diffs: list[Any]) -> list[tuple[str, list[str]]]:
    """(dead_path, successor_paths) pairs from deleted/renamed file diffs.

    A renamed file's old path gets its new path as the successor; a deleted
    file has no successor. Diffs of other statuses contribute nothing.
    """
    out: list[tuple[str, list[str]]] = []
    for fd in file_diffs or []:
        status = getattr(fd, "status", None)
        if status == "deleted" and fd.path:
            out.append((fd.path, []))
        elif status == "renamed" and fd.old_path:
            out.append((fd.old_path, [fd.path] if fd.path else []))
    return out


async def mark_tombstone_pages(
    session: Any, repo_id: str, candidates: list[tuple[str, list[str]]]
) -> list[str]:
    """Mark the pages of deleted/renamed files as tombstones: each file's page
    and the symbol spotlight pages of the symbols it defined.

    A ``freshness_status="fresh"`` page for a file that no longer exists is
    an active trap: retrieval serves it, agents cite it, and the index-age
    metadata says nothing is wrong. Tombstoned pages keep their content
    (the prose may still orient a reader) but carry status=tombstone and
    ``successor_paths`` in metadata so serving layers can skip or redirect.

    Returns the page ids marked, so the caller can drop them from the
    full-text index once its session has closed. Every serving layer already
    discards a tombstone, but retrieval fetches a fixed number of rows before
    any of that runs, so a tombstone still takes one of those slots and pushes
    a real candidate out of the fetch. The row has to go, and only the caller
    knows when it is safe to write to the index — on SQLite it shares a file
    with this session.
    """
    if not candidates:
        return []
    from sqlalchemy import or_, select

    from repowise.core.persistence.models import Page

    page_ids = {f"file_page:{path}": (path, successors) for path, successors in candidates}
    pages = list(
        (
            await session.execute(
                select(Page).where(Page.repository_id == repo_id, Page.id.in_(page_ids))
            )
        ).scalars()
    )
    # A symbol spotlight page targets ``<path>::<symbol>``, so its id is never
    # ``file_page:<path>``; it goes with its file all the same. Matched on the
    # ``<path>::`` prefix, so a file whose name merely starts with *path* stays.
    successors_by_path = dict(candidates)
    paths = list(successors_by_path)
    for start in range(0, len(paths), _SPOTLIGHT_PATHS_PER_QUERY):
        chunk = paths[start : start + _SPOTLIGHT_PATHS_PER_QUERY]
        res = await session.execute(
            select(Page).where(
                Page.repository_id == repo_id,
                Page.page_type == "symbol_spotlight",
                or_(*(Page.target_path.startswith(f"{p}::", autoescape=True) for p in chunk)),
            )
        )
        for page in res.scalars():
            path = (page.target_path or "").split("::", 1)[0]
            # SQLite LIKE ignores ASCII case, so ``pkg/Foo.py::`` also matches
            # a live ``pkg/foo.py``; keep only exact path matches.
            if path not in successors_by_path:
                continue
            page_ids[page.id] = (path, successors_by_path.get(path, []))
            pages.append(page)
    marked: list[str] = []
    for page in pages:
        _, successors = page_ids[page.id]
        page.freshness_status = "tombstone"
        try:
            meta = json.loads(page.metadata_json or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        meta["successor_paths"] = successors
        page.metadata_json = json.dumps(meta)
        marked.append(page.id)
    if marked:
        logger.info("pages_tombstoned", repo_id=repo_id, count=len(marked))
    elif candidates:
        # Candidates existed but no page matched — the id scheme drifted from
        # ``file_page:{path}`` or the paths don't line up. Silent success here
        # would let stale pages keep serving, so surface it.
        logger.debug(
            "tombstone_no_match",
            repo_id=repo_id,
            candidate_count=len(candidates),
            sample=[p for p, _ in candidates[:3]],
        )
    return marked


async def tombstone_file_pages_outside_scope(
    session: Any, repo_id: str, current_file_paths: set[str]
) -> list[str]:
    """Tombstone active source-backed pages outside a full traversal's scope.

    Files removed by ``exclude_patterns`` still exist on disk, so the ordinary
    deleted-file tombstone path cannot identify them.  A successful full
    traversal can: any active file page outside its parsed-file set is no longer
    part of this index.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import Page

    pages = (
        await session.execute(
            select(Page).where(
                Page.repository_id == repo_id,
                Page.page_type.in_(("file_page", "api_contract", "infra_page", "symbol_spotlight")),
                Page.freshness_status != "tombstone",
            )
        )
    ).scalars()
    marked: list[str] = []
    for page in pages:
        target = page.target_path or ""
        source_path = target.split("::", 1)[0] if page.page_type == "symbol_spotlight" else target
        if source_path in current_file_paths:
            continue
        page.freshness_status = "tombstone"
        try:
            metadata = json.loads(page.metadata_json or "{}")
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        metadata["successor_paths"] = []
        page.metadata_json = json.dumps(metadata)
        marked.append(page.id)
    return marked


async def reconcile_full_index_scope(
    session: Any,
    repo_id: str,
    current_graph_paths: set[str],
    current_git_paths: set[str],
) -> list[str]:
    """Reconcile every source-backed store against one successful traversal."""
    tombstoned = await tombstone_file_pages_outside_scope(session, repo_id, current_graph_paths)
    await _prune_stale_file_rows(
        session,
        repo_id,
        current_graph_paths,
        current_git_paths,
        authoritative_empty=True,
    )
    from repowise.core.persistence.crud.decisions import (
        purge_proposed_decisions_outside_files,
    )

    await purge_proposed_decisions_outside_files(session, repo_id, current_graph_paths)
    return tombstoned


async def tombstone_pages_outside_generation(
    session: Any, repo_id: str, current_page_ids: set[str]
) -> list[str]:
    """Retire generated pages absent from an authoritative full generation.

    This is used only when generation configuration changed and the update ran
    the complete generation ladder. It covers selection changes such as page
    caps, spotlight ranking, and disabling onboarding, none of which imply that
    the backing source file disappeared.
    """
    from sqlalchemy import select

    from repowise.core.generation.models import GENERATION_LEVELS
    from repowise.core.persistence.models import Page

    pages = (
        await session.execute(
            select(Page).where(
                Page.repository_id == repo_id,
                Page.page_type.in_(tuple(GENERATION_LEVELS)),
                Page.freshness_status != "tombstone",
            )
        )
    ).scalars()
    marked: list[str] = []
    for page in pages:
        if page.id in current_page_ids:
            continue
        page.freshness_status = "tombstone"
        try:
            metadata = json.loads(page.metadata_json or "{}")
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        metadata["successor_paths"] = []
        page.metadata_json = json.dumps(metadata)
        marked.append(page.id)
    return marked


async def tombstone_absent_file_pages(
    session: Any, repo_id: str, repo_path: Path | str
) -> list[str]:
    """Tombstone every file page whose file is no longer on disk.

    :func:`mark_tombstone_pages` works from a diff, so it only ever sees a
    file that was deleted or renamed *between two commits this run compared*.
    ``repowise init`` compares nothing — it indexes a checkout as it stands —
    so it has never tombstoned anything, and a page written before a file was
    deleted keeps ``freshness_status='fresh'`` through every later index.

    That page is the trap the tombstone exists to close: retrieval serves it,
    agents cite it, and the index-age metadata says nothing is wrong.

    The check is a plain existence test against the checkout rather than a
    comparison with what this run parsed. A file can be present but unparsed —
    an unsupported extension, a parse failure, a changed exclude list — and
    such a file is stale, not deleted. Calling it deleted would put a
    ``successor_paths: []`` on a page whose file a reader can still open.

    Returns the page ids marked, so the caller can drop them from the
    full-text index once its session has closed, on the same terms as
    :func:`mark_tombstone_pages`.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import Page

    root = Path(repo_path)
    res = await session.execute(
        select(Page).where(
            Page.repository_id == repo_id,
            Page.page_type == "file_page",
            Page.freshness_status != "tombstone",
        )
    )
    live = list(res.scalars().all())
    if not live:
        return []

    absent = [p for p in live if not (root / (p.target_path or "")).exists()]
    if not absent:
        return []

    # Every page absent means the paths are not being resolved against the
    # tree they were written from — a wrong root, a bare-repo checkout, a
    # working copy that has not been restored yet. Tombstoning the whole wiki
    # on that reading is worse than leaving it, and unlike a partial mistake
    # it is not recoverable by re-indexing a file. Refuse, loudly, and let the
    # rest of the run proceed.
    if len(absent) == len(live):
        _log.error(
            "tombstone_sweep_refused repo_id=%s file_pages=%d root=%s: every file "
            "page's path is missing from the checkout, which reads as a wrong "
            "root rather than a deleted repository. Nothing was tombstoned.",
            repo_id,
            len(live),
            root,
        )
        return []

    marked: list[str] = []
    for page in absent:
        page.freshness_status = "tombstone"
        try:
            meta = json.loads(page.metadata_json or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        # Deleted, not renamed: rename detection is the diff-driven sweep's
        # job and it has evidence this one does not.
        meta["successor_paths"] = []
        page.metadata_json = json.dumps(meta)
        marked.append(page.id)

    logger.info(
        "file_pages_tombstoned_absent",
        repo_id=repo_id,
        count=len(marked),
        sample=[p.target_path for p in absent[:3]],
    )
    return marked


async def mark_stale_pages(session: Any, repo_id: str, paths: Iterable[str]) -> int:
    """Decay weakly-affected file pages to ``freshness_status='stale'``.

    ``ChangeDetector.get_affected_pages`` returns ``decay_only`` — pages hit
    by the change cascade but beyond the regeneration budget (budget
    overflow, co-change partners, 2-hop rename fallout). They keep serving
    their existing content, but the stale bit makes the coverage view and
    ``get_stale_pages`` truthful so the next docs run (or a reader) knows
    they lag the code. Only ``fresh`` pages are downgraded — tombstoned or
    already-stale pages keep their stronger status, and pages regenerated in
    this run are never in ``decay_only`` by construction.

    Maps each path to its ``file_page:<path>`` id and hands off to
    :func:`mark_page_ids_stale`, so both entry points share one UPDATE.

    The two were separate copies of the same statement, and only the sibling
    was chunked against ``SQLITE_MAX_VARIABLE_NUMBER``. That is not a crash
    anyone is hitting today: SQLite has defaulted to 32766 bind variables since
    3.32, and a ``decay_only`` set that large would take an enormous cascade.
    It is reachable on the 999-variable builds older interpreters still ship,
    but the reason to collapse the two is narrower than that. They answered the
    same question differently, in the file with the most bug fixes in this repo,
    and now there is one place to answer it.

    Returns the number of pages marked.
    """
    return await _persist.mark_page_ids_stale(
        session, repo_id, (f"file_page:{path}" for path in paths)
    )


async def mark_page_ids_stale(session: Any, repo_id: str, page_ids: Iterable[str]) -> int:
    """Decay arbitrary pages to ``freshness_status='stale'`` by full page id.

    The sibling of :func:`mark_stale_pages`, which only understands
    ``file_page:<path>`` ids. Scoped generation's cascade needs to mark
    dependents of every type — module, SCC, layer and the repo-wide overview /
    architecture / onboarding pages — stale when the run regenerated a file but
    not its summaries. Same ``fresh``-only downgrade so a tombstoned or
    already-stale page keeps its stronger status.

    Returns the number of pages marked.
    """
    ids = list(dict.fromkeys(page_ids))  # dedupe, preserve order
    if not ids:
        return 0
    from sqlalchemy import update

    from repowise.core.persistence.models import Page

    # Chunk the IN list: SQLite (the local CLI store) binds one variable per id
    # and caps at SQLITE_MAX_VARIABLE_NUMBER, so a large cascade could exceed it.
    marked = 0
    for i in range(0, len(ids), _STALE_ID_CHUNK):
        batch = ids[i : i + _STALE_ID_CHUNK]
        res = await session.execute(
            update(Page)
            .where(
                Page.repository_id == repo_id,
                Page.id.in_(batch),
                Page.freshness_status == "fresh",
            )
            .values(freshness_status="stale")
        )
        marked += int(res.rowcount or 0)
    if marked:
        logger.info("pages_decayed_stale", repo_id=repo_id, count=marked)
    return marked
