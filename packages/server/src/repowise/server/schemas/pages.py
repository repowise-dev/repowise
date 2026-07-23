"""Generated-page and generation-job response models."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel


class PageResponse(BaseModel):
    id: str
    repository_id: str
    page_type: str
    title: str
    content: str
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
    metadata: dict
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
    def from_orm(cls, obj: object) -> PageResponse:
        metadata = json.loads(obj.metadata_json)  # type: ignore[attr-defined]
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            repository_id=obj.repository_id,  # type: ignore[attr-defined]
            page_type=obj.page_type,  # type: ignore[attr-defined]
            title=obj.title,  # type: ignore[attr-defined]
            content=obj.content,  # type: ignore[attr-defined]
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
            metadata=metadata,
            human_notes=obj.human_notes,  # type: ignore[attr-defined]
            parent_page_id=obj.parent_page_id,  # type: ignore[attr-defined]
            display_order=obj.display_order,  # type: ignore[attr-defined]
            section_number=obj.section_number,  # type: ignore[attr-defined]
            structural_key=obj.structural_key,  # type: ignore[attr-defined]
            created_at=obj.created_at,  # type: ignore[attr-defined]
            updated_at=obj.updated_at,  # type: ignore[attr-defined]
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
