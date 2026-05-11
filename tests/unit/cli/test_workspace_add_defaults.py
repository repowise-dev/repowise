"""Tests for the new ``repowise workspace add`` defaults.

The most important behavior shift in Phase A: ``workspace add`` now runs
index + docs by default, and surfaces an honest "no docs because ..."
notice when generation is skipped. These tests pin the decision matrix
in :func:`_resolve_docs_flag` so the auto-on/auto-off contract doesn't
silently regress.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.commands.workspace_cmd import _resolve_docs_flag
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig


@pytest.fixture
def ws(tmp_path: Path) -> tuple[Path, WorkspaceConfig]:
    backend = tmp_path / "backend"
    (backend / ".git").mkdir(parents=True)
    (backend / ".repowise").mkdir(parents=True)
    cfg = WorkspaceConfig(
        version=1,
        repos=[RepoEntry(path="backend", alias="backend", is_primary=True)],
        default_repo="backend",
    )
    cfg.save(tmp_path)
    return tmp_path, cfg


@pytest.fixture(autouse=True)
def _clear_provider_env(monkeypatch):
    """Strip every provider key so tests start from a clean slate."""
    for key in (
        "REPOWISE_PROVIDER",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
        "OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_explicit_yes_wins(ws):
    ws_root, cfg = ws
    docs, reason = _resolve_docs_flag(
        run_docs=True, provider_name=None, ws_root=ws_root, ws_config=cfg,
    )
    assert docs is True
    assert reason is None


def test_explicit_no_records_reason(ws):
    ws_root, cfg = ws
    docs, reason = _resolve_docs_flag(
        run_docs=False, provider_name=None, ws_root=ws_root, ws_config=cfg,
    )
    assert docs is False
    assert reason == "--no-docs flag"


def test_provider_flag_forces_on(ws):
    ws_root, cfg = ws
    docs, reason = _resolve_docs_flag(
        run_docs=None,
        provider_name="anthropic",
        ws_root=ws_root,
        ws_config=cfg,
    )
    assert docs is True
    assert reason is None


def test_inherits_primary_config(ws, monkeypatch):
    ws_root, cfg = ws
    # Drop a provider into the primary repo's config — that should be
    # enough to flip the default to docs-on.
    cfg_path = ws_root / "backend" / ".repowise" / "config.yaml"
    cfg_path.write_text(
        "provider: anthropic\nmodel: claude-sonnet-4-5\nembedder: gemini\n",
        encoding="utf-8",
    )
    docs, reason = _resolve_docs_flag(
        run_docs=None, provider_name=None, ws_root=ws_root, ws_config=cfg,
    )
    assert docs is True
    assert reason is None


def test_env_provider_forces_on(ws, monkeypatch):
    ws_root, cfg = ws
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    docs, reason = _resolve_docs_flag(
        run_docs=None, provider_name=None, ws_root=ws_root, ws_config=cfg,
    )
    assert docs is True


def test_no_provider_anywhere_returns_off(ws):
    ws_root, cfg = ws
    docs, reason = _resolve_docs_flag(
        run_docs=None, provider_name=None, ws_root=ws_root, ws_config=cfg,
    )
    assert docs is False
    assert reason == "no provider configured"
