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


def _canonical(
    repo: Path,
    *,
    operation: str = "test_output",
    surface: str = "distill",
    agent: str = "unknown",
    baseline: int = 10_000,
    delivered: int = 1_000,
    evidence_kind: str = "measured",
    priced: bool = False,
    occurred_at: str | None = None,
) -> None:
    """Write one canonical savings event, which is what the command reads."""
    from datetime import UTC, datetime

    from repowise.core.savings import recorder
    from repowise.core.savings.correlation import new_event_id, scoped_idempotency_key

    event_id = new_event_id()
    payload: dict = {
        "event_id": event_id,
        "idempotency_key": scoped_idempotency_key(str(repo), surface, event_id),
        "occurred_at": occurred_at
        or datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "surface": surface,
        "integration": agent,
        "agent": agent,
        "operation": operation,
        "evidence_kind": evidence_kind,
        "estimator": "chars_per_token_floor_v1",
        "token_unit": "estimated_tokens",
        "result_state": "success",
        "is_usable": True,
        "baseline_input_tokens": baseline,
        "pre_budget_input_tokens": baseline,
        "delivered_input_tokens": delivered,
    }
    if priced:
        payload.update(
            model="claude-sonnet-4-6",
            currency="USD",
            pricing_source="session_model:unknown",
            pricing_version="pricing:test",
            input_rate_usd_per_million=3.0,
            output_rate_usd_per_million=15.0,
        )
    assert recorder.record_event(repo, payload) is True


def _seed_canonical(repo: Path) -> None:
    """The canonical twin of :func:`_seed`: 13,800 saved input tokens."""
    _store(repo).close()
    _canonical(repo, operation="test_output", baseline=10_000, delivered=1_000)
    _canonical(repo, operation="test_output", surface="hook", baseline=5_000, delivered=500)
    _canonical(repo, operation="git_status", surface="hook", baseline=400, delivered=100)


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


def test_saved_reports_totals_and_per_operation(repo_cwd: Path) -> None:
    _seed_canonical(repo_cwd)
    result = CliRunner().invoke(saved_command, [])
    assert result.exit_code == 0
    assert "test_output" in result.output
    assert "git_status" in result.output
    assert "TOTAL" in result.output
    assert "13,800" in result.output  # total saved input tokens
    # The scope caption must be visible in the report itself (normalize
    # whitespace — rich wraps the caption at terminal width).
    flat = " ".join(result.output.split())
    assert "the hooks that replace a tool result, and MCP calls" in flat


def test_saved_splits_savings_by_surface(repo_cwd: Path) -> None:
    """The grouping that replaced ``--by source``."""
    _seed_canonical(repo_cwd)
    result = CliRunner().invoke(saved_command, ["--by", "surface"])
    assert result.exit_code == 0
    assert "distill" in result.output
    assert "hook" in result.output


def test_saved_labels_agents_from_the_registry(repo_cwd: Path) -> None:
    """No label map in this command; the report carries the display name."""
    _store(repo_cwd).close()
    _canonical(repo_cwd, agent="claude_code", baseline=1_000, delivered=0)
    result = CliRunner().invoke(saved_command, ["--by", "agent"])
    assert result.exit_code == 0
    assert "Claude Code" in result.output


def test_saved_keeps_measured_and_inferred_apart(repo_cwd: Path) -> None:
    """The total must not read as one confident number."""
    _store(repo_cwd).close()
    _canonical(repo_cwd, baseline=1_000, delivered=0, evidence_kind="measured")
    _canonical(repo_cwd, baseline=400, delivered=0, evidence_kind="inferred")
    result = CliRunner().invoke(saved_command, [])
    assert result.exit_code == 0
    flat = " ".join(result.output.split())
    assert "Measured: 1,000" in flat
    assert "Inferred: 400" in flat


def test_saved_by_day(repo_cwd: Path) -> None:
    _seed_canonical(repo_cwd)
    result = CliRunner().invoke(saved_command, ["--by", "day"])
    assert result.exit_code == 0
    assert "Events" in result.output


def test_saved_dollar_estimate_uses_the_rate_recorded_on_the_event(repo_cwd: Path) -> None:
    """Priced from the event, not from whatever model is detected today."""
    _store(repo_cwd).close()
    _canonical(repo_cwd, baseline=1_000_000, delivered=0, priced=True)
    # The event carries $3.00/M input -> exactly $3.00 for 1M saved.
    result = CliRunner().invoke(saved_command, [])
    assert result.exit_code == 0
    assert "$3.0000" in result.output


def test_saved_reports_unpriced_tokens_rather_than_valuing_them(repo_cwd: Path) -> None:
    """An event written without a rate is counted but not valued."""
    _store(repo_cwd).close()
    _canonical(repo_cwd, baseline=1_000_000, delivered=0, priced=False)
    result = CliRunner().invoke(saved_command, [])
    assert result.exit_code == 0
    flat = " ".join(result.output.split())
    assert "1,000,000 tokens carry no recorded rate" in flat
    assert "Estimated saved" not in flat


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

    def test_the_rate_on_the_event_is_what_reaches_the_dollar_line(
        self, repo_cwd: Path, monkeypatch
    ) -> None:
        """Detection no longer prices the report; the event does.

        An event priced at write time keeps its rate even when the newest
        session reports a different model, which is the whole point of
        snapshotting. Detection is monkeypatched to a *different* model here
        precisely so a report that still repriced from the session would fail.
        """
        from repowise.core.distill import session_model

        detected = session_model.ResolvedModel(
            model="claude-opus-5",
            raw="claude-opus-5",
            agent="claude_code",
            source="detected from Claude Code session",
        )
        monkeypatch.setattr(session_model, "resolve_session_model", lambda *a, **k: detected)
        _store(repo_cwd).close()
        _canonical(repo_cwd, baseline=1_000_000, delivered=0, priced=True)
        result = CliRunner().invoke(saved_command, [])
        assert result.exit_code == 0
        # The event's own $3.00/M rate, not Opus's $5.00 repriced from the session.
        assert "$3.0000" in result.output
        assert "$5.0000" not in result.output


def test_saved_no_store_prints_hint(repo_cwd: Path) -> None:
    result = CliRunner().invoke(saved_command, [])
    assert result.exit_code == 0
    assert "No savings recorded yet" in result.output
    assert "repowise distill" in result.output


def test_saved_empty_ledger_prints_message(repo_cwd: Path) -> None:
    _store(repo_cwd).close()  # creates the DB with zero ledger rows
    result = CliRunner().invoke(saved_command, [])
    assert result.exit_code == 0
    assert "No savings events recorded" in result.output


def test_saved_since_filters_events(repo_cwd: Path) -> None:
    _store(repo_cwd).close()
    _canonical(repo_cwd, operation="test_output", baseline=10_000, delivered=1_000)
    _canonical(
        repo_cwd,
        operation="git_status",
        baseline=400,
        delivered=100,
        occurred_at="2020-01-01T00:00:00.000000Z",
    )
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
    _seed_canonical(repo)
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
