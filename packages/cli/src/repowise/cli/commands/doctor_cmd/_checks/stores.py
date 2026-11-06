"""Checks 8-10: SQL vs vector store vs FTS consistency, and coordinator drift."""

from __future__ import annotations

import contextlib
from pathlib import Path as _DoctorPath
from typing import NamedTuple

from repowise.cli.helpers import run_async
from repowise.core.exclusion import build_exclude_spec, is_excluded

from .._types import DoctorCheck, _check
from .context import CheckContext, StoreDrift, open_store

_REPAIR = "repowise doctor --repair"


async def _decision_vector_ids(session, repository_id: str) -> set[str]:
    """Vector-store ids for this repo's decision records.

    Decisions are co-located in the *page* vector store under the
    ``decision:<record_id>`` namespace (no separate table). The store therefore
    legitimately holds page vectors *and* decision vectors, so any SQL↔vector
    reconciliation must count decisions on the SQL side — otherwise every
    decision embedding reads as an orphan.
    """
    from sqlalchemy import text as _sql_text

    from repowise.core.analysis.decisions.semantic_match import DECISION_VECTOR_PREFIX

    rows = await session.execute(
        _sql_text("SELECT id FROM decision_records WHERE repository_id = :rid"),
        {"rid": repository_id},
    )
    return {f"{DECISION_VECTOR_PREFIX}{r[0]}" for r in rows}


