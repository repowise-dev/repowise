"""The hook surfaces record canonical events, attributed to the real agent.

This is the one surface where attribution is evidence rather than inference:
the hook is handed the serving agent's own adapter, so it knows which agent
saved the tokens. MCP has to resolve a self-declared name, and distill does not
know at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Row

import pytest

from repowise.cli.commands.augment_cmd import _shared
from repowise.core.distill.store import OmissionStore
from repowise.core.savings import recorder


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    OmissionStore(recorder.sidecar_path(tmp_path)).close()
    return tmp_path


def _events(repo: Path) -> list[dict]:
    with OmissionStore(recorder.sidecar_path(repo)) as store:
        store._conn.row_factory = Row
        return [dict(row) for row in store._conn.execute("SELECT * FROM savings_events")]


def _record(repo: Path, **overrides: object) -> None:
    kwargs: dict = {
        "source": "hook-read",
        "filter_name": "read_skeleton",
        "command": "src/app.py",
        "raw_tokens": 4000,
        "distilled_tokens": 400,
        "hook_adapter": "claude-code",
    }
    kwargs.update(overrides)
    _shared.record_saving(repo, **kwargs)


def test_a_served_replacement_records_a_measured_hook_event(repo: Path) -> None:
    _record(repo)
    events = _events(repo)
    assert len(events) == 1
    event = events[0]
    assert event["surface"] == "hook"
    assert event["operation"] == "read_skeleton"
    assert event["evidence_kind"] == "measured"
    assert event["result_state"] == "success"
    assert event["baseline_input_tokens"] == 4000
    assert event["delivered_input_tokens"] == 400
    assert event["saved_input_tokens"] == 3600


@pytest.mark.parametrize(
    ("adapter", "expected"),
    [("claude-code", "claude_code"), ("codex", "codex"), (None, "unknown"), ("nope", "unknown")],
)
def test_the_adapter_names_the_agent_that_saved_the_tokens(
    repo: Path, adapter: str | None, expected: str
) -> None:
    """Resolved through the identity registry, not a second mapping here.

    An adapter name the registry does not know resolves to ``unknown`` rather
    than being stored raw: the hook adapter spelling is not the canonical slug,
    and storing it would put a fourth spelling in the ledger.
    """
    _record(repo, hook_adapter=adapter)
    event = _events(repo)[0]
    assert event["agent"] == expected
    assert event["integration"] == expected


def test_the_legacy_row_is_still_written(repo: Path) -> None:
    """Both ledgers, until the reporting consumers move to the canonical one."""
    _record(repo)
    with OmissionStore(recorder.sidecar_path(repo)) as store:
        legacy = store.savings_summary()
    assert legacy["saved_tokens"] == 3600
    assert _events(repo)[0]["saved_input_tokens"] == 3600


def test_the_replaced_path_is_never_persisted(repo: Path) -> None:
    """``command`` is a file path on this surface, and paths are not telemetry."""
    _record(repo, command="/home/someone/secret-project/app.py")
    stored = str(_events(repo)[0])
    assert "secret-project" not in stored
    assert "someone" not in stored


def test_a_repository_without_a_sidecar_records_nothing(tmp_path: Path) -> None:
    """A hook is not the place to decide a repo has opted into bookkeeping."""
    _record(tmp_path)
    assert not recorder.sidecar_path(tmp_path).exists()


def test_two_replacements_stay_two_interactions(repo: Path) -> None:
    _record(repo)
    _record(repo)
    events = _events(repo)
    assert len(events) == 2
    assert len({event["event_id"] for event in events}) == 2


def test_a_forgone_saving_is_an_opportunity_not_an_achievement(repo: Path) -> None:
    """The repo did not save these tokens; it only could have.

    They must never reach a total of what was saved, which is why they land in
    a different table rather than behind a flag on the same one.
    """
    _shared.record_forgone(
        repo,
        source="hook-read",
        path="src/app.py",
        raw_tokens=4000,
        distilled_tokens=400,
        filter_name="read_skeleton",
    )
    assert _events(repo) == []
    with OmissionStore(recorder.sidecar_path(repo)) as store:
        report = store.savings().report(str(repo), as_of=datetime.now(UTC))
        row = store._conn.execute("SELECT kind FROM savings_opportunities").fetchone()
    assert report.saved_input_tokens == 0
    assert report.opportunity_count == 1
    assert report.opportunity_tokens_excluded == 3600
    assert row[0] == "hook_surface_disabled:read_skeleton"


def test_a_forgone_saving_does_not_record_the_path(repo: Path) -> None:
    _shared.record_forgone(
        repo,
        source="hook-read",
        path="/home/someone/secret-project/app.py",
        raw_tokens=4000,
        distilled_tokens=400,
        filter_name="read_skeleton",
    )
    with OmissionStore(recorder.sidecar_path(repo)) as store:
        store._conn.row_factory = Row
        stored = str([dict(r) for r in store._conn.execute("SELECT * FROM savings_opportunities")])
    assert "secret-project" not in stored
