"""REST datetime fields must identify UTC explicitly."""

from datetime import UTC, datetime, timedelta, timezone

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
