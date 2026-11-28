"""REST datetime fields must identify UTC explicitly."""

import importlib
import inspect
import pkgutil
from datetime import UTC, datetime, timedelta, timezone
from typing import Annotated, get_args, get_origin

from pydantic import BaseModel, PlainSerializer

from repowise.server.schemas import (
    ChatMessageResponse,
    JobResponse,
    RepoResponse,
)


def test_naive_sqlite_datetime_is_serialized_as_utc() -> None:
    response = RepoResponse(
        id="repo",
        name="repo",
        url="",
        local_path="/tmp/repo",
        default_branch="main",
        head_commit=None,
        settings={},
        created_at=datetime(2026, 9, 8, 20, 2, 58, 949469),
        updated_at=datetime(2026, 9, 8, 20, 2, 58, 949469),
    )

    payload = response.model_dump(mode="json")

    assert payload["created_at"] == "2026-09-08T20:02:58.949469Z"
    assert payload["updated_at"] == "2026-09-08T20:02:58.949469Z"


def test_aware_datetime_is_normalized_to_utc() -> None:
    response = ChatMessageResponse(
        id="message",
        conversation_id="conversation",
        role="user",
        content={"text": "hello"},
        created_at=datetime(2026, 9, 8, 22, 2, 58, tzinfo=timezone(timedelta(hours=2))),
    )

    assert response.model_dump(mode="json")["created_at"] == "2026-09-08T20:02:58Z"


def test_optional_job_datetimes_keep_null_and_emit_utc() -> None:
    response = JobResponse(
        id="job",
        repository_id="repo",
        status="running",
        provider_name="mock",
        model_name="mock",
        total_pages=1,
        completed_pages=0,
        failed_pages=0,
        current_level=0,
        error_message=None,
        config={},
        created_at=datetime(2026, 9, 8, tzinfo=UTC),
        updated_at=datetime(2026, 9, 8, tzinfo=UTC),
        started_at=datetime(2026, 9, 8),
        finished_at=None,
    )

    payload = response.model_dump(mode="json")

    assert payload["started_at"] == "2026-09-08T00:00:00Z"
    assert payload["finished_at"] is None


def _naive_datetime(annotation: object, metadata: tuple = ()) -> bool:
    """True when ``annotation`` holds a ``datetime`` without the UTC serializer."""
    if annotation is datetime:
        return not any(isinstance(m, PlainSerializer) for m in metadata)
    if get_origin(annotation) is Annotated:
        inner, *extra = get_args(annotation)
        return _naive_datetime(inner, (*metadata, *extra))
    return any(_naive_datetime(arg) for arg in get_args(annotation))


def test_every_schema_datetime_field_serializes_as_utc() -> None:
    """New response fields must use ``UTCDateTime``, not a bare ``datetime``."""
    import repowise.server.schemas as schemas

    offenders = []
    for info in pkgutil.iter_modules(schemas.__path__):
        module = importlib.import_module(f"{schemas.__name__}.{info.name}")
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if not issubclass(cls, BaseModel) or cls.__module__ != module.__name__:
                continue
            for field_name, field in cls.model_fields.items():
                if _naive_datetime(field.annotation, tuple(field.metadata)):
                    offenders.append(f"{module.__name__}.{name}.{field_name}")

    assert offenders == [], f"use UTCDateTime for these fields: {offenders}"
