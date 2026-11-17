"""Shared datetime types for the REST API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def _serialize_utc(value: datetime) -> str:
    """Serialize an instant as an explicit UTC ISO-8601 value.

    SQLite drops ``tzinfo`` when round-tripping SQLAlchemy datetime columns,
    while PostgreSQL preserves it. The persistence layer stores these values as
    UTC, so a naive value read from SQLite is UTC as well. Stamping it here
    keeps every REST client from interpreting it as local browser time.
    """
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.isoformat().replace("+00:00", "Z")


UTCDateTime = Annotated[
    datetime,
    PlainSerializer(_serialize_utc, return_type=str, when_used="json"),
]
