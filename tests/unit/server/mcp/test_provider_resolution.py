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

from repowise.server.mcp_server.tool_answer import synthesis as synthesis_module
from repowise.server.mcp_server.tool_answer.synthesis import (
    _load_repo_provider_config,
    _resolve_provider_for_answer,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "REPOWISE_PROVIDER",
        "REPOWISE_DOC_MODEL",
        "REPOWISE_MODEL",
        "REPOWISE_REASONING",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "DEEPSEEK_API_KEY",
        "KIMI_API_KEY",
        "OLLAMA_BASE_URL",
        "OPENAI_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def _write_repo(tmp_path, *, config=None, state=None):
    d = tmp_path / ".repowise"
    d.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (d / "config.yaml").write_text(
            "\n".join(f"{k}: {v}" for k, v in config.items()), encoding="utf-8"
        )
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
    repo = _write_repo(
        tmp_path,
        config={"commit_limit": 200},
        state={"provider": "openai", "model": "gpt-5.4-nano"},
    )
    name, model, _ = _load_repo_provider_config(repo)
    assert name == "openai"
    assert model == "gpt-5.4-nano"


def test_reasoning_is_loaded_from_repo_config(tmp_path):
    repo = _write_repo(tmp_path, config={"reasoning": "low"})

    assert synthesis_module._resolve_reasoning_for_answer(repo) == "low"


def test_keyless_persisted_provider_falls_back_to_available_key(tmp_path, monkeypatch):
    """gemini persisted, only OPENAI_API_KEY present -> openai provider, not None."""
    repo = _write_repo(tmp_path, state={"provider": "gemini", "model": "gemini-3.1-flash"})
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


def _record_get_provider(monkeypatch):
    """Capture the kwargs resolution hands to the registry."""
    from repowise.core.providers.llm import registry

    seen: dict = {}

    def _fake(name, **kwargs):
        seen["name"] = name
        seen["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(registry, "get_provider", _fake)
    return seen


def test_agent_cli_provider_is_told_which_repo_to_run_in(tmp_path, monkeypatch):
    """#1119: codex exec inherited the MCP server's cwd, not the user's repo.

    For a server launched by an editor that is some arbitrary directory, so the
    CLI reasoned about the wrong tree (when it ran at all).
    """
    repo = _write_repo(tmp_path, config={"provider": "codex_cli"})
    seen = _record_get_provider(monkeypatch)

    assert _resolve_provider_for_answer(repo) is not None
    assert seen["name"] == "codex_cli"
    assert seen["kwargs"]["repo_path"] == repo


def test_keyless_provider_resolves_without_any_api_key(tmp_path, monkeypatch):
    """codex_cli authenticates out of band; a missing key is not a reason to skip it."""
    repo = _write_repo(tmp_path, config={"provider": "opencode"})
    seen = _record_get_provider(monkeypatch)

    assert _resolve_provider_for_answer(repo) is not None
    assert seen["name"] == "opencode"


def test_repo_path_is_not_forwarded_to_an_http_provider(tmp_path, monkeypatch):
    """AnthropicProvider has no repo_path parameter; passing it would TypeError."""
    repo = _write_repo(tmp_path, config={"provider": "anthropic"})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    seen = _record_get_provider(monkeypatch)

    assert _resolve_provider_for_answer(repo) is not None
    assert "repo_path" not in seen["kwargs"]


def test_env_file_key_is_used_when_the_process_env_has_none(tmp_path, monkeypatch):
    """The MCP server is not launched from the shell that ran `repowise init`."""
    repo = _write_repo(tmp_path, config={"provider": "openai"})
    (repo / ".repowise" / ".env").write_text('OPENAI_API_KEY="sk-from-dotenv"', encoding="utf-8")
    seen = _record_get_provider(monkeypatch)

    assert _resolve_provider_for_answer(repo) is not None
    assert seen["kwargs"]["api_key"] == "sk-from-dotenv"


def test_process_env_key_outranks_the_env_file(tmp_path, monkeypatch):
    repo = _write_repo(tmp_path, config={"provider": "openai"})
    (repo / ".repowise" / ".env").write_text("OPENAI_API_KEY=sk-from-dotenv", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-shell")
    seen = _record_get_provider(monkeypatch)

    assert _resolve_provider_for_answer(repo) is not None
    assert seen["kwargs"]["api_key"] == "sk-from-shell"


def test_autodetect_does_not_pick_a_keyless_provider_out_of_thin_air(tmp_path):
    """No config, no keys: a keyless provider must not be silently assumed."""
    repo = _write_repo(tmp_path)
    assert _resolve_provider_for_answer(repo) is None


def test_openrouter_key_alone_now_resolves(tmp_path, monkeypatch):
    """Auto-detection skipped openrouter entirely, so those users got no synthesis."""
    repo = _write_repo(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    seen = _record_get_provider(monkeypatch)

    assert _resolve_provider_for_answer(repo) is not None
    assert seen["name"] == "openrouter"
