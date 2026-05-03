"""Async CRUD operations for the repowise persistence layer.

All functions accept an AsyncSession as the first argument; the caller owns
transaction boundaries.  Functions that complete a logical unit of work call
``await session.flush()`` to write changes to the transaction buffer — the
caller must ``await session.commit()`` (or use the ``get_session`` context
manager from database.py).

Versioning contract for upsert_page:
    First upsert  → inserts Page (version=1).  No PageVersion created.
    Second upsert → archives existing Page as PageVersion, then updates Page
                    in place (version increments).  created_at is preserved.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    ChatMessage,
    Conversation,
    DeadCodeFinding,
    DecisionRecord,
    GenerationJob,
    GitMetadata,
    GraphEdge,
    GraphNode,
    Page,
    PageVersion,
    Repository,
    WebhookEvent,
    WikiSymbol,
    _new_uuid,
    _now_utc,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_VALID_JOB_STATUSES = frozenset({"pending", "running", "completed", "failed", "paused"})

_BATCH_SIZE = 500  # max rows per INSERT to stay under SQLite's parameter limit


def _parse_dt(ts: str) -> datetime:
    """Parse an ISO-8601 UTC string to a timezone-aware datetime."""
    ts = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


# ---------------------------------------------------------------------------
# Repository CRUD
# ---------------------------------------------------------------------------


async def upsert_repository(
    session: AsyncSession,
    *,
    name: str,
    local_path: str,
    url: str = "",
    default_branch: str = "main",
    settings: dict | None = None,
) -> Repository:
    """Create or update a repository record.

    Lookup is by ``local_path`` (the canonical key for local repositories).
    """
    result = await session.execute(select(Repository).where(Repository.local_path == local_path))
    repo = result.scalar_one_or_none()

    if repo is None:
        repo = Repository(
            id=_new_uuid(),
            name=name,
            local_path=local_path,
            url=url,
            default_branch=default_branch,
            settings_json=json.dumps(settings or {}),
        )
        session.add(repo)
    else:
        repo.name = name
        repo.url = url
        repo.default_branch = default_branch
        if settings is not None:
            repo.settings_json = json.dumps(settings)
        repo.updated_at = _now_utc()

    await session.flush()
    return repo


async def get_repository(session: AsyncSession, repo_id: str) -> Repository | None:
    """Return a Repository by primary key, or None."""
    return await session.get(Repository, repo_id)


async def get_repository_by_path(session: AsyncSession, local_path: str) -> Repository | None:
    """Return a Repository by local_path, or None."""
    result = await session.execute(select(Repository).where(Repository.local_path == local_path))
    return result.scalar_one_or_none()


async def delete_repository(session: AsyncSession, repo_id: str) -> bool:
    """Delete a repository and all cascaded children.

    Returns True if deleted, False if not found.

    NOTE: The caller should clean up the FTS index *before* calling this,
    since the CASCADE will delete Page rows and we lose the page IDs.
    """
    repo = await session.get(Repository, repo_id)
    if repo is None:
        return False
    await session.delete(repo)
    await session.flush()
    return True


async def list_page_ids(session: AsyncSession, repository_id: str) -> list[str]:
    """Return all page IDs for a repository (lightweight, ID-only query)."""
    result = await session.execute(
        select(Page.id).where(Page.repository_id == repository_id)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# GenerationJob CRUD
# ---------------------------------------------------------------------------


async def upsert_generation_job(
    session: AsyncSession,
    *,
    repository_id: str,
    status: str = "pending",
    provider_name: str = "",
    model_name: str = "",
    total_pages: int = 0,
    config: dict | None = None,
    job_id: str | None = None,
) -> GenerationJob:
    """Insert a new GenerationJob (jobs are append-only)."""
    job = GenerationJob(
        id=job_id or _new_uuid(),
        repository_id=repository_id,
        status=status,
        provider_name=provider_name,
        model_name=model_name,
        total_pages=total_pages,
        config_json=json.dumps(config or {}),
    )
    session.add(job)
    await session.flush()
    return job


async def get_generation_job(session: AsyncSession, job_id: str) -> GenerationJob | None:
    """Return a GenerationJob by primary key, or None."""
    return await session.get(GenerationJob, job_id)


async def update_job_status(
    session: AsyncSession,
    job_id: str,
    status: str,
    *,
    completed_pages: int | None = None,
    failed_pages: int | None = None,
    current_level: int | None = None,
    total_pages: int | None = None,
    error_message: str | None = None,
) -> GenerationJob:
    """Update the mutable fields of a GenerationJob.

    Raises:
        ValueError: If *status* is not a recognised value.
        LookupError: If *job_id* does not exist.
    """
    if status not in _VALID_JOB_STATUSES:
        raise ValueError(
            f"Unknown job status {status!r}. Valid values: {sorted(_VALID_JOB_STATUSES)}"
        )

    job = await session.get(GenerationJob, job_id)
    if job is None:
        raise LookupError(f"No GenerationJob with id={job_id!r}")

    job.status = status
    job.updated_at = _now_utc()

    if completed_pages is not None:
        job.completed_pages = completed_pages
    if failed_pages is not None:
        job.failed_pages = failed_pages
    if current_level is not None:
        job.current_level = current_level
    if total_pages is not None:
        job.total_pages = total_pages
    if error_message is not None:
        job.error_message = error_message

    if status == "running" and job.started_at is None:
        job.started_at = _now_utc()
    if status in ("completed", "failed"):
        job.finished_at = _now_utc()

    await session.flush()
    return job


# ---------------------------------------------------------------------------
# Page CRUD (with versioning)
# ---------------------------------------------------------------------------


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
    page_created_at = created_at or now
    page_updated_at = updated_at or now
    meta_json = json.dumps(metadata or {})

    existing_result = await session.execute(select(Page).where(Page.id == page_id))
    existing = existing_result.scalar_one_or_none()

    if existing is not None:
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
        existing.updated_at = page_updated_at

        await session.flush()
        return existing
    else:
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
            created_at=page_created_at,
            updated_at=page_updated_at,
        )
        session.add(page)
        await session.flush()
        return page


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


async def get_page(session: AsyncSession, page_id: str) -> Page | None:
    """Return a Page by its page_id, or None."""
    return await session.get(Page, page_id)


async def list_pages(
    session: AsyncSession,
    repository_id: str,
    *,
    page_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
    sort_by: str = "updated_at",
    order: str = "desc",
) -> list[Page]:
    """Return pages for a repository, optionally filtered by page_type."""
    q = select(Page).where(Page.repository_id == repository_id)
    if page_type is not None:
        q = q.where(Page.page_type == page_type)
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


# ---------------------------------------------------------------------------
# Graph CRUD (batch)
# ---------------------------------------------------------------------------


async def batch_upsert_graph_nodes(
    session: AsyncSession,
    repository_id: str,
    nodes: list[dict],
) -> None:
    """Upsert graph nodes for a repository in batches of up to 500.

    Each element of *nodes* is a dict with keys matching GraphNode fields
    (excluding id and repository_id which are set here).

    Uses SELECT-then-INSERT/UPDATE for dialect portability.
    """
    for node_data in nodes:
        node_id = node_data.get("node_id", "")
        result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository_id,
                GraphNode.node_id == node_id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing is not None:
            for key, val in node_data.items():
                if key not in ("id", "repository_id", "created_at") and hasattr(existing, key):
                    setattr(existing, key, val)
        else:
            session.add(
                GraphNode(
                    id=_new_uuid(),
                    repository_id=repository_id,
                    **{k: v for k, v in node_data.items() if k not in ("id", "repository_id")},
                )
            )

    await session.flush()


async def batch_upsert_graph_edges(
    session: AsyncSession,
    repository_id: str,
    edges: list[dict],
) -> None:
    """Upsert graph edges for a repository.

    Each element of *edges* should have ``source_node_id``, ``target_node_id``,
    ``edge_type``, and optionally ``imported_names_json`` and ``confidence``.

    The unique constraint is (repository_id, source, target, edge_type),
    allowing multiple edge types between the same pair of nodes.
    """
    for edge_data in edges:
        source = edge_data.get("source_node_id", "")
        target = edge_data.get("target_node_id", "")
        edge_type = edge_data.get("edge_type", "imports")
        result = await session.execute(
            select(GraphEdge).where(
                GraphEdge.repository_id == repository_id,
                GraphEdge.source_node_id == source,
                GraphEdge.target_node_id == target,
                GraphEdge.edge_type == edge_type,
            )
        )
        existing = result.scalar_one_or_none()

        if existing is not None:
            imported = edge_data.get("imported_names_json")
            if imported is not None:
                existing.imported_names_json = imported
            confidence = edge_data.get("confidence")
            if confidence is not None:
                existing.confidence = confidence
        else:
            session.add(
                GraphEdge(
                    id=_new_uuid(),
                    repository_id=repository_id,
                    source_node_id=source,
                    target_node_id=target,
                    imported_names_json=edge_data.get("imported_names_json", "[]"),
                    edge_type=edge_type,
                    confidence=edge_data.get("confidence", 1.0),
                )
            )

    await session.flush()


# ---------------------------------------------------------------------------
# WikiSymbol CRUD (batch)
# ---------------------------------------------------------------------------


async def batch_upsert_symbols(
    session: AsyncSession,
    repository_id: str,
    symbols: list,  # list[ingestion.models.Symbol]
) -> None:
    """Upsert ingestion Symbol objects into the wiki_symbols table.

    Accepts ingestion.models.Symbol dataclass instances (duck-typed).
    """
    for sym in symbols:
        symbol_id = getattr(sym, "id", None) or f"{sym.file_path}::{sym.name}"
        result = await session.execute(
            select(WikiSymbol).where(
                WikiSymbol.repository_id == repository_id,
                WikiSymbol.symbol_id == symbol_id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing is not None:
            existing.name = sym.name
            existing.qualified_name = getattr(sym, "qualified_name", sym.name)
            existing.kind = sym.kind
            existing.signature = getattr(sym, "signature", "")
            existing.start_line = getattr(sym, "start_line", 0)
            existing.end_line = getattr(sym, "end_line", 0)
            existing.docstring = getattr(sym, "docstring", None)
            existing.visibility = getattr(sym, "visibility", "public")
            existing.is_async = getattr(sym, "is_async", False)
            existing.complexity_estimate = getattr(sym, "complexity_estimate", 0)
            existing.language = getattr(sym, "language", "")
            existing.parent_name = getattr(sym, "parent_name", None)
            existing.updated_at = _now_utc()
        else:
            session.add(
                WikiSymbol(
                    id=_new_uuid(),
                    repository_id=repository_id,
                    file_path=getattr(sym, "file_path", ""),
                    symbol_id=symbol_id,
                    name=sym.name,
                    qualified_name=getattr(sym, "qualified_name", sym.name),
                    kind=sym.kind,
                    signature=getattr(sym, "signature", ""),
                    start_line=getattr(sym, "start_line", 0),
                    end_line=getattr(sym, "end_line", 0),
                    docstring=getattr(sym, "docstring", None),
                    visibility=getattr(sym, "visibility", "public"),
                    is_async=getattr(sym, "is_async", False),
                    complexity_estimate=getattr(sym, "complexity_estimate", 0),
                    language=getattr(sym, "language", ""),
                    parent_name=getattr(sym, "parent_name", None),
                )
            )

    await session.flush()


# ---------------------------------------------------------------------------
# WebhookEvent CRUD
# ---------------------------------------------------------------------------


async def store_webhook_event(
    session: AsyncSession,
    *,
    provider: str,
    event_type: str,
    payload: dict,
    repository_id: str | None = None,
    delivery_id: str = "",
) -> WebhookEvent:
    """Append a new WebhookEvent record."""
    event = WebhookEvent(
        id=_new_uuid(),
        repository_id=repository_id,
        provider=provider,
        event_type=event_type,
        delivery_id=delivery_id,
        payload_json=json.dumps(payload),
        processed=False,
    )
    session.add(event)
    await session.flush()
    return event


async def mark_webhook_processed(
    session: AsyncSession, event_id: str, *, job_id: str | None = None
) -> None:
    """Mark a WebhookEvent as processed and optionally link it to a job."""
    event = await session.get(WebhookEvent, event_id)
    if event is None:
        raise LookupError(f"No WebhookEvent with id={event_id!r}")
    event.processed = True
    if job_id is not None:
        event.job_id = job_id
    await session.flush()


# ---------------------------------------------------------------------------
# GitMetadata CRUD
# ---------------------------------------------------------------------------


async def upsert_git_metadata(
    session: AsyncSession,
    *,
    repository_id: str,
    file_path: str,
    **kwargs: object,
) -> GitMetadata:
    """Create or update a single GitMetadata row."""
    result = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repository_id,
            GitMetadata.file_path == file_path,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        for key, val in kwargs.items():
            if hasattr(existing, key):
                setattr(existing, key, val)
        existing.updated_at = _now_utc()
    else:
        existing = GitMetadata(
            id=_new_uuid(),
            repository_id=repository_id,
            file_path=file_path,
            **{k: v for k, v in kwargs.items() if hasattr(GitMetadata, k)},
        )
        session.add(existing)

    await session.flush()
    return existing


async def get_git_metadata(
    session: AsyncSession, repository_id: str, file_path: str
) -> GitMetadata | None:
    """Return GitMetadata for a specific file, or None."""
    result = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repository_id,
            GitMetadata.file_path == file_path,
        )
    )
    return result.scalar_one_or_none()


async def get_git_metadata_bulk(
    session: AsyncSession, repository_id: str, file_paths: list[str]
) -> dict[str, GitMetadata]:
    """Return a dict of file_path → GitMetadata for the given paths."""
    if not file_paths:
        return {}
    result = await session.execute(
        select(GitMetadata).where(
            GitMetadata.repository_id == repository_id,
            GitMetadata.file_path.in_(file_paths),
        )
    )
    return {gm.file_path: gm for gm in result.scalars().all()}


async def get_all_git_metadata(session: AsyncSession, repository_id: str) -> dict[str, GitMetadata]:
    """Return all GitMetadata rows for a repository."""
    result = await session.execute(
        select(GitMetadata).where(GitMetadata.repository_id == repository_id)
    )
    return {gm.file_path: gm for gm in result.scalars().all()}


async def upsert_git_metadata_bulk(
    session: AsyncSession,
    repository_id: str,
    metadata_list: list[dict],
) -> None:
    """Bulk upsert git metadata rows in batches."""
    for i in range(0, len(metadata_list), _BATCH_SIZE):
        batch = metadata_list[i : i + _BATCH_SIZE]
        for meta in batch:
            file_path = meta.get("file_path", "")
            result = await session.execute(
                select(GitMetadata).where(
                    GitMetadata.repository_id == repository_id,
                    GitMetadata.file_path == file_path,
                )
            )
            existing = result.scalar_one_or_none()

            if existing is not None:
                for key, val in meta.items():
                    if key not in ("id", "repository_id") and hasattr(existing, key):
                        setattr(existing, key, val)
                existing.updated_at = _now_utc()
            else:
                session.add(
                    GitMetadata(
                        id=_new_uuid(),
                        repository_id=repository_id,
                        **{
                            k: v
                            for k, v in meta.items()
                            if k not in ("id", "repository_id") and hasattr(GitMetadata, k)
                        },
                    )
                )
        await session.flush()


async def recompute_git_percentiles(
    session: AsyncSession,
    repository_id: str,
) -> int:
    """Recompute churn_percentile + is_hotspot using a SQL PERCENT_RANK window function.

    Called after incremental updates so that percentile rankings stay fresh
    without a full ``repowise init``.  Returns the number of rows updated.

    Primary ranking signal is temporal_hotspot_score (exponentially decayed churn);
    commit_count_90d is the tiebreak.  Works on both SQLite (3.25+) and PostgreSQL.
    """
    # First check how many rows exist so we can return the count without an
    # extra query after the UPDATE.
    count_result = await session.execute(
        select(GitMetadata).where(GitMetadata.repository_id == repository_id)
    )
    rows = count_result.scalars().all()
    if not rows:
        return 0

    sql = """
