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
from repowise.cli.helpers import NO_SAVE_KEY_ENV, save_config
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


class TestNonInteractiveKeyPersistence:
    """A scripted init that succeeds must leave an MCP-answerable repo.

    Persistence used to hang off the interactive key *prompt*, so it only ran
    when a human typed a key. Supplying the key through the environment is
    exactly the no-prompt case, which meant `init --provider openai --yes`
    indexed fine, wrote `provider: openai`, and left no credential — and
    `get_answer` against that repo then returned
    `degraded: "no-llm-provider"` with retrieval-only output.

    These pin `save_config`, the one place all three flows (single-repo init,
    workspace init, `workspace add`) commit to a provider for a repo.
    """

    def test_env_supplied_key_is_persisted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
        save_config(tmp_path, "openai", "gpt-5.4-nano", "openai")

        env_file = tmp_path / ".repowise" / ".env"
        assert env_file.exists(), "a key that indexed the repo was not saved"
        assert "OPENAI_API_KEY=sk-from-the-environment" in env_file.read_text(encoding="utf-8")

    def test_persisting_a_key_gitignores_it(self, tmp_path, monkeypatch):
        """The secret and its ignore rule land together or not at all."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
        save_config(tmp_path, "openai", "gpt-5.4-nano", "openai")

        ignored = (tmp_path / ".gitignore").read_text(encoding="utf-8")
        assert ".repowise/.env" in {line.strip() for line in ignored.splitlines()}

    def test_no_save_key_leaves_nothing_behind(self, tmp_path, monkeypatch):
        """The opt-out is for keys injected per-process that must not hit disk."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
        save_config(tmp_path, "openai", "gpt-5.4-nano", "openai", save_key=False)

        assert not (tmp_path / ".repowise" / ".env").exists()

    def test_keyless_provider_writes_no_env_file(self, tmp_path, monkeypatch):
        """Nothing to persist must mean no file, not an empty one."""
        for var in ("OPENAI_API_KEY", "OLLAMA_BASE_URL"):
            monkeypatch.delenv(var, raising=False)
        save_config(tmp_path, "codex_cli", "codex_cli/default", "mock")

        assert not (tmp_path / ".repowise" / ".env").exists()

    def test_gemini_persists_only_the_variable_actually_set(self, tmp_path, monkeypatch):
        """Gemini accepts either var; saving both would invent a credential."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "goog-key")
        save_config(tmp_path, "gemini", "gemini-3.5-flash-lite", "gemini")

        written = (tmp_path / ".repowise" / ".env").read_text(encoding="utf-8")
        assert "GOOGLE_API_KEY=goog-key" in written
        assert "GEMINI_API_KEY" not in written

    def test_declined_interactive_key_is_not_persisted_later(self, tmp_path, monkeypatch):
        """A "no" at the key prompt outranks the save-by-default rule.

        The prompt puts the key in the environment so the run can index with
        it, which is exactly what `save_config` mirrors to disk at the end. The
        decline has to survive that gap or the user is asked and then overruled.
        """
        monkeypatch.setenv("OPENAI_API_KEY", "sk-declined")
        monkeypatch.setenv(NO_SAVE_KEY_ENV, "1")
        save_config(tmp_path, "openai", "gpt-5.4-nano", "openai")

        assert not (tmp_path / ".repowise" / ".env").exists()

    def test_ollama_endpoint_is_not_persisted_as_a_credential(self, tmp_path, monkeypatch):
        """Pinning an endpoint into a repo is a different decision from saving a key."""
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
        save_config(tmp_path, "ollama", "qwen3.5:4b", "ollama")

        assert not (tmp_path / ".repowise" / ".env").exists()

    def test_unwritable_gitignore_warns_but_does_not_lose_the_index(
        self, tmp_path, monkeypatch, capsys
    ):
        """This runs after the index is paid for, so it must never abort the run.

        It is also the first thing on the scripted path to write outside
        `.repowise/`, so it is the first that can fail on a read-only checkout.
        """
        monkeypatch.setenv("OPENAI_API_KEY", "sk-key")
        (tmp_path / ".gitignore").mkdir()  # a directory where a file must go

        save_config(tmp_path, "openai", "gpt-5.4-nano", "openai")

        assert (tmp_path / ".repowise" / "config.yaml").exists(), "the run's config was lost"
        # And the key never reached disk without its ignore rule.
        assert not (tmp_path / ".repowise" / ".env").exists()

    def test_key_with_a_trailing_newline_is_stored_clean(self, tmp_path, monkeypatch):
        """Secret stores and CI env-file loaders routinely keep the newline."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-newline\n")
        save_config(tmp_path, "openai", "gpt-5.4-nano", "openai")

        written = (tmp_path / ".repowise" / ".env").read_text(encoding="utf-8")
        assert "OPENAI_API_KEY=sk-newline\n" in written
