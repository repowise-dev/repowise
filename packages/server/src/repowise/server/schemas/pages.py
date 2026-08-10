"""Generated-page and generation-job response models."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel


def _layer_stamp(obj: object, metadata: dict | None) -> tuple[str | None, str | None]:
    """Which layer this page belongs to, read off its metadata blob.

    Promoted out of ``metadata`` because a *list* of pages needs it: the docs
    tree groups modules under their layer from this stamp, and it draws itself
    from a summary listing, which drops the blob. Two short strings per row is
    a rounding error next to what the summary saves.

    The caller passes ``metadata`` when it has already parsed it. Otherwise the
    blob is only parsed when its raw text mentions the key at all, so a listing
    of pages that carry no stamp costs a substring scan rather than a decode.
    """
    if metadata is None:
        raw = getattr(obj, "metadata_json", None) or ""
        if "layer_id" not in raw:
            return None, None
        try:
            metadata = json.loads(raw)
        except ValueError:
            # A blob that will not parse is a page we cannot place. Say
            # nothing rather than guess — the tree treats that as "no layer".
            return None, None
    if not isinstance(metadata, dict):
        return None, None
    layer_id = metadata.get("layer_id")
    layer_name = metadata.get("layer_name")
    return (
        layer_id if isinstance(layer_id, str) and layer_id else None,
        layer_name if isinstance(layer_name, str) and layer_name else None,
    )


def _is_chapter(obj: object, metadata: dict | None) -> bool:
    """Whether this page heads a chapter, read off its metadata blob.

    Promoted for the same reason as the layer stamp, and read the same way: a
    chapter *is* a ``module_page`` and only its metadata says otherwise, so a
    reader drawing from a summary listing shows it as an ordinary module that
    happens to have children. That is the reader dead-end a chapter exists to
    close.

    Absent on every page written before chapters shipped, which reads as "not a
    chapter" and leaves those wikis exactly as they render today.
    """
    if metadata is None:
        raw = getattr(obj, "metadata_json", None) or ""
        if "is_chapter" not in raw:
            return False
        try:
            metadata = json.loads(raw)
        except ValueError:
            # Same call as the layer stamp makes: a blob that will not parse
            # says nothing, rather than guessing a shape for the page.
            return False
    if not isinstance(metadata, dict):
        return False
    return bool(metadata.get("is_chapter"))


def _summary_fields(obj: object, metadata: dict | None = None) -> dict:
    """The part of a page row that costs nothing to send.

    Shared by both response models so a field added to one can never go
    missing from the other.
    """
    layer_id, layer_name = _layer_stamp(obj, metadata)
    return dict(
        id=obj.id,  # type: ignore[attr-defined]
        repository_id=obj.repository_id,  # type: ignore[attr-defined]
        page_type=obj.page_type,  # type: ignore[attr-defined]
        title=obj.title,  # type: ignore[attr-defined]
        target_path=obj.target_path,  # type: ignore[attr-defined]
        source_hash=obj.source_hash,  # type: ignore[attr-defined]
        model_name=obj.model_name,  # type: ignore[attr-defined]
        provider_name=obj.provider_name,  # type: ignore[attr-defined]
        input_tokens=obj.input_tokens,  # type: ignore[attr-defined]
        output_tokens=obj.output_tokens,  # type: ignore[attr-defined]
        cached_tokens=obj.cached_tokens,  # type: ignore[attr-defined]
        generation_level=obj.generation_level,  # type: ignore[attr-defined]
        version=obj.version,  # type: ignore[attr-defined]
        confidence=obj.confidence,  # type: ignore[attr-defined]
        freshness_status=obj.freshness_status,  # type: ignore[attr-defined]
        content_chars=len(obj.content or ""),  # type: ignore[attr-defined]
        layer_id=layer_id,
        layer_name=layer_name,
        is_chapter=_is_chapter(obj, metadata),
        human_notes=obj.human_notes,  # type: ignore[attr-defined]
        parent_page_id=obj.parent_page_id,  # type: ignore[attr-defined]
        display_order=obj.display_order,  # type: ignore[attr-defined]
        section_number=obj.section_number,  # type: ignore[attr-defined]
        structural_key=obj.structural_key,  # type: ignore[attr-defined]
        created_at=obj.created_at,  # type: ignore[attr-defined]
        updated_at=obj.updated_at,  # type: ignore[attr-defined]
    )


class PageSummaryResponse(BaseModel):
    """A page row without its two heavy fields.

    On a 5,485-page wiki a full listing measured 38.6 MB, of which ``content`` and
    ``metadata`` are 95%. Nothing that draws a list of pages — the docs tree,
    breadcrumbs, the command palette — reads either one, so ``GET /api/pages``
    can serve this instead when the caller asks for ``fields=summary``.

    ``content_chars`` stands in for the one thing a list genuinely wanted the
    body for: ranking pages by how much was written.
    """

    id: str
    repository_id: str
    page_type: str
    title: str
    target_path: str
    source_hash: str
    model_name: str
    provider_name: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    generation_level: int
    version: int
    confidence: float
    freshness_status: str
    content_chars: int
    # Which layer this page belongs to, stamped at generation time. ``None``
    # on a page no layer claimed, and on every page of a repo indexed before
    # layers were stamped — both of which read as "ungrouped", not as an error.
    layer_id: str | None = None
    layer_name: str | None = None
    # Whether this module page heads a chapter. A chapter shares its page type
    # with the modules beneath it, so without this a listing cannot tell them
    # apart and the reader labels a chapter "Module".
    is_chapter: bool = False
    human_notes: str | None = None
    # Position in the wiki outline. Older rows carry no placement, which reads
    # as a flat wiki and is what those rows actually describe.
    parent_page_id: str | None = None
    display_order: int = 0
    section_number: str | None = None
    structural_key: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, obj: object) -> PageSummaryResponse:
        return cls(**_summary_fields(obj))


class PageResponse(PageSummaryResponse):
    content: str
    metadata: dict

    @classmethod
    def from_orm(cls, obj: object) -> PageResponse:
        # Parsed once and handed down, so a full listing never decodes the same
        # blob twice just to read the layer stamp out of it.
        metadata = json.loads(obj.metadata_json)  # type: ignore[attr-defined]
        return cls(
            **_summary_fields(obj, metadata),
            content=obj.content,  # type: ignore[attr-defined]
            metadata=metadata,
        )


class PageVersionResponse(BaseModel):
    id: str
    page_id: str
    version: int
    page_type: str
    title: str
    content: str
    source_hash: str
    model_name: str
    provider_name: str
    input_tokens: int
    output_tokens: int
    confidence: float
    archived_at: datetime

    @classmethod
    def from_orm(cls, obj: object) -> PageVersionResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            page_id=obj.page_id,  # type: ignore[attr-defined]
            version=obj.version,  # type: ignore[attr-defined]
            page_type=obj.page_type,  # type: ignore[attr-defined]
            title=obj.title,  # type: ignore[attr-defined]
            content=obj.content,  # type: ignore[attr-defined]
            source_hash=obj.source_hash,  # type: ignore[attr-defined]
            model_name=obj.model_name,  # type: ignore[attr-defined]
            provider_name=obj.provider_name,  # type: ignore[attr-defined]
            input_tokens=obj.input_tokens,  # type: ignore[attr-defined]
            output_tokens=obj.output_tokens,  # type: ignore[attr-defined]
            confidence=obj.confidence,  # type: ignore[attr-defined]
            archived_at=obj.archived_at,  # type: ignore[attr-defined]
        )


class JobResponse(BaseModel):
    id: str
    repository_id: str
    status: str
    provider_name: str
    model_name: str
    total_pages: int
    completed_pages: int
    failed_pages: int
    current_level: int
    error_message: str | None
    config: dict
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    # Short-lived token for the SSE progress stream (an EventSource can't send
    # the bearer header). Only minted while the job is live; ``None`` once it
    # reaches a terminal state, since there's nothing left to stream. Any client
    # that can read this job (already authenticated) can therefore obtain a fresh
    # stream token, which is what lets a reloaded page reconnect to the stream.
    stream_token: str | None = None

    @classmethod
    def from_orm(cls, obj: object) -> JobResponse:
        status = obj.status  # type: ignore[attr-defined]
        stream_token: str | None = None
        if status in ("pending", "running"):
            from repowise.server.stream_auth import mint_stream_token

            stream_token = mint_stream_token(obj.id)  # type: ignore[attr-defined]
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            repository_id=obj.repository_id,  # type: ignore[attr-defined]
            status=status,
            provider_name=obj.provider_name,  # type: ignore[attr-defined]
            model_name=obj.model_name,  # type: ignore[attr-defined]
            total_pages=obj.total_pages,  # type: ignore[attr-defined]
            completed_pages=obj.completed_pages,  # type: ignore[attr-defined]
            failed_pages=obj.failed_pages,  # type: ignore[attr-defined]
            current_level=obj.current_level,  # type: ignore[attr-defined]
            error_message=obj.error_message,  # type: ignore[attr-defined]
            config=json.loads(obj.config_json),  # type: ignore[attr-defined]
            created_at=obj.created_at,  # type: ignore[attr-defined]
            updated_at=obj.updated_at,  # type: ignore[attr-defined]
            started_at=obj.started_at,  # type: ignore[attr-defined]
            finished_at=obj.finished_at,  # type: ignore[attr-defined]
            stream_token=stream_token,
        )
