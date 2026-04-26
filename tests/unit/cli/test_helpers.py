"""Unit tests for repowise.cli.helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from repowise.cli.helpers import (
    ensure_repowise_dir,
    CONFIG_FILENAME,
    get_db_url_for_repo,
    get_head_commit,
    get_repowise_dir,
    load_state,
    resolve_provider,
    resolve_repo_path,
    run_async,
    save_state,
    validate_provider_config,
)

# ---------------------------------------------------------------------------
# run_async
# ---------------------------------------------------------------------------


class TestRunAsync:
    def test_returns_coroutine_result(self):
        async def _add(a, b):
            return a + b

        assert run_async(_add(3, 4)) == 7

    def test_raises_exception_from_coroutine(self):
        async def _fail():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            run_async(_fail())


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


class TestResolveRepoPath:
    def test_none_defaults_to_cwd(self):
        result = resolve_repo_path(None)
        assert result == Path.cwd().resolve()

    def test_resolves_relative_path(self, tmp_path):
        import os

        old = os.getcwd()
        try:
            os.chdir(tmp_path)
            result = resolve_repo_path(".")
            assert result == tmp_path.resolve()
        finally:
            os.chdir(old)

    def test_resolves_absolute_path(self, tmp_path):
        result = resolve_repo_path(str(tmp_path))
        assert result == tmp_path.resolve()


# ---------------------------------------------------------------------------
# .repowise/ directory
# ---------------------------------------------------------------------------


class TestrepowiseDir:
    def test_get_repowise_dir(self, tmp_path):
        assert get_repowise_dir(tmp_path) == tmp_path / ".repowise"

    def test_ensure_repowise_dir_creates(self, tmp_path):
        d = ensure_repowise_dir(tmp_path)
        assert d.exists()
        assert d == tmp_path / ".repowise"

    def test_ensure_repowise_dir_idempotent(self, tmp_path):
        ensure_repowise_dir(tmp_path)
        d = ensure_repowise_dir(tmp_path)
        assert d.exists()


# ---------------------------------------------------------------------------
# DB URL
# ---------------------------------------------------------------------------


class TestDbUrl:
    def test_defaults_to_repo_local_database(self, tmp_path):
        url = get_db_url_for_repo(tmp_path)
        expected_path = (tmp_path / ".repowise" / "wiki.db").as_posix()
        assert url == f"sqlite+aiosqlite:///{expected_path}"
        assert (tmp_path / ".repowise").exists()


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


class TestStateFile:
    def test_load_missing_returns_empty(self, tmp_path):
        ensure_repowise_dir(tmp_path)
        assert load_state(tmp_path) == {}

    def test_save_and_load_roundtrip(self, tmp_path):
        ensure_repowise_dir(tmp_path)
        state = {"last_sync_commit": "abc123", "total_pages": 42}
        save_state(tmp_path, state)
        loaded = load_state(tmp_path)
        assert loaded == state

    def test_save_creates_repowise_dir(self, tmp_path):
        save_state(tmp_path, {"key": "value"})
        assert (tmp_path / ".repowise" / "state.json").exists()


# ---------------------------------------------------------------------------
# Git HEAD
# ---------------------------------------------------------------------------


class TestGetHeadCommit:
    def test_non_git_returns_none(self, tmp_path):
        assert get_head_commit(tmp_path) is None


# ---------------------------------------------------------------------------
# Provider validation
# ---------------------------------------------------------------------------


class TestValidateProviderConfig:
    def test_no_provider_returns_empty_warnings(self, monkeypatch):
        # Clear all provider env vars
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("REPOWISE_PROVIDER", raising=False)

        assert validate_provider_config() == []

    def test_anthropic_missing_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("REPOWISE_PROVIDER", "anthropic")

        warnings = validate_provider_config()
        assert len(warnings) == 1
        assert "anthropic" in warnings[0]
        assert "ANTHROPIC_API_KEY" in warnings[0]

    def test_anthropic_valid_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("REPOWISE_PROVIDER", "anthropic")

        assert validate_provider_config() == []

    def test_anthropic_empty_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.setenv("REPOWISE_PROVIDER", "anthropic")

        warnings = validate_provider_config()
        assert len(warnings) == 1
        assert "ANTHROPIC_API_KEY" in warnings[0]

    def test_openai_missing_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("REPOWISE_PROVIDER", "openai")

        warnings = validate_provider_config()
        assert len(warnings) == 1
        assert "openai" in warnings[0]
        assert "OPENAI_API_KEY" in warnings[0]

    def test_gemini_with_gemini_key(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("REPOWISE_PROVIDER", "gemini")

        assert validate_provider_config() == []

    def test_gemini_with_google_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        monkeypatch.setenv("REPOWISE_PROVIDER", "gemini")

        assert validate_provider_config() == []

    def test_gemini_missing_keys(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("REPOWISE_PROVIDER", "gemini")

        warnings = validate_provider_config()
        assert len(warnings) == 1
        assert "gemini" in warnings[0]

    def test_ollama_missing_url(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        monkeypatch.setenv("REPOWISE_PROVIDER", "ollama")

        warnings = validate_provider_config()
        assert len(warnings) == 1
        assert "ollama" in warnings[0]
        assert "OLLAMA_BASE_URL" in warnings[0]

    def test_unknown_provider(self, monkeypatch):
        warnings = validate_provider_config("unknown")
        assert len(warnings) == 1
        assert "unknown provider" in warnings[0].lower()

    def test_auto_detect_anthropic(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

        # Should not warn when env var is properly set
        assert validate_provider_config() == []

    def test_anthropic_empty_key_auto_detect(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

        # Should warn when env var exists but is empty
        warnings = validate_provider_config()
        assert len(warnings) == 1
        assert "anthropic" in warnings[0]
        assert "ANTHROPIC_API_KEY" in warnings[0]


# ---------------------------------------------------------------------------
# Provider base_url resolution
# ---------------------------------------------------------------------------


class TestResolveProviderBaseUrl:
    @staticmethod
    def test_env_base_url_forwarded(monkeypatch, tmp_path):
        captured: dict[str, Any] = {}

        def fake_get_provider(name: str, **kwargs: Any):
            captured["name"] = name
            captured["kwargs"] = kwargs
            return "provider"

        monkeypatch.setattr("repowise.core.providers.get_provider", fake_get_provider)
        monkeypatch.setattr("repowise.cli.helpers.validate_provider_config", lambda *_args, **_kw: [])
        monkeypatch.setenv("REPOWISE_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_BASE_URL", "http://proxy.local")

        result = resolve_provider(None, None, repo_path=tmp_path)

        assert result == "provider"
        assert captured["name"] == "openai"
        assert captured["kwargs"].get("base_url") == "http://proxy.local"

    @staticmethod
    def test_config_base_url_used_when_env_missing(monkeypatch, tmp_path):
        captured: dict[str, Any] = {}

        def fake_get_provider(name: str, **kwargs: Any):
            captured["name"] = name
            captured["kwargs"] = kwargs
            return "provider"

        monkeypatch.setattr("repowise.core.providers.get_provider", fake_get_provider)
        monkeypatch.setattr("repowise.cli.helpers.validate_provider_config", lambda *_args, **_kw: [])
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        cfg = {
            "provider": "ollama",
            "model": "llama3",
            "ollama": {"base_url": "http://ollama.local:11434"},
        }
        repowise_dir = ensure_repowise_dir(tmp_path)
        config_path = repowise_dir / CONFIG_FILENAME

        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            yaml = None

        if "yaml" in locals() and yaml is not None:
            config_path.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False), encoding="utf-8")
        else:
            config_path.write_text(
                "provider: ollama\nmodel: llama3\nollama:\n  base_url: http://ollama.local:11434\n",
                encoding="utf-8",
            )

        result = resolve_provider(None, None, repo_path=tmp_path)

        assert result == "provider"
        assert captured["name"] == "ollama"
        assert captured["kwargs"].get("base_url") == "http://ollama.local:11434"
