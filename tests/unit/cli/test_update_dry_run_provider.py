"""The full-upgrade dry run names the provider the run would resolve (#5709)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from repowise.cli.commands.update_cmd import command as update_command
from repowise.core.providers.llm import resolve


@pytest.fixture(autouse=True)
def _no_env_default(monkeypatch):
    monkeypatch.delenv("REPOWISE_PROVIDER", raising=False)
    monkeypatch.delenv("REPOWISE_MODEL", raising=False)
    resolve.clear_cache()


def _set_default(provider: str, model: str) -> None:
    resolve.update_global_config(
        lambda config: config.update(active_provider=provider, active_model=model)
    )


def _indexed_repo(tmp_path: Path, config: str = "") -> Path:
    """A repo whose last index ran on openrouter, per its state.json."""
    rw = tmp_path / ".repowise"
    rw.mkdir()
    (rw / "state.json").write_text(
        json.dumps({"provider": "openrouter", "model": "or-model"}), encoding="utf-8"
    )
    if config:
        (rw / "config.yaml").write_text(config, encoding="utf-8")
    return tmp_path


def _dry_run(monkeypatch, repo: Path, **flags) -> str:
    out = io.StringIO()
    monkeypatch.setattr(update_command, "console", Console(file=out, width=200, color_system=None))
    update_command._render_full_upgrade_dry_run(
        repo, provider_name=flags.get("provider"), model=flags.get("model")
    )
    return out.getvalue()


def test_a_repo_naming_no_provider_shows_the_current_default(tmp_path, monkeypatch):
    """Not the provider its last index ran on, which the default has since replaced."""
    repo = _indexed_repo(tmp_path)
    _set_default("ollama", "qwen")

    assert "Provider: ollama / qwen" in _dry_run(monkeypatch, repo)


def test_the_repo_own_provider_is_shown_over_the_default(tmp_path, monkeypatch):
    repo = _indexed_repo(tmp_path, "provider: gemini\nmodel: g-1\n")
    _set_default("ollama", "qwen")

    assert "Provider: gemini / g-1" in _dry_run(monkeypatch, repo)


def test_flags_are_shown_over_everything(tmp_path, monkeypatch):
    repo = _indexed_repo(tmp_path, "provider: gemini\nmodel: g-1\n")
    _set_default("ollama", "qwen")

    text = _dry_run(monkeypatch, repo, provider="openai", model="gpt-x")

    assert "Provider: openai / gpt-x" in text


def test_nothing_configured_shows_auto_detect(tmp_path, monkeypatch):
    repo = _indexed_repo(tmp_path)

    assert "Provider: auto-detect / provider default" in _dry_run(monkeypatch, repo)
