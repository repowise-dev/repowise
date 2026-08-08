"""Unit tests for ``repowise saved`` and the tracking rollup SQL."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.commands import saved_cmd
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
    # The scope caption must be visible in the report itself (normalize
    # whitespace — rich wraps the caption at terminal width).
    flat = " ".join(result.output.split())
    assert "MCP counterfactual savings (mcp:<tool>)" in flat


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
    # Pinned explicitly: the rate is now detected, and a tmp repo happens to
    # have no session to detect, which is not something to assert by accident.
    result = CliRunner().invoke(saved_command, ["--model", "claude-sonnet-4-6"])
    assert result.exit_code == 0
    assert "$3.0000" in result.output


class TestPricingModelResolution:
    """Saved tokens must cost what the agent that saved them costs.

    The Costs endpoint has always priced this ledger at the detected session
    model while this command assumed Sonnet, so the same ledger produced two
    dollar figures — and the assumed one understates an Opus session by two
    thirds.
    """

    def test_explicit_model_wins(self, tmp_path: Path) -> None:
        model, note = saved_cmd._resolve_pricing(tmp_path, "gpt-5.4-nano")
        assert model == "gpt-5.4-nano"
        assert note == "gpt-5.4-nano"

    def test_detected_model_is_used_and_named(self, tmp_path: Path, monkeypatch) -> None:
        from repowise.core.distill import session_model

        detected = session_model.ResolvedModel(
            model="claude-opus-5",
            raw="claude-opus-5[1m]",
            agent="claude_code",
            source="detected from Claude Code session",
        )
        monkeypatch.setattr(session_model, "resolve_session_model", lambda *a, **k: detected)
        model, note = saved_cmd._resolve_pricing(tmp_path, None)
        assert model == "claude-opus-5"
        # The reader is told it was detected, not assumed.
        assert note == "claude-opus-5, detected from Claude Code session"

    def test_detection_failure_falls_back_rather_than_raising(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from repowise.core.distill import session_model

        def boom(*_a, **_k):
            raise RuntimeError("unreadable transcript")

        monkeypatch.setattr(session_model, "resolve_session_model", boom)
        model, note = saved_cmd._resolve_pricing(tmp_path, None)
        assert model == saved_cmd.DEFAULT_PRICING_MODEL
        assert "assumed" in note

    def test_the_fallback_matches_the_resolver_s_own_default(self) -> None:
        # Not asserted by running detection against a tmp path: that reads the
        # real ~/.codex, where a rollout carrying no cwd is kept on purpose,
        # so the assertion would pass or fail on whose machine ran it.
        # `test_session_model.py` owns detection, hermetically, with injected
        # roots. What belongs here is that the two defaults agree.
        from repowise.core.distill.session_model import DEFAULT_MODEL

        assert saved_cmd.DEFAULT_PRICING_MODEL == DEFAULT_MODEL

    def test_detected_rate_reaches_the_dollar_line(self, repo_cwd: Path, monkeypatch) -> None:
        from repowise.core.distill import session_model

        detected = session_model.ResolvedModel(
            model="claude-opus-5",
            raw="claude-opus-5",
            agent="claude_code",
            source="detected from Claude Code session",
        )
        monkeypatch.setattr(session_model, "resolve_session_model", lambda *a, **k: detected)
        s = _store(repo_cwd)
        s.record_saving(
            filter_name="test_output",
            source="cli",
            command="pytest",
            raw_tokens=1_000_000,
            distilled_tokens=0,
        )
        s.close()
        result = CliRunner().invoke(saved_command, [])
        assert result.exit_code == 0
        # Opus input is $5.00/M, not the $3.00 this command used to assume.
        assert "$5.0000" in result.output
        assert "claude-opus-5" in result.output


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


def test_missed_tip_offers_install_when_hook_is_absent(monkeypatch) -> None:
    monkeypatch.setattr(saved_cmd, "_rewrite_hook_installed", lambda: False)
    assert "repowise hook rewrite install" in saved_cmd._missed_tip()


def test_missed_tip_does_not_nag_when_hook_is_already_installed(monkeypatch) -> None:
    """The old tip claimed installing would catch these rows even when the
    hook was already installed, which hid the real reason they are there."""
    monkeypatch.setattr(saved_cmd, "_rewrite_hook_installed", lambda: True)
    tip = saved_cmd._missed_tip()
    assert "repowise hook rewrite install" not in tip
    assert "repowise distill" in tip


def test_missed_tip_names_the_opt_out_when_the_repo_declined(monkeypatch, tmp_path: Path) -> None:
    """An opted-out repo has a different reason again, and doctor says so too."""
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "distill:\n  commands:\n    enabled: false\n", encoding="utf-8"
    )
    monkeypatch.setattr(saved_cmd, "_rewrite_hook_installed", lambda: True)
    assert "opted out" in saved_cmd._missed_tip(tmp_path)


def test_rewrite_hook_installed_degrades_to_false(monkeypatch) -> None:
    """A broken or absent adapter must not break `saved --missed`.

    Patches the status call rather than the presence check underneath it:
    that is the entry point now, and stubbing anything shallower would let
    this assertion fall through to the developer's real ~/.claude.
    """
    import repowise.cli.agent_adapters.claude_code as cc

    def boom(self):
        raise RuntimeError("no adapter here")

    monkeypatch.setattr(cc.ClaudeCodeAdapter, "rewrite_hook_status", boom)
    assert saved_cmd._rewrite_hook_installed() is False
