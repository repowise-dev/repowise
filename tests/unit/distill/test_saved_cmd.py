"""Unit tests for ``repowise saved`` and the tracking rollup SQL."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands.saved_cmd import saved_command
from repowise.core.distill import tracking
from repowise.core.distill.store import OmissionStore


@pytest.fixture()
def repo_cwd(tmp_path: Path, monkeypatch) -> Path:
    """A scratch repo with .repowise/ so the store lands locally."""
    (tmp_path / ".repowise").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _seed(store: OmissionStore) -> None:
    store.record_saving(
        filter_name="test_output",
        source="cli",
        command="pytest",
        raw_tokens=10_000,
        distilled_tokens=1_000,
    )
    store.record_saving(
        filter_name="test_output",
        source="hook",
        command="pytest -x",
        raw_tokens=5_000,
        distilled_tokens=500,
    )
    store.record_saving(
        filter_name="git_status",
        source="hook",
        command="git status",
        raw_tokens=400,
        distilled_tokens=100,
    )


def _store(repo: Path) -> OmissionStore:
    return OmissionStore(repo / ".repowise" / "omissions" / "omissions.db")


# -- tracking rollups -------------------------------------------------------


def test_rollup_by_filter_orders_by_saved_desc(store: OmissionStore) -> None:
    _seed(store)
    rows = store.savings_rollup(by="filter")
    assert [r["group"] for r in rows] == ["test_output", "git_status"]
    assert rows[0]["events"] == 2
    assert rows[0]["saved_tokens"] == 13_500
    assert rows[1]["saved_tokens"] == 300


def test_rollup_by_source(store: OmissionStore) -> None:
    _seed(store)
    rows = store.savings_rollup(by="source")
    by_group = {r["group"]: r for r in rows}
    assert by_group["cli"]["events"] == 1
    assert by_group["hook"]["events"] == 2
    assert by_group["hook"]["saved_tokens"] == 4_800


def test_rollup_by_day_buckets_chronologically(store: OmissionStore) -> None:
    _seed(store)
    # Backdate one event by two days; the rollup must produce two buckets
    # in chronological order.
    store._conn.execute(
        "UPDATE savings SET created_at = created_at - 2 * 86400 WHERE filter = 'git_status'"
    )
    store._conn.commit()
    rows = store.savings_rollup(by="day")
    assert len(rows) == 2
    assert rows[0]["group"] < rows[1]["group"]  # ISO dates sort lexically
    assert rows[0]["saved_tokens"] == 300


def test_rollup_unknown_dimension_raises(store: OmissionStore) -> None:
    with pytest.raises(ValueError, match="Unknown rollup dimension"):
        store.savings_rollup(by="command")


def test_summary_and_rollup_honor_since(store: OmissionStore) -> None:
    _seed(store)
    store._conn.execute(
        "UPDATE savings SET created_at = created_at - 10 * 86400 WHERE filter = 'git_status'"
    )
    store._conn.commit()
    cutoff = time.time() - 86400
    summary = store.savings_summary(since=cutoff)
    assert summary["events"] == 2
    assert "git_status" not in summary["per_filter"]
    rows = store.savings_rollup(by="filter", since=cutoff)
    assert [r["group"] for r in rows] == ["test_output"]


def test_rollup_dimensions_constant_matches_columns() -> None:
    assert set(tracking.ROLLUP_DIMENSIONS) == set(tracking._ROLLUP_COLUMNS)


# -- repowise saved ---------------------------------------------------------


def test_saved_reports_totals_and_per_filter(repo_cwd: Path) -> None:
    s = _store(repo_cwd)
    _seed(s)
    s.close()
    result = CliRunner().invoke(saved_command, [])
    assert result.exit_code == 0
    assert "test_output" in result.output
    assert "git_status" in result.output
    assert "TOTAL" in result.output
    assert "13,800" in result.output  # total saved tokens
    assert "Estimated saved" in result.output
    # The scope caveat must be visible in the report itself (normalize
    # whitespace — rich wraps the caption at terminal width).
    flat = " ".join(result.output.split())
    assert "MCP response truncation is not counted" in flat


def test_saved_by_day(repo_cwd: Path) -> None:
    s = _store(repo_cwd)
    _seed(s)
    s.close()
    result = CliRunner().invoke(saved_command, ["--by", "day"])
    assert result.exit_code == 0
    assert "Events" in result.output


def test_saved_dollar_estimate_uses_input_rate(repo_cwd: Path) -> None:
    s = _store(repo_cwd)
    s.record_saving(
        filter_name="test_output",
        source="cli",
        command="pytest",
        raw_tokens=1_000_000,
        distilled_tokens=0,
    )
    s.close()
    # claude-sonnet-4-6 input rate is $3.00/M -> exactly $3.00 for 1M saved.
    result = CliRunner().invoke(saved_command, [])
    assert result.exit_code == 0
    assert "$3.0000" in result.output


def test_saved_no_store_prints_hint(repo_cwd: Path) -> None:
    result = CliRunner().invoke(saved_command, [])
    assert result.exit_code == 0
    assert "No savings recorded yet" in result.output
    assert "repowise distill" in result.output


def test_saved_empty_ledger_prints_message(repo_cwd: Path) -> None:
    _store(repo_cwd).close()  # creates the DB with zero ledger rows
    result = CliRunner().invoke(saved_command, [])
    assert result.exit_code == 0
    assert "No distillation events recorded" in result.output


def test_saved_since_filters_events(repo_cwd: Path) -> None:
    s = _store(repo_cwd)
    _seed(s)
    s._conn.execute(
        "UPDATE savings SET created_at = created_at - 10 * 86400 WHERE filter = 'git_status'"
    )
    s._conn.commit()
    s.close()
    from datetime import date, timedelta

    cutoff = (date.today() - timedelta(days=1)).isoformat()
    result = CliRunner().invoke(saved_command, ["--since", cutoff])
    assert result.exit_code == 0
    assert "git_status" not in result.output
    assert "test_output" in result.output


def test_saved_bad_since_fails_cleanly(repo_cwd: Path) -> None:
    result = CliRunner().invoke(saved_command, ["--since", "not-a-date"])
    assert result.exit_code != 0
    assert "Cannot parse date" in result.output


def test_saved_explicit_path_argument(tmp_path: Path) -> None:
    repo = tmp_path / "elsewhere"
    (repo / ".repowise").mkdir(parents=True)
    s = _store(repo)
    _seed(s)
    s.close()
    result = CliRunner().invoke(saved_command, [str(repo)])
    assert result.exit_code == 0
    assert "test_output" in result.output
