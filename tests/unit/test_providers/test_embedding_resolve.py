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


def test_kwargs_reads_pinned_embedding_model_from_repo_config(tmp_path):
    repo = _repo(
        tmp_path,
        config="embedder: openrouter\nembedding_model: mongodb/voyage-code-4\n",
        env="OPENROUTER_API_KEY=or-key\n",
    )
    assert resolve.embedder_kwargs("openrouter", repo) == {
        "model": "mongodb/voyage-code-4",
        "api_key": "or-key",
    }


def test_env_var_overrides_pinned_embedding_model(tmp_path, monkeypatch):
    repo = _repo(
        tmp_path,
        config="embedder: openrouter\nembedding_model: mongodb/voyage-code-4\n",
    )
    monkeypatch.setenv("REPOWISE_EMBEDDING_MODEL", "custom-override-model")
    assert resolve.embedding_model_for("openrouter", repo) == "custom-override-model"


def test_pinned_embedding_model_rejected_when_embedder_mismatches(tmp_path):
    repo = _repo(
        tmp_path,
        config="embedder: openrouter\nembedding_model: mongodb/voyage-code-4\n",
    )
    # The pin belongs to openrouter, so resolving gemini ignores the Voyage pin
    assert resolve.embedding_model_for("gemini", repo) is None


def test_pinned_embedding_model_rejected_when_embedder_unspecified(tmp_path):
    # An unbound embedding_model pin without an embedder key is ignored
    repo = _repo(
        tmp_path,
        config="embedding_model: mongodb/voyage-code-4\n",
    )
    assert resolve.embedding_model_for("openrouter", repo) is None
    assert resolve.embedding_model_for("gemini", repo) is None


def test_resolve_embedding_model_honours_repo_pin(tmp_path):
    from repowise.cli.providers.embedders import resolve_embedding_model

    repo = _repo(
        tmp_path,
        config="embedder: openrouter\nembedding_model: mongodb/voyage-code-4\n",
    )
    assert resolve_embedding_model("openrouter", repo) == "mongodb/voyage-code-4"
    assert resolve_embedding_model("gemini", repo) is None


def test_init_persistence_preserves_config_only_pinned_embedding_model(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch

    from repowise.cli.commands.init_cmd.persistence import save_full_state_and_config
    from repowise.cli.helpers import load_config

    repo = _repo(
        tmp_path,
        config="embedder: ollama\nembedding_model: qwen3-embedding:8b\n",
    )
    (repo / ".repowise" / "state.json").write_text("{}", encoding="utf-8")

    def _fake_run_async(coro):
        coro.close()
        return 0

    with (
        patch("repowise.cli.commands.init_cmd.persistence.get_head_commit", return_value="abc1234"),
        patch("repowise.cli.commands.init_cmd.persistence.record_decision_lanes"),
        patch("repowise.cli.commands.init_cmd.persistence.apply_git_history_coverage_state"),
        patch("repowise.cli.commands.init_cmd.persistence.run_async", side_effect=_fake_run_async),
        patch("repowise.cli.commands.init_cmd.persistence.config_fingerprint", return_value="fp"),
        patch(
            "repowise.cli.commands.init_cmd.persistence.config_dependency_fingerprints",
            return_value={},
        ),
    ):
        save_full_state_and_config(
            repo_path=repo,
            result=SimpleNamespace(generated_pages=[]),
            provider=SimpleNamespace(provider_name="ollama", model_name="qwen2.5-coder"),
            phase_timings={},
            embedder_name_resolved="ollama",
            exclude_patterns=[],
            commit_limit=None,
            resolved_commit_limit=50,
            resolved_reasoning="medium",
            save_key=False,
        )

    cfg = load_config(repo)
    assert cfg.get("embedding_model") == "qwen3-embedding:8b"


def test_index_only_finish_preserves_config_only_pinned_embedding_model(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import patch

    from repowise.cli.commands.init_cmd._finish import _save_index_only_state
    from repowise.cli.helpers import load_config

    repo = _repo(
        tmp_path,
        config="embedder: ollama\nembedding_model: qwen3-embedding:8b\n",
    )
    (repo / ".repowise" / "state.json").write_text("{}", encoding="utf-8")

    run = SimpleNamespace(
        repo_path=repo,
        index_only_embedder="ollama",
        exclude_patterns=[],
        resolved_commit_limit=50,
        opts=SimpleNamespace(commit_limit=None),
        result=SimpleNamespace(),
    )

    with (
        patch("repowise.cli.commands.init_cmd._finish.stamp_fresh_index"),
        patch("repowise.cli.commands.init_cmd._finish.save_state"),
    ):
        _save_index_only_state(run, {})

    cfg = load_config(repo)
    assert cfg.get("embedding_model") == "qwen3-embedding:8b"


def test_workspace_save_repo_config_preserves_pinned_embedding_model(tmp_path):
    from types import SimpleNamespace

    from repowise.cli.commands.init_cmd.workspace import _save_repo_config
    from repowise.cli.helpers import load_config

    repo = _repo(
        tmp_path,
        config="embedder: ollama\nembedding_model: qwen3-embedding:8b\n",
    )

    ctx = SimpleNamespace(
        provider=SimpleNamespace(provider_name="ollama", model_name="qwen2.5-coder"),
        embedder_name_resolved="ollama",
        exclude_patterns=[],
        resolved_commit_limit=50,
        resolved_reasoning="medium",
        save_key=False,
        wiki_style="default",
        language="en",
    )
    docs = SimpleNamespace(docs_mode="llm")

    _save_repo_config(repo, ctx, docs)

    cfg = load_config(repo)
    assert cfg.get("embedding_model") == "qwen3-embedding:8b"
