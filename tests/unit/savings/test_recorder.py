"""The one write path every capture surface goes through.

The behaviours here are the ones that used to be re-decided per surface, and
the ones a surface must be able to rely on without checking: an absent sidecar
is an opt-out rather than something to create, a malformed event never reaches
the caller as an exception, and a repository cannot be attributed an event it
is not writing to.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from repowise.core.distill.store import OmissionStore
from repowise.core.savings import recorder
from repowise.core.savings.correlation import scoped_idempotency_key


def _event(index: int = 1, **overrides: object) -> dict:
    payload = {
        "idempotency_key": scoped_idempotency_key("repo", "mcp", index),
        "occurred_at": f"2026-09-18T00:00:0{index}Z",
        "surface": "mcp",
        "integration": "claude_code",
        "agent": "claude_code",
        "operation": "get_risk",
        "evidence_kind": "measured",
        "estimator": "chars_per_token_floor_v1",
        "token_unit": "estimated_tokens",
        "result_state": "success",
        "is_usable": True,
        "baseline_input_tokens": 100,
        "pre_budget_input_tokens": 100,
        "delivered_input_tokens": 40,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository that has opted in, i.e. one whose sidecar exists."""
    OmissionStore(recorder.sidecar_path(tmp_path)).close()
    return tmp_path


def test_an_event_is_written_and_attributed_to_the_repository(repo: Path) -> None:
    assert recorder.record_event(repo, _event()) is True
    with OmissionStore(recorder.sidecar_path(repo)) as store:
        row = store._conn.execute(
            "SELECT repository_id, agent, saved_input_tokens FROM savings_events"
        ).fetchone()
    assert tuple(row) == (str(repo), "claude_code", 60)


def test_a_repository_cannot_be_attributed_an_event_it_is_not_writing(repo: Path) -> None:
    """``repository_id`` comes from the path written to, never from the payload.

    Otherwise a surface holding a stale repo id files one repository's savings
    under another, and nothing downstream could tell.
    """
    recorder.record_event(repo, _event(repository_id="/somewhere/else"))
    with OmissionStore(recorder.sidecar_path(repo)) as store:
        stored = store._conn.execute("SELECT repository_id FROM savings_events").fetchone()[0]
    assert stored == str(repo)


def test_a_retry_of_the_same_event_reports_that_it_added_nothing(repo: Path) -> None:
    assert recorder.record_event(repo, _event()) is True
    assert recorder.record_event(repo, _event()) is False
    with OmissionStore(recorder.sidecar_path(repo)) as store:
        assert store._conn.execute("SELECT COUNT(*) FROM savings_events").fetchone()[0] == 1


def test_an_absent_sidecar_is_an_opt_out_rather_than_something_to_create(
    tmp_path: Path,
) -> None:
    """A repository that never ran ``init`` does not acquire a ledger by being used."""
    assert recorder.record_event(tmp_path, _event()) is False
    assert not recorder.sidecar_path(tmp_path).exists()


def test_no_repository_means_no_guess(tmp_path: Path) -> None:
    """Never fall back to a home-directory store: the dashboard would not see it."""
    assert recorder.record_event(None, _event()) is False
    assert recorder.record_event("", _event()) is False


@pytest.mark.parametrize(
    "broken",
    [
        {"surface": "telepathy"},
        {"agent": "Claude Code"},
        {"result_state": "success", "is_usable": False},
        {"metadata": {"prompt": "do not persist"}},
        {"occurred_at": "not a timestamp"},
    ],
)
def test_a_malformed_event_is_dropped_rather_than_raised(repo: Path, broken: dict) -> None:
    """A ledger that can fail a live tool call is worse than no ledger."""
    assert recorder.record_event(repo, _event(**broken)) is False
    with OmissionStore(recorder.sidecar_path(repo)) as store:
        assert store._conn.execute("SELECT COUNT(*) FROM savings_events").fetchone()[0] == 0


def test_a_write_failure_is_swallowed(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The surface keeps working when the database does not."""
    import repowise.core.savings.repository as repository_module

    def explode(self: object, event: object) -> bool:
        raise RuntimeError("disk gone")

    monkeypatch.setattr(repository_module.SavingsRepository, "record_event", explode)
    assert recorder.record_event(repo, _event()) is False


def test_an_opportunity_never_reaches_the_event_ledger(repo: Path) -> None:
    """Separate functions, so no caller can file one as the other."""
    assert (
        recorder.record_opportunity(
            repo,
            {
                "observation_id": "obs-1",
                "occurred_at": "2026-09-18T00:00:02Z",
                "integration": "claude_code",
                "kind": "bypassed_distillation",
                "estimated_potential_input_tokens": 500,
            },
        )
        is True
    )
    with OmissionStore(recorder.sidecar_path(repo)) as store:
        assert store._conn.execute("SELECT COUNT(*) FROM savings_events").fetchone()[0] == 0
        report = store.savings().report(str(repo), as_of=datetime(2026, 9, 19, tzinfo=UTC))
    assert report.saved_input_tokens == 0
    assert report.opportunity_count == 1
    assert report.opportunity_tokens_excluded == 500


def test_a_malformed_opportunity_is_dropped_rather_than_raised(repo: Path) -> None:
    assert recorder.record_opportunity(repo, {"kind": "missing everything else"}) is False


def test_the_id_the_surface_minted_is_the_id_stored(repo: Path) -> None:
    """Otherwise nothing joins a log line, or a response, to its ledger row.

    The contract substitutes a fresh id unless told to accept one, and the
    substitution is silent: the row exists, it just is not the event the surface
    thinks it wrote. It also scopes the idempotency key on an id that was thrown
    away, which makes retry deduplication unreachable while appearing to work.
    """
    minted = "11111111-2222-3333-4444-555555555555"
    assert recorder.record_event(repo, _event(event_id=minted)) is True
    with OmissionStore(recorder.sidecar_path(repo)) as store:
        stored = store._conn.execute("SELECT event_id FROM savings_events").fetchone()[0]
    assert stored == minted


def test_the_sidecar_path_matches_the_store_that_owns_it(tmp_path: Path) -> None:
    """The path is spelled out here to avoid a structlog import; keep it honest."""
    from repowise.core.distill.store import OMISSIONS_DB_FILENAME, OMISSIONS_DIRNAME

    assert recorder.sidecar_path(tmp_path) == (
        tmp_path / ".repowise" / OMISSIONS_DIRNAME / OMISSIONS_DB_FILENAME
    )


def test_a_raw_connection_reaches_the_same_ledger(repo: Path) -> None:
    """The hook writes on the connection it already has, importing nothing heavy.

    Reaching the ledger through ``OmissionStore`` would pull structlog, roughly
    250ms, on the surface whose whole justification is latency.
    """
    import sqlite3

    connection = sqlite3.connect(str(recorder.sidecar_path(repo)), timeout=2)
    try:
        assert recorder.record_event_on(connection, repo, _event()) is True
    finally:
        connection.close()
    with OmissionStore(recorder.sidecar_path(repo)) as store:
        assert store._conn.execute("SELECT COUNT(*) FROM savings_events").fetchone()[0] == 1


def test_a_bad_repository_argument_does_not_raise(repo: Path) -> None:
    """ "Never raises" has to hold for the argument too, not just the write."""
    assert recorder.record_event(object(), _event()) is False  # type: ignore[arg-type]
    assert recorder.record_opportunity(object(), {}) is False  # type: ignore[arg-type]
