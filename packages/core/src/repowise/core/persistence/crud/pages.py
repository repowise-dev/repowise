"""CRUD operations for the pages domain (repowise persistence layer).

Split out of the former monolithic ``crud.py``; ``crud/__init__.py`` re-exports
every public name, so existing imports are unaffected.
"""

from __future__ import annotations

import json
from datetime import datetime
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


def _apply_page_upsert(
    session: AsyncSession,
    existing: Page | None,
    *,
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
        # Idempotent no-op: content, prompt hash and model all unchanged, so
        # do not bump the version or spawn a PageVersion snapshot; only refresh
        # the cheap derived fields (metadata enrichment lands here).
        if (
            existing.content == content
            and existing.source_hash == source_hash
            and existing.model_name == model_name
        ):
            existing.summary = summary
            existing.target_path = target_path
            existing.freshness_status = freshness_status
            existing.metadata_json = meta_json
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

    result = await session.execute(
        select(Page).where(
            Page.repository_id == repository_id,
            Page.freshness_status != "tombstone",
        )
    )
    rows = [r for r in result.scalars() if r.id not in (skip_page_ids or set())]
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

    changed = 0
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
        row.metadata_json = json.dumps(shim.metadata)
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
    deterministic: bool | None = None,
    limit: int = 100,
    offset: int = 0,
    sort_by: str = "updated_at",
    order: str = "desc",
) -> list[Page]:
    """Return pages for a repository, optionally filtered by page_type.

    ``deterministic=True`` returns only unwritten (template) pages;
    ``deterministic=False`` returns only model-written ones. ``None`` (default)
    returns both. A template page is stamped ``provider_name='template'``.
    """
    q = select(Page).where(Page.repository_id == repository_id)
    if page_type is not None:
        q = q.where(Page.page_type == page_type)
    if deterministic is True:
        q = q.where(Page.provider_name == "template")
    elif deterministic is False:
        q = q.where(Page.provider_name != "template")
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
