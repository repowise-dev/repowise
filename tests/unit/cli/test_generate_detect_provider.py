"""A ``generate`` with no provider set uses a key it finds, else a signed-in agent CLI
billed to a plan quota, and says which before the cost question."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from repowise.cli.commands.generate_cmd import command as generate_command
from repowise.cli.helpers import save_state
from repowise.core.providers.llm import resolve, subscription

CLAUDE_TEAM = subscription.CliAuth("Claude team plan: uses your plan quota", plan_quota=True)


@pytest.fixture
def out(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    buf = io.StringIO()
    monkeypatch.setattr(generate_command, "console", Console(file=buf, width=200))
    return buf


@pytest.fixture
def keyless(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A repo with no flag, config, environment or stored key naming a provider."""
    monkeypatch.setattr(resolve, "resolve_llm", lambda *_a, **_k: resolve.LLMSelection())
    monkeypatch.setattr(resolve, "credentialed_providers", lambda _repo: ())
    (tmp_path / ".repowise").mkdir()
    return tmp_path


def _signed_in(monkeypatch: pytest.MonkeyPatch, auths: dict) -> None:
    monkeypatch.setattr(subscription, "agent_cli_auth", lambda name: auths.get(name))


def test_a_plan_billed_cli_is_used_and_named(keyless, out, monkeypatch) -> None:
    _signed_in(monkeypatch, {"claude_cli": CLAUDE_TEAM})
    assert generate_command._signed_in_cli(keyless) == "claude_cli"
    text = out.getvalue()
    assert "No provider configured, so this run uses your Claude Code sign-in and plan quota" in text
    assert "--provider" in text


@pytest.mark.parametrize(
    "auth",
    [
        subscription.CliAuth("API key: billed per token"),
        subscription.CliAuth("runs through bedrock: billed to that cloud account"),
        subscription.CliAuth("signed in without a paid Claude plan", offer=False),
    ],
)
def test_a_metered_login_is_never_picked_unasked(keyless, out, monkeypatch, auth) -> None:
    _signed_in(monkeypatch, {"claude_cli": auth, "codex_cli": subscription.CliAuth("API key")})
    assert generate_command._signed_in_cli(keyless) is None
    assert out.getvalue() == ""


def test_nothing_signed_in_leaves_the_no_provider_path(keyless, out, monkeypatch) -> None:
    _signed_in(monkeypatch, {})
    assert generate_command._signed_in_cli(keyless) is None
    assert out.getvalue() == ""


@pytest.mark.parametrize("source", ["autodetect", "config.yaml"])
def test_a_resolving_provider_keeps_winning(tmp_path, out, monkeypatch, source) -> None:
    """A configured provider or a key in the environment is never swapped for a CLI."""
    monkeypatch.setattr(
        resolve,
        "resolve_llm",
        lambda *_a, **_k: resolve.LLMSelection(provider="anthropic", provider_source=source),
    )
    monkeypatch.setattr(subscription, "agent_cli_auth", lambda _n: pytest.fail("probed a CLI"))
    assert generate_command._signed_in_cli(tmp_path) is None


def test_a_key_found_by_detection_is_named_with_its_source(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        resolve,
        "resolve_llm",
        lambda *_a, **_k: resolve.LLMSelection(provider="anthropic", provider_source="autodetect"),
    )
    monkeypatch.setattr(
        resolve, "resolve_api_key", lambda *_a, **_k: ("k", "env:ANTHROPIC_API_KEY")
    )
    assert generate_command._key_source(tmp_path, "anthropic") == (
        " (key from env:ANTHROPIC_API_KEY)"
    )


def test_a_configured_provider_needs_no_source(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        resolve,
        "resolve_llm",
        lambda *_a, **_k: resolve.LLMSelection(provider="anthropic", provider_source="config.yaml"),
    )
    assert generate_command._key_source(tmp_path, "anthropic") == ""


def test_dry_run_prices_the_cli_it_would_use(keyless, monkeypatch) -> None:
    """One command, no provider configured: the dry run names the CLI it would use."""
    save_state(keyless, {"docs_mode": "deterministic"})
    _signed_in(monkeypatch, {"claude_cli": CLAUDE_TEAM})
    seen: dict[str, object] = {}

    async def _has_docs(_repo):
        return True

    def _dry_provider(name, *_a, **_k):
        seen["provider"] = name
        return None

    async def _run(*_a, **_k):
        return None

    monkeypatch.setattr(generate_command, "has_documentation", _has_docs)
    monkeypatch.setattr(generate_command, "_dry_run_provider", _dry_provider)
    monkeypatch.setattr(generate_command, "run_scoped_generation", _run)
    monkeypatch.setattr(
        generate_command, "model_run_embedder", lambda *_a, **_k: (None, "mock", False)
    )
    result = CliRunner().invoke(generate_command.generate_command, [str(keyless), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert seen["provider"] == "claude_cli"