WITH ranked AS (
  SELECT id, PERCENT_RANK() OVER (
    PARTITION BY repository_id
    ORDER BY COALESCE(temporal_hotspot_score, 0.0), commit_count_90d
  ) AS prank
  FROM git_metadata
  WHERE repository_id = :repo_id
)
UPDATE git_metadata
SET churn_percentile = (SELECT prank FROM ranked WHERE ranked.id = git_metadata.id),
    is_hotspot = ((SELECT prank FROM ranked WHERE ranked.id = git_metadata.id) >= 0.75
                  AND git_metadata.commit_count_90d > 0)
WHERE repository_id = :repo_id;
"""
    await session.execute(text(sql), {"repo_id": repository_id})
    await session.flush()
    return len(rows)


# ---------------------------------------------------------------------------
# DeadCodeFinding CRUD
# ---------------------------------------------------------------------------


async def save_dead_code_findings(
    session: AsyncSession,
    repository_id: str,
    findings: list[dict],
) -> None:
    """Persist dead code findings, replacing any existing open findings for the repo."""
    # Delete existing open findings for this repo before saving new ones
    existing = await session.execute(
        select(DeadCodeFinding).where(
            DeadCodeFinding.repository_id == repository_id,
            DeadCodeFinding.status == "open",
        )
    )
    for row in existing.scalars().all():
        await session.delete(row)

    for i in range(0, len(findings), _BATCH_SIZE):
        batch = findings[i : i + _BATCH_SIZE]
        for finding in batch:
            # Accept both DeadCodeFindingData-like objects and plain dicts
            if hasattr(finding, "kind"):
                data = {
                    "kind": str(finding.kind.value)
                    if hasattr(finding.kind, "value")
                    else str(finding.kind),
                    "file_path": finding.file_path,
                    "symbol_name": finding.symbol_name,
                    "symbol_kind": finding.symbol_kind,
                    "confidence": finding.confidence,
                    "reason": finding.reason,
                    "last_commit_at": finding.last_commit_at,
                    "commit_count_90d": finding.commit_count_90d,
                    "lines": finding.lines,
                    "package": finding.package,
                    "evidence_json": json.dumps(
                        finding.evidence if hasattr(finding, "evidence") else []
                    ),
                    "safe_to_delete": finding.safe_to_delete,
                    "primary_owner": finding.primary_owner,
                    "age_days": finding.age_days,
                }
            else:
                data = dict(finding)
                if "evidence" in data:
                    data["evidence_json"] = json.dumps(data.pop("evidence"))

            session.add(
                DeadCodeFinding(
                    id=_new_uuid(),
                    repository_id=repository_id,
                    **{
                        k: v
                        for k, v in data.items()
                        if k not in ("id", "repository_id") and hasattr(DeadCodeFinding, k)
                    },
                )
            )
        await session.flush()


async def get_dead_code_findings(
    session: AsyncSession,
    repository_id: str,
    *,
    kind: str | None = None,
    min_confidence: float = 0.0,
    status: str = "open",
) -> list[DeadCodeFinding]:
    """Return dead code findings filtered by kind, confidence, and status."""
    q = select(DeadCodeFinding).where(
        DeadCodeFinding.repository_id == repository_id,
        DeadCodeFinding.status == status,
        DeadCodeFinding.confidence >= min_confidence,
    )
    if kind is not None:
        q = q.where(DeadCodeFinding.kind == kind)
    q = q.order_by(DeadCodeFinding.confidence.desc())
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_dead_code_status(
    session: AsyncSession,
    finding_id: str,
    status: str,
    note: str | None = None,
) -> DeadCodeFinding | None:
    """Update the status (and optional note) of a dead code finding."""
    finding = await session.get(DeadCodeFinding, finding_id)
    if finding is None:
        return None
    finding.status = status
    if note is not None:
        finding.note = note
    await session.flush()
    return finding


async def get_dead_code_summary(session: AsyncSession, repository_id: str) -> dict:
    """Return aggregate dead code statistics."""
    result = await session.execute(
        select(DeadCodeFinding).where(
            DeadCodeFinding.repository_id == repository_id,
            DeadCodeFinding.status == "open",
        )
    )
    findings = list(result.scalars().all())

    summary: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    total_lines = 0
    by_kind: dict[str, int] = {}

    for f in findings:
        if f.confidence >= 0.7:
            summary["high"] += 1
        elif f.confidence >= 0.4:
            summary["medium"] += 1
        else:
            summary["low"] += 1
        total_lines += f.lines
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1

    return {
        "total_findings": len(findings),
        "confidence_summary": summary,
        "deletable_lines": sum(f.lines for f in findings if f.safe_to_delete),
        "total_lines": total_lines,
        "by_kind": by_kind,
    }


# ---------------------------------------------------------------------------
# DecisionRecord CRUD
# ---------------------------------------------------------------------------

_VALID_DECISION_STATUSES = frozenset({"proposed", "active", "deprecated", "superseded"})


async def upsert_decision(
    session: AsyncSession,
    *,
    repository_id: str,
    title: str,
    status: str = "proposed",
    context: str = "",
    decision: str = "",
    rationale: str = "",
    alternatives: list[str] | None = None,
    consequences: list[str] | None = None,
    affected_files: list[str] | None = None,
    affected_modules: list[str] | None = None,
    tags: list[str] | None = None,
    source: str = "cli",
    evidence_commits: list[str] | None = None,
    evidence_file: str | None = None,
    evidence_line: int | None = None,
    confidence: float = 1.0,
    last_code_change: datetime | None = None,
    staleness_score: float = 0.0,
    superseded_by: str | None = None,
    decision_id: str | None = None,
) -> DecisionRecord:
    """Create or update a decision record.

    Dedup key: ``(repository_id, title, source, evidence_file)``.
    """
    # Normalise text fields — LLM extractors may return explicit None
    rationale = rationale or ""
    context = context or ""
    decision = decision or ""

    # Build the WHERE clause — evidence_file may be NULL
    q = select(DecisionRecord).where(
        DecisionRecord.repository_id == repository_id,
        DecisionRecord.title == title,
        DecisionRecord.source == source,
    )
    if evidence_file is not None:
        q = q.where(DecisionRecord.evidence_file == evidence_file)
    else:
        q = q.where(DecisionRecord.evidence_file.is_(None))

    result = await session.execute(q)
    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.status = status
        existing.context = context
        existing.decision = decision
        existing.rationale = rationale
        existing.alternatives_json = json.dumps(alternatives or [])
        existing.consequences_json = json.dumps(consequences or [])
        existing.affected_files_json = json.dumps(affected_files or [])
        existing.affected_modules_json = json.dumps(affected_modules or [])
        existing.tags_json = json.dumps(tags or [])
        existing.evidence_commits_json = json.dumps(evidence_commits or [])
        existing.evidence_line = evidence_line
        existing.confidence = confidence
        existing.last_code_change = last_code_change
        existing.staleness_score = staleness_score
        existing.superseded_by = superseded_by
        existing.updated_at = _now_utc()
        await session.flush()
        return existing

    rec = DecisionRecord(
        id=decision_id or _new_uuid(),
        repository_id=repository_id,
        title=title,
        status=status,
        context=context,
        decision=decision,
        rationale=rationale,
        alternatives_json=json.dumps(alternatives or []),
        consequences_json=json.dumps(consequences or []),
        affected_files_json=json.dumps(affected_files or []),
        affected_modules_json=json.dumps(affected_modules or []),
        tags_json=json.dumps(tags or []),
        evidence_commits_json=json.dumps(evidence_commits or []),
        source=source,
        evidence_file=evidence_file,
        evidence_line=evidence_line,
        confidence=confidence,
        last_code_change=last_code_change,
        staleness_score=staleness_score,
        superseded_by=superseded_by,
    )
    session.add(rec)
    await session.flush()
    return rec


async def get_decision(session: AsyncSession, decision_id: str) -> DecisionRecord | None:
    """Return a DecisionRecord by primary key, or None."""
    return await session.get(DecisionRecord, decision_id)


async def list_decisions(
    session: AsyncSession,
    repository_id: str,
    *,
    status: str | None = None,
    source: str | None = None,
    tag: str | None = None,
    module: str | None = None,
    include_proposed: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> list[DecisionRecord]:
    """Return decision records with optional filters."""
    q = select(DecisionRecord).where(DecisionRecord.repository_id == repository_id)
    if status is not None:
        q = q.where(DecisionRecord.status == status)
    elif not include_proposed:
        q = q.where(DecisionRecord.status != "proposed")
    if source is not None:
        q = q.where(DecisionRecord.source == source)
    if tag is not None:
        # Match exact tag value in JSON array, not substring.
        # JSON arrays store as '["tag1", "tag2"]', so we match '"tag"'
        q = q.where(DecisionRecord.tags_json.contains(f'"{tag}"'))
    if module is not None:
        # Match exact module path in JSON array
        q = q.where(DecisionRecord.affected_modules_json.contains(f'"{module}"'))
    q = q.order_by(DecisionRecord.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(q)
    return list(result.scalars().all())


async def update_decision_status(
    session: AsyncSession,
    decision_id: str,
    status: str,
    *,
    superseded_by: str | None = None,
) -> DecisionRecord | None:
    """Update the status of a decision record.

    Raises ValueError for invalid statuses. Returns None if not found.
    """
    if status not in _VALID_DECISION_STATUSES:
        raise ValueError(
            f"Unknown decision status {status!r}. Valid values: {sorted(_VALID_DECISION_STATUSES)}"
        )
    rec = await session.get(DecisionRecord, decision_id)
    if rec is None:
        return None
    rec.status = status
    if superseded_by is not None:
        rec.superseded_by = superseded_by
    rec.updated_at = _now_utc()
    await session.flush()
    return rec


async def update_decision_by_id(
    session: AsyncSession,
    decision_id: str,
    **fields: Any,
) -> DecisionRecord | None:
    """Update content fields of a decision record by ID (partial update).

    Accepts keyword arguments for any updatable field:
    title, context, decision, rationale, alternatives, consequences,
    affected_files, affected_modules, tags, evidence_file, evidence_line,
    confidence.

    JSON list fields (alternatives, consequences, affected_files,
    affected_modules, tags) accept Python lists and are serialized to JSON.

    Returns None if the decision is not found.
    """
    rec = await session.get(DecisionRecord, decision_id)
    if rec is None:
        return None

    _json_fields = {
        "alternatives": "alternatives_json",
        "consequences": "consequences_json",
        "affected_files": "affected_files_json",
        "affected_modules": "affected_modules_json",
        "tags": "tags_json",
    }
    _scalar_fields = {
        "title",
        "context",
        "decision",
        "rationale",
        "evidence_file",
        "evidence_line",
        "confidence",
    }

    for key, value in fields.items():
        if key in _json_fields:
            setattr(rec, _json_fields[key], json.dumps(value))
        elif key in _scalar_fields:
            setattr(rec, key, value)

    rec.updated_at = _now_utc()
    await session.flush()
    return rec


async def delete_decision(session: AsyncSession, decision_id: str) -> bool:
    """Delete a decision record. Returns True if deleted, False if not found."""
    rec = await session.get(DecisionRecord, decision_id)
    if rec is None:
        return False
    await session.delete(rec)
    await session.flush()
    return True


def _normalize_title(title: str) -> str:
    """Normalize a decision title for cross-source dedup comparison."""
    import re as _re
    t = title.lower().strip()
    t = _re.sub(r"[^a-z0-9\s]", "", t)
    t = _re.sub(r"\s+", " ", t)
    return t


async def bulk_upsert_decisions(
    session: AsyncSession,
    repository_id: str,
    decisions: list[dict],
) -> None:
    """Bulk upsert decision records from a list of dicts.

    Performs cross-source deduplication: if two decisions from different
    sources have near-identical normalized titles, the one with higher
    confidence wins and the other is skipped.
    """
    # Cross-source dedup: group by normalized title, keep highest confidence
    seen: dict[str, dict] = {}  # normalized_title → best decision dict
    for d in decisions:
        title = d.get("title", "")
        norm = _normalize_title(title)
        if not norm:
            continue
        existing = seen.get(norm)
        if existing is None:
            seen[norm] = d
        else:
            # Keep the one with higher confidence; on tie, prefer more specific source
            new_conf = d.get("confidence", 0.0)
            old_conf = existing.get("confidence", 0.0)
            if new_conf > old_conf:
                seen[norm] = d
            elif new_conf == old_conf:
                # Prefer inline_marker > readme_mining > git_archaeology
                source_priority = {"inline_marker": 3, "readme_mining": 2, "git_archaeology": 1}
                if source_priority.get(d.get("source", ""), 0) > source_priority.get(
                    existing.get("source", ""), 0
                ):
                    seen[norm] = d

    deduped = list(seen.values())

    for i in range(0, len(deduped), _BATCH_SIZE):
        batch = deduped[i : i + _BATCH_SIZE]
        for d in batch:
            await upsert_decision(
                session,
                repository_id=repository_id,
                title=d.get("title", ""),
                status=d.get("status", "proposed"),
                context=d.get("context") or "",
                decision=d.get("decision") or "",
                rationale=d.get("rationale") or "",
                alternatives=d.get("alternatives"),
                consequences=d.get("consequences"),
                affected_files=d.get("affected_files"),
                affected_modules=d.get("affected_modules"),
                tags=d.get("tags"),
                source=d.get("source", "cli"),
                evidence_commits=d.get("evidence_commits"),
                evidence_file=d.get("evidence_file"),
                evidence_line=d.get("evidence_line"),
                confidence=d.get("confidence", 1.0),
                staleness_score=d.get("staleness_score", 0.0),
                superseded_by=d.get("superseded_by"),
            )


async def recompute_decision_staleness(
    session: AsyncSession,
    repository_id: str,
    git_meta_map: dict[str, dict],
) -> int:
    """Recompute staleness_score for all active decisions. Returns update count."""
    result = await session.execute(
        select(DecisionRecord).where(
            DecisionRecord.repository_id == repository_id,
            DecisionRecord.status.in_(["active", "proposed"]),
        )
    )
    decisions = list(result.scalars().all())

    now = _now_utc()
    updated = 0
    for dec in decisions:
        affected = json.loads(dec.affected_files_json)
        if not affected:
            continue

        from repowise.core.analysis.decision_extractor import DecisionExtractor

        decision_text = f"{dec.title} {dec.decision} {dec.rationale}"
        new_score = DecisionExtractor.compute_staleness(
            dec.created_at,
            affected,
            git_meta_map,
            decision_text=decision_text,
        )
        if abs(new_score - dec.staleness_score) > 0.01:
            dec.staleness_score = round(new_score, 3)
            dec.updated_at = now
            updated += 1

    if updated:
        await session.flush()
    return updated


async def get_stale_decisions(
    session: AsyncSession,
    repository_id: str,
    threshold: float = 0.5,
) -> list[DecisionRecord]:
    """Return active decisions with staleness_score >= threshold."""
    result = await session.execute(
        select(DecisionRecord).where(
            DecisionRecord.repository_id == repository_id,
            DecisionRecord.status.in_(["active"]),
            DecisionRecord.staleness_score >= threshold,
        )
    )
    return list(result.scalars().all())


async def get_decision_health_summary(
    session: AsyncSession,
    repository_id: str,
) -> dict:
    """Return decision health: counts by status, stale decisions, ungoverned hotspots."""
    result = await session.execute(
        select(DecisionRecord).where(
            DecisionRecord.repository_id == repository_id,
        )
    )
    all_decisions = list(result.scalars().all())

    counts = {"active": 0, "proposed": 0, "deprecated": 0, "superseded": 0, "stale": 0}
    stale_decisions: list[DecisionRecord] = []
    proposed_decisions: list[DecisionRecord] = []

    # Collect all governed files from active decisions
    governed_files: set[str] = set()
    for d in all_decisions:
        counts[d.status] = counts.get(d.status, 0) + 1
        if d.status == "active":
            if d.staleness_score >= 0.5:
                counts["stale"] += 1
                stale_decisions.append(d)
            for fp in json.loads(d.affected_files_json):
                governed_files.add(fp)
        elif d.status == "proposed":
            proposed_decisions.append(d)

    # Find ungoverned hotspots
    hotspot_result = await session.execute(
        select(GitMetadata.file_path).where(
            GitMetadata.repository_id == repository_id,
            GitMetadata.is_hotspot == True,  # noqa: E712
        )
    )
    hotspot_files = {row[0] for row in hotspot_result.all()}
    ungoverned = sorted(hotspot_files - governed_files)

    return {
        "summary": counts,
        "stale_decisions": stale_decisions,
        "proposed_awaiting_review": proposed_decisions,
        "ungoverned_hotspots": ungoverned,
    }


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------


async def create_conversation(
    session: AsyncSession,
    *,
    repository_id: str,
    title: str = "New conversation",
) -> Conversation:
    conv = Conversation(repository_id=repository_id, title=title)
    session.add(conv)
    await session.flush()
    return conv


async def get_conversation(session: AsyncSession, conversation_id: str) -> Conversation | None:
    return await session.get(Conversation, conversation_id)


async def list_conversations(
    session: AsyncSession, repository_id: str, *, limit: int = 50
) -> list[Conversation]:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.repository_id == repository_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def update_conversation_title(
    session: AsyncSession, conversation_id: str, title: str
) -> Conversation | None:
    conv = await session.get(Conversation, conversation_id)
    if conv:
        conv.title = title
        conv.updated_at = _now_utc()
        await session.flush()
    return conv


async def delete_conversation(session: AsyncSession, conversation_id: str) -> bool:
    conv = await session.get(Conversation, conversation_id)
    if conv is None:
        return False
    await session.delete(conv)
    await session.flush()
    return True


async def touch_conversation(session: AsyncSession, conversation_id: str) -> None:
    """Update the updated_at timestamp of a conversation."""
    conv = await session.get(Conversation, conversation_id)
    if conv:
        conv.updated_at = _now_utc()
        await session.flush()


# ---------------------------------------------------------------------------
# ChatMessage CRUD
# ---------------------------------------------------------------------------


async def create_chat_message(
    session: AsyncSession,
    *,
    conversation_id: str,
    role: str,
    content: dict,
) -> ChatMessage:
    msg = ChatMessage(
        conversation_id=conversation_id,
        role=role,
        content_json=json.dumps(content),
    )
    session.add(msg)
    await session.flush()
    return msg


async def list_chat_messages(session: AsyncSession, conversation_id: str) -> list[ChatMessage]:
    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
    )
    return list(result.scalars().all())


async def count_chat_messages(session: AsyncSession, conversation_id: str) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
    )
    return result.scalar() or 0


# ---------------------------------------------------------------------------
# Graph read-side queries (Phase 5 — MCP graph tools)
# ---------------------------------------------------------------------------


async def get_graph_node(
    session: AsyncSession,
    repository_id: str,
    node_id: str,
) -> GraphNode | None:
    """Look up a single GraphNode by its ``node_id`` (file path or symbol ID)."""
    result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_id == node_id,
        )
    )
    return result.scalar_one_or_none()


async def get_graph_edges_for_node(
    session: AsyncSession,
    repository_id: str,
    node_id: str,
    *,
    direction: str = "both",
    edge_types: list[str] | None = None,
    limit: int = 50,
) -> list[GraphEdge]:
    """Return edges adjacent to *node_id*.

    Parameters
    ----------
    direction:
        ``"callers"`` → inbound edges (target == node_id),
        ``"callees"`` → outbound edges (source == node_id),
        ``"both"`` → union of both.
    edge_types:
        Optional filter, e.g. ``["calls"]`` or ``["extends", "implements"]``.
    limit:
        Max edges per direction.
    """
    results: list[GraphEdge] = []

    if direction in ("callers", "both"):
        q = select(GraphEdge).where(
            GraphEdge.repository_id == repository_id,
            GraphEdge.target_node_id == node_id,
        )
        if edge_types:
            q = q.where(GraphEdge.edge_type.in_(edge_types))
        q = q.limit(limit)
        res = await session.execute(q)
        results.extend(res.scalars().all())

    if direction in ("callees", "both"):
        q = select(GraphEdge).where(
            GraphEdge.repository_id == repository_id,
            GraphEdge.source_node_id == node_id,
        )
        if edge_types:
            q = q.where(GraphEdge.edge_type.in_(edge_types))
        q = q.limit(limit)
        res = await session.execute(q)
        results.extend(res.scalars().all())

    return results


async def get_graph_nodes_by_ids(
    session: AsyncSession,
    repository_id: str,
    node_ids: list[str],
) -> dict[str, GraphNode]:
    """Batch-lookup GraphNodes by node_id. Returns ``{node_id: GraphNode}``."""
    if not node_ids:
        return {}
    # Process in batches to stay under SQLite parameter limits
    out: dict[str, GraphNode] = {}
    for i in range(0, len(node_ids), _BATCH_SIZE):
        batch = node_ids[i : i + _BATCH_SIZE]
        result = await session.execute(
            select(GraphNode).where(
                GraphNode.repository_id == repository_id,
                GraphNode.node_id.in_(batch),
            )
        )
        for node in result.scalars().all():
            out[node.node_id] = node
    return out


async def get_community_members(
    session: AsyncSession,
    repository_id: str,
    community_id: int,
    *,
    node_type: str = "file",
    limit: int = 50,
) -> list[GraphNode]:
    """Return all nodes in a community, ordered by PageRank descending."""
    result = await session.execute(
        select(GraphNode)
        .where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_type == node_type,
            GraphNode.community_id == community_id,
        )
        .order_by(GraphNode.pagerank.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_all_file_metrics(
    session: AsyncSession,
    repository_id: str,
) -> list[GraphNode]:
    """Return all file-type GraphNodes (for percentile computation)."""
    result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_type == "file",
        )
    )
    return list(result.scalars().all())


async def get_cross_community_edges(
    session: AsyncSession,
    repository_id: str,
    community_id: int,
) -> list[dict]:
    """Count edges crossing from *community_id* to other communities.

    Returns a list of ``{"target_community_id": int, "edge_count": int}``.
    Uses a join through ``graph_nodes`` to resolve target community.
    """
    # Alias for the target node lookup
    target_node = GraphNode.__table__.alias("tn")
    source_node = GraphNode.__table__.alias("sn")

    q = (
        select(
            target_node.c.community_id.label("target_community_id"),
            func.count().label("edge_count"),
        )
        .select_from(GraphEdge.__table__)
        .join(
            source_node,
            (GraphEdge.__table__.c.source_node_id == source_node.c.node_id)
            & (GraphEdge.__table__.c.repository_id == source_node.c.repository_id),
        )
        .join(
            target_node,
            (GraphEdge.__table__.c.target_node_id == target_node.c.node_id)
            & (GraphEdge.__table__.c.repository_id == target_node.c.repository_id),
        )
        .where(
            GraphEdge.__table__.c.repository_id == repository_id,
            source_node.c.community_id == community_id,
            target_node.c.community_id != community_id,
            # Only count file-level edges for meaningful community crossing
            source_node.c.node_type == "file",
            target_node.c.node_type == "file",
        )
        .group_by(target_node.c.community_id)
        .order_by(func.count().desc())
    )
    result = await session.execute(q)
    return [
        {"target_community_id": row.target_community_id, "edge_count": row.edge_count}
        for row in result.all()
    ]


async def get_top_entry_points(
    session: AsyncSession,
    repository_id: str,
    *,
    min_score: float = 0.3,
    limit: int = 20,
) -> list[GraphNode]:
    """Return symbol nodes with stored entry_point_score >= *min_score*.

    Scores are stored inside ``community_meta_json``. Since the count of
    symbol nodes is typically < 5000, an in-memory filter is acceptable.
    """
    result = await session.execute(
        select(GraphNode).where(
            GraphNode.repository_id == repository_id,
            GraphNode.node_type == "symbol",
        )
    )
    all_symbols = result.scalars().all()

    scored: list[tuple[float, GraphNode]] = []
    for node in all_symbols:
        try:
            meta = json.loads(node.community_meta_json or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        score = meta.get("entry_point_score")
        if score is not None and score >= min_score:
            scored.append((score, node))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [node for _, node in scored[:limit]]


async def get_node_degree_counts(
    session: AsyncSession,
    repository_id: str,
    node_id: str,
) -> dict[str, int]:
    """Return in-degree and out-degree for a node from edge counts."""
    in_result = await session.execute(
        select(func.count())
        .select_from(GraphEdge)
        .where(
            GraphEdge.repository_id == repository_id,
            GraphEdge.target_node_id == node_id,
        )
    )
    out_result = await session.execute(
        select(func.count())
        .select_from(GraphEdge)
        .where(
            GraphEdge.repository_id == repository_id,
            GraphEdge.source_node_id == node_id,
        )
    )
    return {
        "in_degree": in_result.scalar() or 0,
        "out_degree": out_result.scalar() or 0,
    }
