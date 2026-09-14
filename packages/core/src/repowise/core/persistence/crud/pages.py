"""CRUD operations for the pages domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Page,
    PageVersion,
    _new_uuid,
    _now_utc,
)
from ._shared import _parse_dt

# ---------------------------------------------------------------------------
# Page CRUD (with versioning)
# ---------------------------------------------------------------------------


def _would_bury_prose(existing: Page | None, metadata: dict | None) -> bool:
    """True when writing this page would replace real prose with a failure stub.

    The generator substitutes a structural stub for a model page whose provider
    call failed (issue #1089) so the page has a row to be found by. That is the
    right answer when nothing was there. It is the wrong one on top of a page a
    model already wrote: one 529 during ``update`` would otherwise cost the user
    the page and file the good version behind a snapshot nobody knows to look
    for.

    Narrow on purpose. A stub landing on a row that is *itself* a stub is not
    prose loss, so it falls through to the normal idempotent path and keeps
    refreshing the fields that say where the page sits.

    Imported lazily to keep persistence independent of generation at module
    load, the same reason :func:`load_prior_pages` defers ``PriorPage``.
    """
    if existing is None or not metadata:
        return False
    from repowise.core.generation.models import STUB_FALLBACK_ERROR
    from repowise.core.providers.llm.template import TEMPLATE_PROVIDER_NAME

    return STUB_FALLBACK_ERROR in metadata and existing.provider_name != TEMPLATE_PROVIDER_NAME


def _apply_page_upsert(
    session: AsyncSession,
    existing: Page | None,
    *,
    keep_existing_prose: bool = False,
    page_id: str,
    repository_id: str,
    page_type: str,
    title: str,
    content: str,
    summary: str,
    target_path: str,
    source_hash: str,
    model_name: str,
    provider_name: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
    generation_level: int,
    confidence: float,
    freshness_status: str,
    meta_json: str,
    parent_page_id: str | None,
    display_order: int,
    section_number: str | None,
    structural_key: str | None,
    created_at: datetime,
    updated_at: datetime,
    now: datetime,
) -> Page:
    """Apply the insert / version-snapshot / idempotent-touch branch for one
    page against a PRE-RESOLVED ``existing`` row.

    Extracted verbatim from :func:`upsert_page` so the single-page and batch
    callers share one implementation of the version semantics. Does NOT flush:
    the caller owns the flush (one per call for ``upsert_page``, one per batch
    for :func:`upsert_pages_from_generated`).
    """
    if existing is not None:
        # The page keeps the prose it already has (see
        # :func:`_would_bury_prose`), but it still moved: placement is decided
        # after generation, by a pass that mutates the page objects, and this
        # write is where it lands. Refreshing those fields and nothing else
        # keeps the tree correct without touching what the page says. The
        # failure marker deliberately does not go to ``metadata_json`` — this
        # row has prose, so recording it as a stub would be a lie the stub
        # counters and ``generate`` would both read.
        if keep_existing_prose:
            existing.title = title
            existing.target_path = target_path
            existing.parent_page_id = parent_page_id
            existing.display_order = display_order
            existing.section_number = section_number
            existing.structural_key = structural_key
            return existing
        # Idempotent no-op: content, prompt hash and model all unchanged, so
        # do not bump the version or spawn a PageVersion snapshot; only refresh
        # the cheap derived fields (metadata enrichment lands here).
        #
        # "Cheap derived" means anything describing where a page sits rather
        # than what it says. Those fields come from the repo's structure, not
        # from the page's own bytes, so they can legitimately change while the
        # content hash does not: a renamed page, or one whose siblings moved.
        # A field left out here is frozen at whatever the first run wrote.
        #
        # ``confidence`` belongs here for the same reason ``freshness_status``
        # does: it is decided by *how* the page was written, not by what the
        # page says, so it can move while the bytes do not. A stub renders
        # identically whether the run never asked for prose or asked and lost
        # the call, and its ``source_hash`` is a hash of that render, so both
        # transitions land in this branch with every compared field equal:
        #
        #   * a keyless page written at 0.3 by a version that stamped both
        #     stub kinds alike stays 0.3 forever, on every subsequent run,
        #     because nothing else would ever write the corrected value;
        #   * a provider outage over an existing keyless stub records the
        #     failure in ``metadata_json`` (right below) while leaving the
        #     confidence saying nothing went wrong.
        #
        # The second is the one that matters: the marker and the number would
        # disagree on the same row, and they are meant to be two halves of one
        # statement.
        if (
            existing.content == content
            and existing.source_hash == source_hash
            and existing.model_name == model_name
        ):
            existing.title = title
            existing.summary = summary
            existing.target_path = target_path
            existing.freshness_status = freshness_status
            existing.confidence = confidence
            existing.metadata_json = meta_json
            existing.parent_page_id = parent_page_id
            existing.display_order = display_order
            existing.section_number = section_number
            existing.structural_key = structural_key
            return existing

        # Archive the current state before overwriting
        snapshot = PageVersion(
            id=_new_uuid(),
            page_id=existing.id,
            repository_id=existing.repository_id,
            version=existing.version,
            page_type=existing.page_type,
            title=existing.title,
            content=existing.content,
            source_hash=existing.source_hash,
            model_name=existing.model_name,
            provider_name=existing.provider_name,
            input_tokens=existing.input_tokens,
            output_tokens=existing.output_tokens,
            confidence=existing.confidence,
            archived_at=now,
        )
        session.add(snapshot)

        # Update Page in place (preserves created_at)
        existing.page_type = page_type
        existing.title = title
        existing.content = content
        existing.summary = summary
        existing.target_path = target_path
        existing.source_hash = source_hash
        existing.model_name = model_name
        existing.provider_name = provider_name
        existing.input_tokens = input_tokens
        existing.output_tokens = output_tokens
        existing.cached_tokens = cached_tokens
        existing.generation_level = generation_level
        existing.version = existing.version + 1
        existing.confidence = confidence
        existing.freshness_status = freshness_status
        existing.metadata_json = meta_json
        existing.parent_page_id = parent_page_id
        existing.display_order = display_order
        existing.section_number = section_number
        existing.structural_key = structural_key
        existing.updated_at = updated_at
        return existing

    page = Page(
        id=page_id,
        repository_id=repository_id,
        page_type=page_type,
        title=title,
        content=content,
        summary=summary,
        target_path=target_path,
        source_hash=source_hash,
        model_name=model_name,
        provider_name=provider_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        generation_level=generation_level,
        version=1,
        confidence=confidence,
        freshness_status=freshness_status,
        metadata_json=meta_json,
        parent_page_id=parent_page_id,
        display_order=display_order,
        section_number=section_number,
        structural_key=structural_key,
        created_at=created_at,
        updated_at=updated_at,
    )
    session.add(page)
    return page


async def upsert_page(
    session: AsyncSession,
    *,
    page_id: str,
    repository_id: str,
    page_type: str,
    title: str,
    content: str,
    summary: str = "",
    target_path: str,
    source_hash: str,
    model_name: str,
    provider_name: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    generation_level: int = 0,
    confidence: float = 1.0,
    freshness_status: str = "fresh",
    metadata: dict | None = None,
    parent_page_id: str | None = None,
    display_order: int = 0,
    section_number: str | None = None,
    structural_key: str | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> Page:
    """Insert or update a wiki page, creating a PageVersion snapshot on update.

    First call  → inserts Page at version=1.
    Subsequent  → archives the current Page as a PageVersion, then updates the
                  Page in-place (version += 1, created_at preserved).
    """
    now = _now_utc()
    meta_json = json.dumps(metadata or {})

    existing_result = await session.execute(select(Page).where(Page.id == page_id))
    existing = existing_result.scalar_one_or_none()

    page = _apply_page_upsert(
        session,
        existing,
        keep_existing_prose=_would_bury_prose(existing, metadata),
        page_id=page_id,
        repository_id=repository_id,
        page_type=page_type,
        title=title,
        content=content,
        summary=summary,
        target_path=target_path,
        source_hash=source_hash,
        model_name=model_name,
        provider_name=provider_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        generation_level=generation_level,
        confidence=confidence,
        freshness_status=freshness_status,
        meta_json=meta_json,
        parent_page_id=parent_page_id,
        display_order=display_order,
        section_number=section_number,
        structural_key=structural_key,
        created_at=created_at or now,
        updated_at=updated_at or now,
        now=now,
    )
    await session.flush()
    return page


async def load_prior_pages(
    session: AsyncSession,
    repository_id: str,
) -> dict[str, Any]:
    """Return a ``page_id → PriorPage`` map for cross-run cache reuse.

    Loads every existing wiki page for the repository so the generator can
    short-circuit the LLM call when the freshly rendered prompt produces a
    matching ``source_hash`` under the same model. Returns an empty dict if
    nothing has been generated yet.
    """
    # Import lazily — keeps persistence independent of generation models at
    # module-load time.
    from repowise.core.generation.page_generator import PriorPage

    result = await session.execute(select(Page).where(Page.repository_id == repository_id))
    prior: dict[str, Any] = {}
    for row in result.scalars():
        prior[row.id] = PriorPage(
            source_hash=row.source_hash,
            model_name=row.model_name,
            content=row.content,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
            cached_tokens=row.cached_tokens,
        )
    return prior


async def upsert_page_from_generated(
    session: AsyncSession,
    generated_page: object,  # repowise.core.generation.models.GeneratedPage
    repository_id: str,
) -> Page:
    """Convenience wrapper that unpacks a GeneratedPage dataclass.

    This keeps the CRUD layer independent of the generation models at the
    import level while still providing a clean API for callers that have a
    GeneratedPage in hand.
    """
    gp = generated_page  # type alias for brevity
    return await upsert_page(
        session,
        page_id=gp.page_id,  # type: ignore[attr-defined]
        repository_id=repository_id,
        page_type=gp.page_type,  # type: ignore[attr-defined]
        title=gp.title,  # type: ignore[attr-defined]
        content=gp.content,  # type: ignore[attr-defined]
        summary=getattr(gp, "summary", "") or "",
        target_path=gp.target_path,  # type: ignore[attr-defined]
        source_hash=gp.source_hash,  # type: ignore[attr-defined]
        model_name=gp.model_name,  # type: ignore[attr-defined]
        provider_name=gp.provider_name,  # type: ignore[attr-defined]
        input_tokens=gp.input_tokens,  # type: ignore[attr-defined]
        output_tokens=gp.output_tokens,  # type: ignore[attr-defined]
        cached_tokens=gp.cached_tokens,  # type: ignore[attr-defined]
        generation_level=gp.generation_level,  # type: ignore[attr-defined]
        confidence=gp.confidence,  # type: ignore[attr-defined]
        freshness_status=gp.freshness_status,  # type: ignore[attr-defined]
        metadata=gp.metadata,  # type: ignore[attr-defined]
        parent_page_id=getattr(gp, "parent_page_id", None),
        display_order=getattr(gp, "display_order", 0),
        section_number=getattr(gp, "section_number", None),
        structural_key=getattr(gp, "structural_key", None),
        created_at=_parse_dt(gp.created_at),  # type: ignore[attr-defined]
        updated_at=_parse_dt(gp.updated_at),  # type: ignore[attr-defined]
    )


# Chunk the id SELECT to stay under SQLite's host-parameter limit (same reason
# as ``persist._PRUNE_CHUNK``).
_PAGE_SELECT_CHUNK = 500


async def upsert_pages_from_generated(
    session: AsyncSession,
    generated_pages: list,  # list[GeneratedPage]
    repository_id: str,
) -> list[Page]:
    """Batch equivalent of looping :func:`upsert_page_from_generated`.

    The end-of-run generation persist re-upserts every page. The per-page
    durability sink already wrote them once during generation; this pass
    exists to flush the post-generation metadata enrichment (related-pages /
    interlinking mutate ``page.metadata`` in place after the sink ran), which
    lands through the idempotent-touch branch. Doing that one page at a time
    is an N+1: a SELECT + flush each, i.e. one round-trip per page on a remote
    DB. This resolves every existing row in one (chunked) SELECT and flushes
    once, preserving :func:`upsert_page`'s exact semantics via the shared
    :func:`_apply_page_upsert` (version snapshot on content change, no-op touch
    on metadata-only change, insert on new).

    NOT a drop-in for the per-page durability sinks: this flushes once at the
    end, so an interrupt mid-batch persists nothing. The ``on_page_ready``
    streaming sinks must keep calling :func:`upsert_page_from_generated`.

    Assumes ``generated_pages`` carries no duplicate ``page_id`` (true by
    construction: ids are deterministic, one page per target).
    """
    pages = list(generated_pages)
    if not pages:
        return []

    # Resolve all existing rows up front. page_id (== Page.id) is unique per
    # page within the run and each row is resolved independently, so one SELECT
    # is equivalent to the per-page loop's fresh SELECT-per-page. No repo filter
    # here, matching ``upsert_page``'s ``WHERE Page.id == page_id``.
    ids = [gp.page_id for gp in pages]
    existing_by_id: dict[str, Page] = {}
    for start in range(0, len(ids), _PAGE_SELECT_CHUNK):
        chunk = ids[start : start + _PAGE_SELECT_CHUNK]
        rows = (await session.execute(select(Page).where(Page.id.in_(chunk)))).scalars().all()
        for row in rows:
            existing_by_id[row.id] = row

    now = _now_utc()
    out: list[Page] = []
    for gp in pages:
        out.append(
            _apply_page_upsert(
                session,
                existing_by_id.get(gp.page_id),
                keep_existing_prose=_would_bury_prose(
                    existing_by_id.get(gp.page_id), gp.metadata
                ),
                page_id=gp.page_id,
                repository_id=repository_id,
                page_type=gp.page_type,
                title=gp.title,
                content=gp.content,
                summary=getattr(gp, "summary", "") or "",
                target_path=gp.target_path,
                source_hash=gp.source_hash,
                model_name=gp.model_name,
                provider_name=gp.provider_name,
                input_tokens=gp.input_tokens,
                output_tokens=gp.output_tokens,
                cached_tokens=gp.cached_tokens,
                generation_level=gp.generation_level,
                confidence=gp.confidence,
                freshness_status=gp.freshness_status,
                meta_json=json.dumps(gp.metadata or {}),
                parent_page_id=getattr(gp, "parent_page_id", None),
                display_order=getattr(gp, "display_order", 0),
                section_number=getattr(gp, "section_number", None),
                structural_key=getattr(gp, "structural_key", None),
                created_at=_parse_dt(gp.created_at) or now,
                updated_at=_parse_dt(gp.updated_at) or now,
                now=now,
            )
        )
    await session.flush()
    return out


async def backfill_related_pages(
    session: AsyncSession,
    repository_id: str,
    *,
    import_edges: list[tuple[str, str]] | None = None,
    git_meta_map: dict[str, dict] | None = None,
    pagerank: dict[str, float] | None = None,
    skip_page_ids: set[str] | None = None,
) -> int:
    """Recompute ``metadata['related_pages']`` across every persisted page.

    LLM-free, so every update flavor (docs, index-only, workspace) can heal
    pages generated before related-pages shipped — or drifted by new
    imports — without a regeneration run.

    Selection module groups exist only during full generation, so this
    recompute covers the other three reasons and *preserves* any existing
    same-module entries instead of stripping them. ``skip_page_ids`` exempts
    pages the current run already attached (their metadata is fresher than
    anything this recompute could produce).

    Returns the number of rows whose metadata changed.
    """
    # Import lazily — keeps persistence independent of generation models at
    # module-load time (same pattern as load_prior_pages above).
    from types import SimpleNamespace

    from repowise.core.generation.related_pages import attach_related_pages

    # Five columns, not whole ORM rows. The recompute reads nothing but the
    # id, type, title, target path and metadata, while ``select(Page)`` also
    # drags every page's rendered ``content`` across the wire — megabytes on a
    # repo of any size, for a metadata field. The whole live page set is still
    # read: ``attach_related_pages`` resolves neighbours against the page set it
    # is handed, so narrowing the *rows* (to the affected pages and one hop)
    # rather than the *columns* would silently resolve fewer neighbours and
    # need a periodic full pass to heal itself. Narrowing columns costs nothing
    # in accuracy.
    result = await session.execute(
        select(
            Page.id,
            Page.page_type,
            Page.title,
            Page.target_path,
            Page.metadata_json,
        ).where(
            Page.repository_id == repository_id,
            Page.freshness_status != "tombstone",
        )
    )
    skip = skip_page_ids or set()
    rows = [r for r in result.all() if r.id not in skip]
    if not rows:
        return 0

    shims = []
    prior_related: list[Any] = []
    for row in rows:
        try:
            meta = json.loads(row.metadata_json or "{}")
        except ValueError:
            meta = {}
        prior_related.append(meta.get("related_pages"))
        shims.append(
            SimpleNamespace(
                page_id=row.id,
                page_type=row.page_type,
                title=row.title,
                target_path=row.target_path,
                metadata=meta,
            )
        )

    attach_related_pages(
        shims,  # type: ignore[arg-type]  # duck-typed GeneratedPage view
        import_edges=import_edges,
        git_meta_map=git_meta_map,
        pagerank=pagerank,
    )

    updates: dict[str, str] = {}
    for row, shim, before in zip(rows, shims, prior_related, strict=True):
        after = shim.metadata.get("related_pages")
        if after is None:
            continue
        # Preserve prior same-module entries — recomputing without module
        # groups must not strip what a full generation attached.
        if before:
            seen_targets = {r.get("target_page_id") for r in after}
            after.extend(
                entry
                for entry in before
                if entry.get("reason") == "same-module"
                and entry.get("target_page_id") not in seen_targets
            )
        if after == before:
            continue
        updates[row.id] = json.dumps(shim.metadata)

    # ORM rows are hydrated only for the pages whose metadata actually moved,
    # which on a steady-state update is usually none of them.
    changed = 0
    ids = list(updates)
    for i in range(0, len(ids), _PAGE_SELECT_CHUNK):
        batch = ids[i : i + _PAGE_SELECT_CHUNK]
        page_rows = (
            (
                await session.execute(
                    select(Page).where(
                        Page.repository_id == repository_id, Page.id.in_(batch)
                    )
                )
            )
            .scalars()
            .all()
        )
        for page in page_rows:
            page.metadata_json = updates[page.id]
            changed += 1
    if changed:
        await session.flush()
    return changed


async def get_page(session: AsyncSession, page_id: str) -> Page | None:
    """Return a Page by its page_id, or None."""
    return await session.get(Page, page_id)


async def list_pages(
    session: AsyncSession,
    repository_id: str,
    *,
    page_type: str | None = None,
    has_prose: bool | None = None,
    include_tombstones: bool = True,
    limit: int = 100,
    offset: int = 0,
    sort_by: str = "updated_at",
    order: str = "desc",
) -> list[Page]:
    """Return pages for a repository, optionally filtered by page_type.

    ``has_prose`` filters on whether a model has written a page's prose, which is
    only a meaningful question for the model-written page types (the concept tree
    and onboarding); every other type renders from structure and never has prose.
    So the filter is scoped to those types: ``has_prose=True`` returns the ones a
    model has written, ``has_prose=False`` the ones still stubs, and ``None``
    (default) returns every page of every type. A stub is a model-written type
    still stamped ``provider_name='template'``.

    ``include_tombstones`` defaults to True to preserve the raw row set for
    callers that reconcile against deleted files. Pass ``False`` for a
    reader-facing count or listing, where a tombstoned page (its file is gone)
    should not be shown or counted.
    """
    q = select(Page).where(Page.repository_id == repository_id)
    if page_type is not None:
        q = q.where(Page.page_type == page_type)
    if not include_tombstones:
        q = q.where(Page.freshness_status != "tombstone")
    if has_prose is not None:
        from repowise.core.generation.models import MODEL_WRITTEN_PAGE_TYPES

        q = q.where(Page.page_type.in_(sorted(MODEL_WRITTEN_PAGE_TYPES)))
        if has_prose:
            q = q.where(Page.provider_name != "template")
        else:
            q = q.where(Page.provider_name == "template")
    _sort_cols = {
        "updated_at": Page.updated_at,
        "confidence": Page.confidence,
        "created_at": Page.created_at,
    }
    sort_col = _sort_cols.get(sort_by, Page.updated_at)
    q = q.order_by(sort_col.asc() if order == "asc" else sort_col.desc())
    q = q.limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_page_versions(
    session: AsyncSession,
    page_id: str,
    *,
    limit: int = 50,
) -> list[PageVersion]:
    """Return historical versions of a page, newest first."""
    result = await session.execute(
        select(PageVersion)
        .where(PageVersion.page_id == page_id)
        .order_by(PageVersion.version.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_stale_pages(
    session: AsyncSession,
    repository_id: str,
) -> list[Page]:
    """Return pages with freshness_status in ('stale', 'expired')."""
    result = await session.execute(
        select(Page).where(
            Page.repository_id == repository_id,
            Page.freshness_status.in_(["stale", "expired"]),
        )
    )
    return list(result.scalars().all())


#: Page types a scoped ``update`` can re-render for one file. Every other
#: structural type (cycle, layer, contract, infra) describes the whole
#: repository and is only written by a full run, so a stale row of those types
#: is not something an update can clear and must not make it think it can.
_FILE_SCOPED_PAGE_TYPES = frozenset({"file_page", "symbol_spotlight"})


async def get_stale_structural_file_paths(
    session: AsyncSession,
    repository_id: str,
) -> list[str]:
    """File paths whose file-scoped pages are marked ``stale`` or ``expired``.

    Covers ``file_page`` rows (``target_path`` is the file) and
    ``symbol_spotlight`` rows (``target_path`` is ``<file>::<symbol>``), which
    are the two page kinds a scoped ``update`` re-renders for a file. The
    caller feeds these paths into the same regeneration list the renderer
    staleness path uses, so an already-stale page is reconciled even when HEAD
    has not moved.
    """
    result = await session.execute(
        select(Page.target_path).where(
            Page.repository_id == repository_id,
            Page.page_type.in_(sorted(_FILE_SCOPED_PAGE_TYPES)),
            Page.freshness_status.in_(["stale", "expired"]),
        )
    )
    stale_paths: list[str] = []
    for (target_path,) in result:
        file_path = (target_path or "").split("::", 1)[0]
        if file_path:
            stale_paths.append(file_path)
    return list(dict.fromkeys(stale_paths))


def load_stale_structural_file_paths(repo_path: Any) -> list[str]:
    """Sync entry point for :func:`_load_stale_structural_file_paths_async`.

    Called from synchronous CLI code and from ``check_repo_staleness``, which
    the async workspace update calls from inside a running loop. ``asyncio.run``
    refuses to nest, so that one caller gets its own loop on a worker thread.
    """
    import asyncio
    import concurrent.futures
    from pathlib import Path

    path_obj = Path(repo_path)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                lambda: asyncio.run(_load_stale_structural_file_paths_async(path_obj))
            ).result()
    return asyncio.run(_load_stale_structural_file_paths_async(path_obj))


async def _load_stale_structural_file_paths_async(repo_path: Any) -> list[str]:
    """Load the stale file-scoped page paths for *repo_path* from its store.

    Returns ``[]`` when no store is reachable: no configured database URL and
    no local ``wiki.db``. A store that is reachable but fails to answer raises,
    because reading that as "nothing is stale" would silently retire the
    reconciliation this exists for.
    """
    from pathlib import Path

    import structlog

    from ..database import (
        create_engine,
        create_session_factory,
        get_configured_db_url,
        get_repo_db_path,
        get_session,
        resolve_db_url,
    )
    from .repository import get_repository_by_path

    logger = structlog.get_logger(__name__)
    path_obj = Path(repo_path)

    if get_configured_db_url() is None and not get_repo_db_path(path_obj).exists():
        return []

    url = resolve_db_url(path_obj)
    engine = create_engine(url)
    try:
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(path_obj))
            if repo is None:
                return []
            return await get_stale_structural_file_paths(session, repo.id)
    except Exception as exc:
        logger.warning("load_stale_structural_file_paths_failed", error=str(exc))
        raise
    finally:
        await engine.dispose()


async def get_stale_file_page_ages(
    session: AsyncSession,
    repository_id: str,
) -> dict[str, float]:
    """``{file_path: staleness_age_seconds}`` for stale/expired file pages.

    The cascade-budget ordering in
    :meth:`~repowise.core.ingestion.change_detector.ChangeDetector.get_affected_pages`
    consumes this so a constrained regeneration run bubbles the *oldest* stale
    pages to the top rather than reordering purely by importance (issues #847 /
    #851). Staleness age is measured from ``updated_at`` — the last time the
    page was regenerated — so the page whose prose lagged the code longest
    carries the largest value.

    Only ``file_page`` rows are returned: the cascade reaches file paths, and
    the module / SCC / repo-wide containers are derived from the selected files
    rather than selected themselves. Returns an empty dict when nothing is
    stale (or the repository has no file pages), which keeps the pure-importance
    ordering.
    """
    result = await session.execute(
        select(Page.id, Page.target_path, Page.updated_at).where(
            Page.repository_id == repository_id,
            Page.page_type == "file_page",
            Page.freshness_status.in_(["stale", "expired"]),
        )
    )
    now = datetime.now(UTC)
    ages: dict[str, float] = {}
    for _pid, target_path, updated_at in result:
        if not target_path:
            continue
        if updated_at is None:
            ages[target_path] = float("inf")
            continue
        dt = updated_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        ages[target_path] = max(0.0, (now - dt).total_seconds())
    return ages
