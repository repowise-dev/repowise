"""Retirement identity and complete native readback, separate from live pages."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ._datetime import UTCDateTime


class RetiredPageSummary(BaseModel):
    archive_id: str
    page_id: str
    repository_id: str
    retired_at: UTCDateTime
    reason: str
    superseded_by: str | None
    title: str


class RetiredPageDetail(RetiredPageSummary):
    page: dict[str, Any]
    versions: list[dict[str, Any]]
