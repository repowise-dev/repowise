"""Per-decision delivery counts read from the hook ledger."""

from __future__ import annotations

from pathlib import Path

from repowise.core.sessions.deliveries import (
    fold_deliveries,
    ledger_deliveries,
    record_why_deliveries,
)
from repowise.core.sessions.efficacy import has_unsettled_firings
from repowise.core.sessions.staging import SessionStagingStore

_A = "a" * 32
_B = "b" * 32
_OLD = "c" * 32


def _row(repo: Path, *, session: str, key: str, surface: str, category: str, node: str = "", at: float):
    store = SessionStagingStore.open_default(repo)
    try:
        store.add_settled_row(
            session_id=session, key=key, node_id=node, surface=surface, category=category, shown_at=at
        )
        store.commit()
    finally:
        store.close()


def test_no_ledger_reads_as_unknown_not_zero(tmp_path: Path) -> None:
    assert ledger_deliveries(tmp_path) is None
    assert not (tmp_path / ".repowise").exists()


def test_every_delivery_moment_counts_toward_its_record(tmp_path: Path) -> None:
    _row(tmp_path, session="s1", key=_A, surface="decision", category="edit_notice", at=100.0)
    _row(tmp_path, session="s2", key=_A, surface="decision", category="session_start", at=200.0)
    record_why_deliveries(tmp_path, "mcp:c1", [_A, _B], now=300.0)
    # The commit line names its records in node_id; a replayed row has the prefix only.
    _row(tmp_path, session="s3", key="k1", surface="upkeep", category="commit_notice",
         node=f"{_A} {_B}", at=400.0)
    _row(tmp_path, session="s4", key="k2", surface="upkeep", category="commit_notice",
         node=_B[:8], at=500.0)
    # Not a delivery of a decision.
    _row(tmp_path, session="s5", key="k3", surface="search", category="rescue", node=_A, at=600.0)

    got = fold_deliveries(ledger_deliveries(tmp_path), [_A, _B], {})

    assert got[_A].count == 4 and got[_A].last_at.timestamp() == 400.0
    assert got[_B].count == 3 and got[_B].last_at.timestamp() == 500.0


def test_rows_under_a_retired_id_count_for_the_record_it_resolves_to() -> None:
    got = fold_deliveries([(_OLD, 10.0), (_A, 20.0)], [_A], {_OLD: _A})

    assert got[_A].count == 2


def test_a_record_never_shown_reads_zero_with_no_date() -> None:
    got = fold_deliveries([(_A, 10.0)], [_B], {})

    assert got[_B].count == 0 and got[_B].last_at is None


def test_get_why_counts_a_record_once_per_session(tmp_path: Path) -> None:
    assert record_why_deliveries(tmp_path, "mcp:c1", [_A, _A]) == 1
    assert record_why_deliveries(tmp_path, "mcp:c1", [_A]) == 0
    assert record_why_deliveries(tmp_path, "mcp:c2", [_A]) == 1
    assert record_why_deliveries(tmp_path, "", [_A]) == 0


def test_get_why_rows_never_take_the_edit_notice_slot(tmp_path: Path) -> None:
    """The ledger keys one row per session and key, so a get_why row must not
    occupy the key the edit notice claims for the same record."""
    record_why_deliveries(tmp_path, "s1", [_A])
    store = SessionStagingStore.open_default(tmp_path)
    try:
        claimed = store.add_settled_row(
            session_id="s1", key=_A, node_id="src/x.py", surface="decision",
            category="edit_notice", shown_at=1.0,
        )
    finally:
        store.close()

    assert claimed


def test_unsettled_firings_gate(tmp_path: Path) -> None:
    assert not has_unsettled_firings(tmp_path)
    record_why_deliveries(tmp_path, "s1", [_A])
    # A settled row is nothing to classify.
    assert not has_unsettled_firings(tmp_path)

    store = SessionStagingStore.open_default(tmp_path)
    try:
        store._conn.execute(
            "INSERT INTO injections (session_id, decision_id, shown_at, surface, category) "
            "VALUES ('s1', 'k', 1.0, 'upkeep', 'commit_notice')"
        )
        store.commit()
    finally:
        store.close()

    assert has_unsettled_firings(tmp_path)
