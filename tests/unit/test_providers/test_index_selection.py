"""What a first index records about its provider: only a choice the repo made (#5709)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.providers.llm import resolve
from repowise.core.providers.llm.resolve import (
    inherits_default,
    record_index_selection,
    resolve_llm,
)
from repowise.core.providers.llm.specs import PROVIDER_SPECS

_ENV_VARS = (
    "REPOWISE_PROVIDER",
    "REPOWISE_MODEL",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "KIMI_API_KEY",
    "EDENAI_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("REPOWISE_CONFIG_DIR", str(tmp_path / "global"))
    resolve.clear_cache()


def _repo(tmp_path: Path, config: str = "") -> Path:
    rw = tmp_path / "repo" / ".repowise"
    rw.mkdir(parents=True, exist_ok=True)
    if config:
        (rw / "config.yaml").write_text(config, encoding="utf-8")
    return rw.parent


def _global(tmp_path: Path, **data) -> None:
    path = tmp_path / "global" / "provider_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    resolve.forget_cached(path)


# -- resolving without the repo's own choice -----------------------------------


def test_ignoring_the_repo_choice_resolves_the_shared_default(tmp_path):
    repo = _repo(tmp_path, "provider: openrouter\nmodel: or-model\n")
    _global(tmp_path, active_provider="ollama", active_model="qwen")

    assert resolve_llm(repo).provider == "openrouter"
    shared = resolve_llm(repo, ignore_repo_choice=True)
    assert (shared.provider, shared.model, shared.provider_source) == ("ollama", "qwen", "global")


def test_ignoring_the_repo_choice_keeps_the_repo_base_url(tmp_path):
    repo = _repo(tmp_path, "provider: gemini\nollama:\n  base_url: http://localhost:9\n")
    _global(tmp_path, active_provider="ollama")

    assert resolve_llm(repo, ignore_repo_choice=True).base_url == "http://localhost:9"


# -- inherits_default ----------------------------------------------------------


def test_the_global_default_with_its_model_is_inherited(tmp_path):
    repo = _repo(tmp_path)
    _global(tmp_path, active_provider="ollama", active_model="qwen")

    assert inherits_default(repo, "ollama", "qwen")
    assert not inherits_default(repo, "ollama", "another-model")
    assert not inherits_default(repo, "openrouter", "qwen")


def test_a_default_with_no_model_means_the_provider_default_model(tmp_path):
    repo = _repo(tmp_path)
    _global(tmp_path, active_provider="openai")

    assert inherits_default(repo, "openai", PROVIDER_SPECS["openai"].default_model)


def test_the_env_default_is_inherited(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("REPOWISE_PROVIDER", "gemini")
    monkeypatch.setenv("REPOWISE_MODEL", "g-1")

    assert inherits_default(repo, "gemini", "g-1")


def test_nothing_is_inherited_with_no_default(tmp_path):
    assert not inherits_default(_repo(tmp_path), "ollama", "qwen")


def test_the_repo_own_choice_does_not_hide_the_default(tmp_path):
    repo = _repo(tmp_path, "provider: ollama\nmodel: qwen\n")
    _global(tmp_path, active_provider="ollama", active_model="qwen")

    assert inherits_default(repo, "ollama", "qwen")


def test_an_auto_detected_provider_is_not_a_default(tmp_path, monkeypatch):
    """Detection follows the keys present, so its pick is recorded, as before."""
    repo = _repo(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    detected = resolve_llm(repo)
    assert detected.provider_source == "autodetect"

    assert not inherits_default(repo, detected.provider, detected.model)
    config = record_index_selection({}, repo, detected.provider, detected.model)
    assert config == {"provider": detected.provider, "model": detected.model}


# -- record_index_selection ----------------------------------------------------


def test_an_index_on_the_default_records_no_provider(tmp_path):
    repo = _repo(tmp_path)
    _global(tmp_path, active_provider="openrouter", active_model="or-model")

    config = record_index_selection({"embedder": "mock"}, repo, "openrouter", "or-model")

    assert config == {"embedder": "mock"}


def test_an_index_on_another_provider_records_it(tmp_path):
    repo = _repo(tmp_path)
    _global(tmp_path, active_provider="openrouter", active_model="or-model")

    config = record_index_selection({"embedder": "mock"}, repo, "ollama", "qwen")

    assert config == {"embedder": "mock", "provider": "ollama", "model": "qwen"}


def test_an_index_on_the_default_provider_with_another_model_records_both(tmp_path):
    repo = _repo(tmp_path)
    _global(tmp_path, active_provider="openrouter", active_model="or-model")

    config = record_index_selection({}, repo, "openrouter", "bigger-model")

    assert config == {"provider": "openrouter", "model": "bigger-model"}


def test_an_index_with_no_default_records_the_provider(tmp_path):
    config = record_index_selection({}, _repo(tmp_path), "ollama", "qwen")

    assert config == {"provider": "ollama", "model": "qwen"}


def test_a_choice_that_equals_the_default_is_kept(tmp_path):
    """Only the run's own write is withheld; a choice already there is the repo's."""
    repo = _repo(tmp_path)
    _global(tmp_path, active_provider="ollama", active_model="qwen")

    both = record_index_selection({"provider": "ollama", "model": "qwen"}, repo, "ollama", "qwen")
    provider_only = record_index_selection({"provider": "ollama"}, repo, "ollama", "qwen")

    assert both == {"provider": "ollama", "model": "qwen"}
    assert provider_only == {"provider": "ollama"}


def test_a_choice_the_run_overrode_with_the_default_is_dropped(tmp_path):
    """A flag naming the default is the only way a run gets past the repo's own provider."""
    repo = _repo(tmp_path)
    _global(tmp_path, active_provider="ollama", active_model="qwen")

    config = record_index_selection(
        {"provider": "openrouter", "model": "or-model", "embedder": "mock"}, repo, "ollama", "qwen"
    )

    assert config == {"embedder": "mock"}


def test_a_repo_indexed_on_the_default_follows_a_new_default(tmp_path):
    """The reported bug, at the resolver: index on one default, then change it."""
    repo = _repo(tmp_path)
    _global(tmp_path, active_provider="openrouter", active_model="or-model")
    used = resolve_llm(repo)
    config = record_index_selection({}, repo, used.provider, used.model)
    assert "provider" not in config and "model" not in config

    _global(tmp_path, active_provider="ollama", active_model="qwen")

    after = resolve_llm(repo)
    assert (after.provider, after.model, after.provider_source) == ("ollama", "qwen", "global")