def _is_stub_fallback_row(page) -> bool:
    """True when a ``Page`` row is a stub standing in for a failed model page.

    The generation-side predicate reads ``page.metadata``; the ORM row carries
    the same dict as the ``metadata_json`` text column, so the marker key is
    shared but the accessor is not.
    """
    import json

    from repowise.core.generation.models import STUB_FALLBACK_ERROR

    try:
        meta = json.loads(page.metadata_json or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(meta, dict) and STUB_FALLBACK_ERROR in meta


async def _all_pages_for_reconciliation(session: object, repo_id: str) -> list:
    """Every page this repository has, for reconciling against the indexes.

    The reconciliation asks which store entries have no page behind them, so
    anything it cannot see it calls an orphan. It read the pages through
    ``list_pages(limit=10000)`` — a paginated listing helper whose ``limit``
    defaults to 100 — and treated that page-one result as the whole database.
    Every page past the 10000th was therefore invisible, and each one turned
    its vector and FTS row into a phantom orphan: on a 18900-page repository,
    8900 of them. ``--repair`` deletes what this reports, so what it removed
    was the live index.

    A few columns rather than whole rows: the id to match against the stores,
    the content and digest for the information floor, metadata_json for the stub
    predicate, target_path to tell whether the file is excluded, and
    freshness_status to leave tombstones out. Nothing downstream reads any other field, and hydrating full
    ORM objects for every page only to discard them is what made a cap look
    necessary in the first place.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import Page

    result = await session.execute(  # type: ignore[attr-defined]
        select(
            Page.id,
            Page.content,
            Page.digest,
            Page.metadata_json,
            Page.target_path,
            Page.freshness_status,
        ).where(Page.repository_id == repo_id)
    )
    return list(result.all())


class _SqlSide(NamedTuple):
    """The SQL ids each store is compared against."""

    sql_ids: set[str]
    vector_sql_ids: set[str]
    indexable_ids: set[str]
    vector_indexable_ids: set[str]
    stub_count: int


async def _sql_side(session, repo_path: _DoctorPath, repo_id: str) -> _SqlSide:
    from repowise.core.persistence.information_floor import meets_information_floor

    pages = await _all_pages_for_reconciliation(session, repo_id)
    # A tombstone (its file is gone) is taken out of both indexes by update,
    # so it is never missing, and a store row still holding one is an orphan
    # `--repair` deletes. Counting it as live sent `--repair` to re-index and
    # re-embed pages for deleted files.
    pages = [p for p in pages if p.freshness_status != "tombstone"]
    # ``Page``'s primary key is the column ``id``; there is no ``page_id``
    # attribute. The old spelling raised on every run, was swallowed, and left
    # both reconciliations reporting "Could not check".
    sql_ids = {p.id for p in pages}

    # The page vector store also holds decision embeddings under the
    # "decision:<id>" namespace, so they belong on the SQL side of the ORPHAN
    # check (but NOT FTS, which only indexes pages). They are deliberately
    # excluded from the MISSING check: a decision can legitimately have no
    # vector (empty match text, swallowed embed failure) and repair cannot
    # re-embed it, so flagging it would be permanent noise.
    vector_sql_ids = sql_ids | await _decision_vector_ids(session, repo_id)

    # MISSING side only, both exclusions below:
    # - A page below the information floor is held out of both indexes on
    #   purpose, and `--repair` applies the same rule and declines, so
    #   reporting it would be permanent noise. A stored vector for a page now
    #   below the floor stays on the ORPHAN side: deleting it is a real repair.
    # - A page whose FILE the user excluded is absent because they asked for
    #   that; `--repair`'s only remedy would index excluded content. Every other
    #   read path filters here too (`core/exclusion.py` documents why: rows
    #   outlive an `exclude_patterns` edit by design). Matched on the FILE: a
    #   symbol page's target_path is `path::Name`, which no file pattern
    #   matches. Deleting store entries for newly-excluded files would make
    #   `--repair` delete on a rules edit, too sharp an edge to add here.
    exclude_spec = build_exclude_spec(repo_path)
    indexable_ids = {
        p.id
        for p in pages
        if meets_information_floor(p.content or "", digest=p.digest or "")
        and not is_excluded((p.target_path or "").split("::", 1)[0], exclude_spec)
    }
    # A stub standing in for a failed model page is held out of the vector
    # store on purpose: ``_seed_resume`` reads the store back as the ledger of
    # pages already written, so a vector here would tell the next
    # ``init --resume`` there is nothing left to write, and ``--repair`` would
    # embed the stub and quietly burn the retry. FTS still indexes the stub, so
    # this is the vector side only, and only MISSING.
    stub_ids = {p.id for p in pages if _is_stub_fallback_row(p)}
    return _SqlSide(
        sql_ids, vector_sql_ids, indexable_ids, indexable_ids - stub_ids, len(stub_ids)
    )


async def _vector_ids(lance_dir: _DoctorPath) -> tuple[set[str], str | None]:
    """``(ids, error)``; a store that cannot be opened is named, not read as empty."""
    if not lance_dir.exists():
        return set(), None
    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    try:
        vs = LanceDBVectorStore(str(lance_dir), embedder=MockEmbedder())
        ids = await vs.list_page_ids()
        await vs.close()
        return ids, None
    except Exception as exc:
        from repowise.core.persistence.vector_store.lancedb_store import store_open_fix_hint

        return set(), f"{type(exc).__name__}: {exc}; to fix: {store_open_fix_hint(exc)}"


async def _fts_ids(engine) -> set[str]:
    from repowise.core.persistence import FullTextSearch

    try:
        return await FullTextSearch(engine).list_indexed_ids()
    except Exception:
        return set()


async def _reconcile(
    repo_path: _DoctorPath, repowise_dir: _DoctorPath
) -> tuple[StoreDrift, int, str | None]:
    """``(drift, stub_count, vector_store_error)`` across SQL, vectors and FTS."""
    from repowise.core.persistence import get_repository_by_path, get_session

    engine, sf = await open_store(repo_path)
    async with get_session(sf) as session:
        repo = await get_repository_by_path(session, str(repo_path))
        if not repo:
            await engine.dispose()
            return StoreDrift(), 0, None
        sql = await _sql_side(session, repo_path, repo.id)

    lance_dir = repowise_dir / "lancedb"
    vs_ids, vs_error = await _vector_ids(lance_dir)
    fts_ids = await _fts_ids(engine)
    await engine.dispose()
    return (
        StoreDrift(
            # An index on disk that holds none of the indexable pages is every
            # one of them missing. Only no index at all (fast mode, nothing
            # embedded yet) has nothing to compare.
            missing_from_vector=(
                sql.vector_indexable_ids - vs_ids if lance_dir.exists() else set()
            ),
            orphaned_vector=vs_ids - sql.vector_sql_ids,
            missing_from_fts=sql.indexable_ids - fts_ids if fts_ids else set(),
            orphaned_fts=fts_ids - sql.sql_ids if fts_ids else set(),
            vector_store_empty=lance_dir.exists() and vs_error is None and not vs_ids,
        ),
        sql.stub_count,
        vs_error,
    )


def _drift_detail(missing: set[str], orphaned: set[str]) -> tuple[bool, str]:
    ok = not missing and not orphaned
    return ok, "in sync" if ok else f"{len(missing)} missing, {len(orphaned)} orphaned"


def _consistency_rows(ctx: CheckContext) -> list[DoctorCheck]:
    """Sets ``ctx.drift`` for ``--repair``."""
    rows: list[DoctorCheck] = []
    try:
        ctx.drift, stub_count, vector_store_error = run_async(
            _reconcile(ctx.repo_path, ctx.repowise_dir)
        )
        drift = ctx.drift
        if vector_store_error is not None:
            rows.append(
                _check(
                    "Vector store",
                    False,
                    f"cannot open .repowise/lancedb ({vector_store_error}); semantic "
                    "search is off until it opens, then run `repowise reindex`",
                    check_id="vector-store",
                )
            )
        vec_ok, vec_detail = _drift_detail(drift.missing_from_vector, drift.orphaned_vector)
        # Held-back stubs are not drift, but the wiki has a page there no model
        # wrote, so say so rather than letting "in sync" imply it is complete.
        # Name the command: `--resume` exists only on `init`.
        if stub_count:
            vec_detail += f" · {stub_count} stub(s) awaiting `repowise init --resume`"
        # --repair declines to re-embed a whole empty store; reindex does that.
        vec_fix = "repowise reindex" if drift.vector_store_empty else _REPAIR
        rows.append(
            _check(
                "SQL ↔ Vector Store",
                vec_ok,
                vec_detail,
                check_id="vector-store-sync",
                fix=None if vec_ok else vec_fix,
            )
        )
        fts_ok, fts_detail = _drift_detail(drift.missing_from_fts, drift.orphaned_fts)
        rows.append(
            _check(
                "SQL ↔ FTS Index",
                fts_ok,
                fts_detail,
                check_id="fts-index-sync",
                fix=None if fts_ok else _REPAIR,
            )
        )
    except Exception as exc:
        # Name the failure. "Could not check" is a warn, not a fail, so without
        # the exception a reconciliation that raises on every run would read
        # like one that had nothing to reconcile.
        rows.append(
            _check(
                "Store consistency",
                True,
                f"Could not check: {type(exc).__name__}: {exc}",
                check_id="store-consistency",
                warn=True,
            )
        )
    return rows


async def _decision_aware_drift(session, repo_path: _DoctorPath, result: dict, vector_store):
    """Recompute the coordinator's drift with decisions counted on the SQL side.

    The vector store also holds decision embeddings ("decision:<id>"), so
    without this every decision reads as drift. Only decisions that actually
    have a vector count: one can legitimately be unembedded, and counting it
    would bias drift positive (SQL > Vector) forever.
    """
    from repowise.core.persistence import get_repository_by_path

    repo = await get_repository_by_path(session, str(repo_path))
    if repo is None:
        return
    dec_ids = await _decision_vector_ids(session, repo.id)
    decision_count = len(dec_ids)
    if vector_store is not None:
        with contextlib.suppress(Exception):
            store_ids = await vector_store.list_page_ids()
            decision_count = len(dec_ids & store_ids)
    sql_pages = result.get("sql_pages")
    vector_count = result.get("vector_count")
    if sql_pages is not None:
        adjusted_sql = sql_pages + decision_count
        result["sql_pages"] = adjusted_sql
        if vector_count is not None and vector_count != -1:
            result["drift"] = abs(adjusted_sql - vector_count) / max(adjusted_sql, 1)


async def _coordinator_health(repo_path: _DoctorPath, repowise_dir: _DoctorPath) -> dict:
    from repowise.core.persistence import get_session
    from repowise.core.persistence.coordinator import AtomicStorageCoordinator
    from repowise.core.persistence.vector_store import LanceDBVectorStore
    from repowise.core.providers.embedding.base import MockEmbedder

    engine, sf = await open_store(repo_path)
    vector_store = None
    lance_dir = repowise_dir / "lancedb"
    if lance_dir.exists():
        with contextlib.suppress(Exception):
            vector_store = LanceDBVectorStore(str(lance_dir), embedder=MockEmbedder())

    async with get_session(sf) as session:
        coord = AtomicStorageCoordinator(session, graph_builder=None, vector_store=vector_store)
        result = await coord.health_check()
        await _decision_aware_drift(session, repo_path, result, vector_store)

    if vector_store is not None:
        with contextlib.suppress(Exception):
            await vector_store.close()
    await engine.dispose()
    return result


def _drift_color(drift: float | None) -> str:
    if drift is None:
        return "white"
    if drift < 0.05:
        return "green"
    if drift < 0.15:
        return "yellow"
    return "red"


def _coordinator_row(ctx: CheckContext) -> DoctorCheck:
    """Sets ``ctx.coord_drift_color``/``pct``, which the table highlights."""
    try:
        result = run_async(_coordinator_health(ctx.repo_path, ctx.repowise_dir))
        sql_pages = result.get("sql_pages")
        vector_count = result.get("vector_count")
        drift = result.get("drift")

        drift_pct = f"{drift * 100:.1f}%" if drift is not None else "N/A"
        drift_color = _drift_color(drift)
        vec_display = (
            str(vector_count) if vector_count != -1 and vector_count is not None else "unknown"
        )
        detail = f"SQL={sql_pages}, Vector={vec_display}, Drift={drift_pct}"
        row = _check(
            "Coordinator drift",
            drift is None or drift < 0.05,
            detail,
            check_id="coordinator-drift",
        )
        ctx.coord_drift_color = drift_color
        ctx.coord_drift_pct = drift_pct
        return row
    except Exception as exc:
        return _check(
            "Coordinator drift",
            True,
            f"Could not check: {exc}",
            check_id="coordinator-drift",
            warn=True,
        )


def store_checks(ctx: CheckContext) -> list[DoctorCheck]:
    """Checks 8-10, in order. Fills ``ctx.drift`` and the drift highlight."""
    checks: list[DoctorCheck] = []
    if ctx.quick:
        return checks
    if ctx.db_ok and ctx.page_count > 0:
        checks.extend(_consistency_rows(ctx))
    if ctx.db_ok:
        checks.append(_coordinator_row(ctx))
    return checks
