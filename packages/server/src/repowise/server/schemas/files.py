"""Schemas for the file-detail aggregate and the doc-pin action."""

from __future__ import annotations

from pydantic import BaseModel


class PinDocResponse(BaseModel):
    """Result of pinning a file's doc (issue #812)."""

    file_path: str
    pinned: bool
