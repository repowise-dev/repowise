"""The shared embedder resolver, and that the CLI, HTTP and MCP servers use it."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.providers.embedding import resolve
from repowise.core.providers.llm.specs import PROVIDER_SPECS

_VARS = (
    "REPOWISE_EMBEDDER",
    "REPOWISE_EMBEDDING_MODEL",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "EDENAI_API_KEY",
    "OLLAMA_EMBEDDING_MODEL",
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))


def _repo(tmp_path: Path, config: str = "", env: str = "") -> Path:
    rw = tmp_path / "repo" / ".repowise"
    rw.mkdir(parents=True, exist_ok=True)
    if config:
        (rw / "config.yaml").write_text(config, encoding="utf-8")
    if env:
        (rw / ".env").write_text(env, encoding="utf-8")
    return rw.parent


def test_key_env_map_comes_from_the_provider_specs():
    expected = {
        name: PROVIDER_SPECS[name].api_key_envs
        for name in ("openai", "gemini", "openrouter", "edenai")
    }
    assert dict(resolve.EMBEDDER_KEY_ENVS) == expected


def test_precedence_flag_env_pin_detection_default(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "embedder: gemini\n")
    monkeypatch.setenv("OPENAI_API_KEY", "sk")

    assert resolve.resolve_embedder_name("ollama", repo_path=repo) == ("ollama", "flag")
    monkeypatch.setenv("REPOWISE_EMBEDDER", "OpenRouter")
    assert resolve.resolve_embedder_name(repo_path=repo) == ("openrouter", "env:REPOWISE_EMBEDDER")
    monkeypatch.delenv("REPOWISE_EMBEDDER")
    assert resolve.resolve_embedder_name(repo_path=repo) == ("gemini", "config.yaml")
    assert resolve.resolve_embedder_name(repo_path=repo, use_pin=False) == ("openai", "autodetect")
    assert resolve.resolve_embedder_name(detect=False) == ("mock", "default")


def test_key_chain_env_repo_env_global(tmp_path, monkeypatch):
    repo = _repo(tmp_path, env="OPENAI_API_KEY=from-file\n")
    home = tmp_path / "home" / ".repowise"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        "embedder: openai\nembedder_api_key: from-global\n", encoding="utf-8"
    )

    assert resolve.resolve_embedder_key("openai", repo).key == "from-file"
    assert resolve.resolve_embedder_key("openai").key == "from-global"
    assert resolve.resolve_embedder_key("gemini").key is None  # global key names openai
    monkeypatch.setenv("OPENAI_API_KEY", "from-shell")
    assert resolve.resolve_embedder_key("openai", repo).key == "from-shell"
    assert resolve.resolve_embedder_key("ollama") == resolve.KeyLookup(None, ())


def test_kwargs_pass_the_resolved_key_and_model(tmp_path, monkeypatch):
    repo = _repo(tmp_path, env="GEMINI_API_KEY=g-key\n")
    monkeypatch.setenv("REPOWISE_EMBEDDING_MODEL", "gemini-embedding-002")
    assert resolve.embedder_kwargs("gemini", repo) == {
        "model": "gemini-embedding-002",
        "api_key": "g-key",
    }


def test_every_entry_point_resolves_the_same_embedder(tmp_path, monkeypatch):
    from repowise.cli.providers.embedders import resolve_embedder_for_repo
    from repowise.server.mcp_server import _server, _state

    repo = _repo(tmp_path, "embedder: openrouter\n", env="OPENROUTER_API_KEY=or-key\n")
    monkeypatch.setattr(_state, "_repo_path", str(repo))

    assert resolve_embedder_for_repo(repo) == "openrouter"
    assert _server._configured_embedder_name() == "openrouter"
    assert _server._embedder_kwargs("openrouter")["api_key"] == "or-key"


def test_http_server_builds_from_the_shared_resolver(monkeypatch):
    from repowise.core.providers.embedding.base import KeylessEmbedder
    from repowise.core.providers.embedding.openai import OpenAIEmbedder
    from repowise.server.app import _build_embedder

    assert isinstance(_build_embedder(), KeylessEmbedder)
    monkeypatch.setenv("REPOWISE_EMBEDDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert isinstance(_build_embedder(), OpenAIEmbedder)
