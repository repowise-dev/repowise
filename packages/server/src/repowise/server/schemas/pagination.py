"""Pagination envelope shared across list endpoints."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Paginated(BaseModel, Generic[T]):
    """Stable envelope for paginated list endpoints.

    Lets the UI show "Showing N of M / Load more" without guessing whether
    a list was truncated by the server. `next_offset` is null when there
    are no further pages.
    """

    items: list[T]
    total: int
    has_more: bool
    next_offset: int | None = None
