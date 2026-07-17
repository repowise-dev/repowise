"""get_answer provider resolution: intent precedence and keyless fall-through.

Regression coverage for the silent retrieval-only degradation: an index
whose state.json recorded a provider from a months-old build, in an env
that only carried a different provider's key, resolved to None and ran
get_answer without synthesis — no error, no fallback, even though a usable
key was sitting in the environment.
"""

from __future__ import annotations

import json as _json

import pytest

from repowise.server.mcp_server.tool_answer.synthesis import (
    _load_repo_provider_config,
    _resolve_provider_for_answer,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("REPOWISE_PROVIDER", "REPOWISE_DOC_MODEL", "REPOWISE_MODEL",
                "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                "GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "KIMI_API_KEY",
                "OLLAMA_BASE_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


def _write_repo(tmp_path, *, config=None, state=None):
    d = tmp_path / ".repowise"
    d.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (d / "config.yaml").write_text(
            "\n".join(f"{k}: {v}" for k, v in config.items()), encoding="utf-8")
    if state is not None:
        (d / "state.json").write_text(_json.dumps(state), encoding="utf-8")
    return tmp_path


def test_config_yaml_outranks_state_json(tmp_path):
    """config.yaml is user intent; state.json only records the last run."""
    repo = _write_repo(
        tmp_path,
        config={"provider": "openai", "model": "gpt-5.4-nano"},
        state={"provider": "gemini", "model": "gemini-3.1-flash-lite-preview"},
    )
    name, model, _ = _load_repo_provider_config(repo)
    assert name == "openai"
    assert model == "gpt-5.4-nano"


def test_state_json_still_used_when_config_lacks_provider(tmp_path):
    repo = _write_repo(tmp_path, config={"commit_limit": 200},
                       state={"provider": "openai", "model": "gpt-5.4-nano"})
    name, model, _ = _load_repo_provider_config(repo)
    assert name == "openai"
    assert model == "gpt-5.4-nano"


def test_keyless_persisted_provider_falls_back_to_available_key(tmp_path, monkeypatch):
    """gemini persisted, only OPENAI_API_KEY present -> openai provider, not None."""
    repo = _write_repo(tmp_path,
                       state={"provider": "gemini", "model": "gemini-3.1-flash"})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    provider = _resolve_provider_for_answer(repo)
    assert provider is not None
    assert getattr(provider, "provider_name", "") == "openai"
    # The persisted gemini model must not leak into the openai call.
    assert "gemini" not in (getattr(provider, "model_name", "") or "")


def test_no_keys_at_all_resolves_to_none(tmp_path):
    repo = _write_repo(tmp_path, state={"provider": "gemini"})
    assert _resolve_provider_for_answer(repo) is None


def test_explicit_env_provider_still_wins(tmp_path, monkeypatch):
    repo = _write_repo(tmp_path, state={"provider": "gemini"})
    monkeypatch.setenv("REPOWISE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    provider = _resolve_provider_for_answer(repo)
    assert provider is not None
    assert getattr(provider, "provider_name", "") == "openai"
