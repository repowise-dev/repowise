"""Distillation records a canonical event beside its legacy ledger row.

The legacy row stays for now because the costs endpoint, the overview headline
and ``repowise saved`` all still read it. So the property under test is that
both land and agree, not that one replaced the other.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Row

import pytest

from repowise.core.distill.engine import distill_output
from repowise.core.distill.store import OmissionStore
from repowise.core.savings import recorder

# Long enough to beat the filter's min_lines and the net-positive floor.
PYTEST_OUTPUT = "\n".join(
    ["=" * 70, "FAILED tests/test_a.py::test_one - AssertionError: nope"]
    + [f"     boilerplate line {index} that carries no signal whatsoever" for index in range(400)]
    + ["1 failed, 399 passed in 12.34s"]
)


@pytest.fixture
def store(tmp_path: Path):
    opened = OmissionStore(recorder.sidecar_path(tmp_path))
    try:
        yield opened
    finally:
        opened.close()


def _events(store: OmissionStore) -> list[dict]:
    store._conn.row_factory = Row
    return [dict(row) for row in store._conn.execute("SELECT * FROM savings_events")]


def test_a_distillation_records_one_measured_event(store: OmissionStore) -> None:
    result = distill_output(PYTEST_OUTPUT, command="pytest", source="cli", store=store)
    assert result.distilled

    events = _events(store)
    assert len(events) == 1
    event = events[0]
    assert event["surface"] == "distill"
    assert event["operation"] == result.filter_name
    assert event["evidence_kind"] == "measured"
    assert event["estimator"] == "chars_per_token_floor_v1"
    assert event["result_state"] == "success"
    assert event["baseline_input_tokens"] == result.raw_tokens
    assert event["delivered_input_tokens"] == result.distilled_tokens
    assert event["saved_input_tokens"] == result.raw_tokens - result.distilled_tokens


def test_the_event_agrees_with_the_legacy_row_it_sits_beside(store: OmissionStore) -> None:
    distill_output(PYTEST_OUTPUT, command="pytest", source="cli", store=store)
    legacy = store.savings_summary()
    event = _events(store)[0]
    assert event["saved_input_tokens"] == legacy["saved_tokens"]
    assert event["delivered_input_tokens"] == legacy["distilled_tokens"]


def test_the_recovery_reference_links_the_event_to_its_artifact(store: OmissionStore) -> None:
    """The event points at the omission rather than duplicating its content."""
    result = distill_output(PYTEST_OUTPUT, command="pytest", source="cli", store=store)
    linked = store._conn.execute("SELECT omission_ref FROM savings_event_omissions").fetchall()
    assert [row[0] for row in linked] == [result.ref]
    assert store.get(result.ref) is not None


def test_the_rewrite_hooks_shell_is_a_hook_not_the_cli(store: OmissionStore) -> None:
    """``source`` separates the surfaces; it does not name an agent."""
    distill_output(PYTEST_OUTPUT, command="pytest", source="hook-bash", store=store)
    event = _events(store)[0]
    assert event["surface"] == "hook"
    assert event["agent"] == "unknown"
    assert event["integration"] == "unknown"


def test_output_not_worth_distilling_records_nothing(store: OmissionStore) -> None:
    """No saving, no event. A zero-token row would only dilute the ledger."""
    assert not distill_output("one short line\n", command="pytest", store=store).distilled
    assert _events(store) == []


def test_the_command_is_never_persisted(store: OmissionStore) -> None:
    """A command line can carry paths, hostnames and secrets."""
    distill_output(
        PYTEST_OUTPUT,
        command="pytest --token=hunter2 /home/someone/private",
        source="cli",
        store=store,
    )
    stored = str(_events(store)[0])
    assert "hunter2" not in stored
    assert "private" not in stored


def test_two_distillations_of_identical_output_stay_independent(store: OmissionStore) -> None:
    """The omission ref is a content hash, so two identical runs share one.

    The events must not collapse with it: they are two interactions that each
    really saved tokens, and the ledger counts interactions.
    """
    first = distill_output(PYTEST_OUTPUT, command="pytest", source="cli", store=store)
    second = distill_output(PYTEST_OUTPUT, command="pytest", source="cli", store=store)
    assert first.ref == second.ref

    events = _events(store)
    assert len(events) == 2
    assert len({event["event_id"] for event in events}) == 2
    report = store.savings().report(str(store.db_path.parents[2]), as_of=datetime.now(UTC))
    assert report.unique_events == 2
    assert report.saving_interactions == 2


def test_the_event_is_attributed_to_the_repository_not_to_a_derivation(
    tmp_path: Path, store: OmissionStore
) -> None:
    """Spelled out rather than recomputed from the expression under test."""
    distill_output(PYTEST_OUTPUT, command="pytest", source="cli", store=store)
    assert _events(store)[0]["repository_id"] == str(tmp_path)


def test_a_home_directory_store_is_refused(tmp_path: Path, monkeypatch) -> None:
    """``open_default`` falls back to ``~/.repowise`` when there is no repo.

    The same path arithmetic would then call the home directory a repository and
    merge every uninitialized directory on the machine into one ledger. The
    legacy row still lands there, as it always has; the canonical event does not.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    opened = OmissionStore(home / ".repowise" / "omissions" / "omissions.db")
    try:
        result = distill_output(PYTEST_OUTPUT, command="pytest", source="cli", store=opened)
        assert result.distilled, "the distillation itself must still work"
        assert _events(opened) == []
        assert opened.savings_summary()["saved_tokens"] > 0
    finally:
        opened.close()
