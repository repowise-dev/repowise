"""Prompts in `repowise init` that must degrade rather than end the run.

Agents set repowise up on a user's behalf, usually as `repowise init --yes` or
with stdin pointed at /dev/null. `sys.stdin.isatty()` is not a reliable guard:
on Windows under Git Bash, `init < /dev/null` reports a terminal and then reads
EOF on the first question.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

from repowise.cli import cost_gate
from repowise.cli.commands.init_cmd._interactive import offer_hook_install
from repowise.cli.main import cli


def _est(usd: float) -> SimpleNamespace:
    """A stand-in for a cost estimate, which the gate only reads two fields of."""
    return SimpleNamespace(estimated_cost_usd=usd, cost_range=None, is_calibrated=False)


class TestCostGate:
    def test_below_threshold_never_prompts(self, monkeypatch):
        """A cheap run is not worth interrupting, so the gate stays out of the way."""

        def _boom(*_a, **_k):
            raise AssertionError("prompted below the cost gate threshold")

        monkeypatch.setattr(click, "confirm", _boom)
        assert cost_gate.cost_gate_declined(_est(0.10), yes=False, message="m") is False

    def test_yes_never_prompts(self, monkeypatch):
        """--yes means the spend is pre-approved."""

        def _boom(*_a, **_k):
            raise AssertionError("prompted under --yes")

        monkeypatch.setattr(click, "confirm", _boom)
        assert cost_gate.cost_gate_declined(_est(999.0), yes=True, message="m") is False

    def test_non_tty_declines_without_prompting(self, monkeypatch):
        """No terminal means no answer. Decline, do not Abort.

        The gate is the only prompt that fires *after* a full index, so an
        Abort here throws away every minute already spent. Declining hands the
        caller back to the template renderer with the index intact.
        """
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)

        def _boom(*_a, **_k):
            raise AssertionError("prompted with no terminal attached")

        monkeypatch.setattr(click, "confirm", _boom)
        assert cost_gate.cost_gate_declined(_est(50.0), yes=False, message="m") is True

    def test_default_is_yes_under_the_hard_ceiling(self, monkeypatch):
        """The user just configured this run; Enter-through continues it."""
        seen: dict = {}
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(
            click, "confirm", lambda _m, default=None: seen.setdefault("default", default) or True
        )
        cost_gate.confirm_cost_gate("m", estimated_usd=5.0)
        assert seen["default"] is True

    def test_default_flips_above_the_hard_ceiling(self, monkeypatch):
        """A bill this size must be typed, not defaulted into."""
        seen: dict = {}
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

        def _confirm(_m, default=None):
            seen["default"] = default
            return False

        monkeypatch.setattr(click, "confirm", _confirm)
        cost_gate.confirm_cost_gate("m", estimated_usd=cost_gate.COST_GATE_HARD_USD + 1)
        assert seen["default"] is False


class TestCostGateBlocks:
    """The single cost question shared by init, generate and update --full.

    This is the agent path: a piped run with no --yes must proceed under the
    gate and raise an actionable error over it, never hang.
    """

    def test_under_gate_proceeds(self, monkeypatch):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
        assert cost_gate.cost_gate_blocks(_est(0.10), yes=False, message="m") is False

    def test_yes_proceeds_over_gate(self, monkeypatch):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
        assert cost_gate.cost_gate_blocks(_est(999.0), yes=True, message="m") is False

    def test_non_tty_over_gate_raises_naming_yes(self, monkeypatch):
        """No terminal + over the gate + no --yes is an actionable error, not a hang."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
        with pytest.raises(click.ClickException) as exc:
            cost_gate.cost_gate_blocks(_est(50.0), yes=False, message="m")
        assert "--yes" in str(exc.value)

    def test_tty_over_gate_asks_and_blocks_on_decline(self, monkeypatch):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(click, "confirm", lambda *_a, **_k: False)
        assert cost_gate.cost_gate_blocks(_est(50.0), yes=False, message="m") is True


class TestHookOfferDegrades:
    def test_eof_on_hook_offer_does_not_raise(self, monkeypatch, tmp_path):
        """A finished run must not fail on an optional trailing question."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
        monkeypatch.setattr(
            "repowise.cli.commands.init_cmd._interactive.click.confirm",
            lambda *_a, **_k: (_ for _ in ()).throw(click.Abort()),
        )
        monkeypatch.setattr("repowise.cli.hooks.status", lambda _p: "missing")
        # Must not raise.
        offer_hook_install(
            SimpleNamespace(print=lambda *_a, **_k: None), [tmp_path], None, yes=False
        )


class TestDocsFlag:
    @pytest.fixture
    def runner(self) -> CliRunner:
        return CliRunner()

    def test_docs_llm_conflicts_with_index_only(self, runner, tmp_path):
        """Contradictory flags are a usage error, not a silent winner.

        Exit 2 also keeps this out of the telemetry error bucket, which #907
        split usage errors away from.
        """
        result = runner.invoke(cli, ["init", str(tmp_path), "--docs", "llm", "--index-only"])
        assert result.exit_code == 2
        assert "contradicts" in result.output

    def test_docs_deterministic_needs_no_key(self, runner, tmp_path, monkeypatch):
        """`--docs deterministic` is the scriptable spelling of --index-only."""
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
            "DEEPSEEK_API_KEY",
            "KIMI_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "OLLAMA_BASE_URL",
            "LITELLM_API_KEY",
            "REPOWISE_PROVIDER",
        ):
            monkeypatch.delenv(var, raising=False)
        result = runner.invoke(cli, ["init", str(tmp_path), "--docs", "deterministic", "--yes"])
        assert result.exit_code == 0, result.output

    def test_docs_choice_is_constrained(self, runner, tmp_path):
        """An unknown value is rejected by Click rather than reaching the pipeline."""
        result = runner.invoke(cli, ["init", str(tmp_path), "--docs", "magic"])
        assert result.exit_code == 2


class TestProseFlag:
    @pytest.fixture
    def runner(self) -> CliRunner:
        return CliRunner()

    @pytest.fixture
    def keyless_env(self, monkeypatch):
        for var in (
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
            "DEEPSEEK_API_KEY",
            "KIMI_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "OLLAMA_BASE_URL",
            "LITELLM_API_KEY",
            "REPOWISE_PROVIDER",
        ):
            monkeypatch.delenv(var, raising=False)

    def test_no_prose_needs_no_key(self, runner, tmp_path, keyless_env):
        """`--no-prose` is the current spelling of --index-only: a free wiki."""
        result = runner.invoke(cli, ["init", str(tmp_path), "--no-prose", "--yes"])
        assert result.exit_code == 0, result.output

    def test_index_only_prints_deprecation(self, runner, tmp_path, keyless_env):
        """The deprecated alias still works and says so."""
        result = runner.invoke(cli, ["init", str(tmp_path), "--index-only", "--yes"])
        assert result.exit_code == 0, result.output
        assert "deprecated" in result.output.lower()

    def test_prose_and_no_prose_conflict(self, runner, tmp_path):
        """--prose contradicting the --index-only alias is a usage error."""
        result = runner.invoke(cli, ["init", str(tmp_path), "--prose", "--index-only"])
        assert result.exit_code == 2
        assert "contradicts" in result.output
