"""The one-time review of a provider an earlier first index copied into config.yaml (#5709)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.providers.llm import resolve
from repowise.core.providers.llm.inherited_pin import PIN_REVIEWED_KEY, release_inherited_pin
from repowise.core.providers.llm.resolve import resolve_llm
from repowise.core.repo_config import (
    changed_config_dependencies,
    config_dependency_fingerprints,
    config_fingerprint,
    load_repo_config,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    for var in ("REPOWISE_PROVIDER", "REPOWISE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("REPOWISE_CONFIG_DIR", str(tmp_path / "global"))
    resolve.clear_cache()


def _global(tmp_path: Path, provider: str, model: str) -> None:
    path = tmp_path / "global" / "provider_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"active_provider": provider, "active_model": model}), encoding="utf-8"
    )


def _indexed_repo(tmp_path: Path, config: str, **state) -> Path:
    """A repo as an index before the fix left it: config.yaml, then state.json."""
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    (repo / ".repowise" / "config.yaml").write_text(config, encoding="utf-8")
    state = {
        "config_fingerprint": config_fingerprint(repo),
        "config_dependency_fingerprints": config_dependency_fingerprints(repo),
        **state,
    }
    (repo / ".repowise" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return repo


def _state(repo: Path) -> dict:
    return json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))


_PINNED = "provider: openrouter\nmodel: or-model\nembedder: mock\n"
_INDEX_STAMP = {"provider": "openrouter", "model": "or-model"}


def test_a_copied_down_default_is_released(tmp_path):
    _global(tmp_path, "openrouter", "or-model")
    repo = _indexed_repo(tmp_path, _PINNED, **_INDEX_STAMP)

    assert release_inherited_pin(repo) is True

    assert load_repo_config(repo) == {"embedder": "mock"}
    assert _state(repo)[PIN_REVIEWED_KEY] is True
    # Same provider and model as before the release, now from the default...
    assert resolve_llm(repo).provenance["provider"] == "global"
    # ...so the repo follows the next one.
    _global(tmp_path, "ollama", "qwen")
    after = resolve_llm(repo)
    assert (after.provider, after.model) == ("ollama", "qwen")


def test_the_release_is_not_a_config_change_to_update(tmp_path):
    _global(tmp_path, "openrouter", "or-model")
    repo = _indexed_repo(tmp_path, _PINNED, **_INDEX_STAMP)

    release_inherited_pin(repo)

    state = _state(repo)
    assert state["config_fingerprint"] == config_fingerprint(repo)
    assert not changed_config_dependencies(
        state["config_dependency_fingerprints"], config_dependency_fingerprints(repo)
    )


def test_a_provider_that_differs_from_the_default_is_kept(tmp_path):
    """A --provider flag and a default that has since moved wrote the same lines."""
    _global(tmp_path, "ollama", "qwen")
    repo = _indexed_repo(tmp_path, _PINNED, **_INDEX_STAMP)

    assert release_inherited_pin(repo) is False

    assert load_repo_config(repo)["provider"] == "openrouter"
    assert _state(repo)[PIN_REVIEWED_KEY] is True


def test_a_provider_edited_after_the_index_is_kept(tmp_path):
    """config.yaml no longer names what the index ran on: someone chose it."""
    _global(tmp_path, "openrouter", "or-model")
    repo = _indexed_repo(tmp_path, _PINNED, provider="gemini", model="g-1")

    assert release_inherited_pin(repo) is False

    assert load_repo_config(repo)["provider"] == "openrouter"


def test_a_model_edited_after_the_index_is_kept(tmp_path):
    _global(tmp_path, "openrouter", "or-model")
    repo = _indexed_repo(tmp_path, _PINNED, provider="openrouter", model="older-model")

    assert release_inherited_pin(repo) is False

    assert load_repo_config(repo)["model"] == "or-model"


def test_the_review_runs_once(tmp_path):
    """A provider the repo has after the review is its own, even when it equals the default."""
    _global(tmp_path, "ollama", "qwen")
    repo = _indexed_repo(tmp_path, _PINNED, **_INDEX_STAMP)
    assert release_inherited_pin(repo) is False

    _global(tmp_path, "openrouter", "or-model")
    resolve.clear_cache()

    assert release_inherited_pin(repo) is False
    assert load_repo_config(repo)["provider"] == "openrouter"


def test_a_repo_indexed_after_the_fix_is_never_reviewed(tmp_path):
    _global(tmp_path, "openrouter", "or-model")
    repo = _indexed_repo(tmp_path, _PINNED, **_INDEX_STAMP, **{PIN_REVIEWED_KEY: True})
    before = (repo / ".repowise" / "state.json").read_bytes()

    assert release_inherited_pin(repo) is False

    assert load_repo_config(repo)["provider"] == "openrouter"
    assert (repo / ".repowise" / "state.json").read_bytes() == before


def test_a_repo_with_no_provider_line_is_marked_reviewed(tmp_path):
    """So a provider it is given tomorrow is not taken for an inherited one."""
    _global(tmp_path, "openrouter", "or-model")
    repo = _indexed_repo(tmp_path, "embedder: mock\n", **_INDEX_STAMP)

    assert release_inherited_pin(repo) is False

    assert load_repo_config(repo) == {"embedder": "mock"}
    assert _state(repo)[PIN_REVIEWED_KEY] is True


def test_a_repo_that_was_never_indexed_is_left_alone(tmp_path):
    _global(tmp_path, "openrouter", "or-model")
    repo = tmp_path / "repo"
    (repo / ".repowise").mkdir(parents=True)
    (repo / ".repowise" / "config.yaml").write_text(_PINNED, encoding="utf-8")

    assert release_inherited_pin(repo) is False
    assert release_inherited_pin(None) is False

    assert load_repo_config(repo)["provider"] == "openrouter"
    assert not (repo / ".repowise" / "state.json").exists()


def test_other_state_and_config_keys_survive_the_release(tmp_path):
    _global(tmp_path, "openrouter", "or-model")
    repo = _indexed_repo(
        tmp_path,
        _PINNED + "openrouter:\n  base_url: https://example.test/v1\n",
        **_INDEX_STAMP,
        last_sync_commit="abc123",
    )

    assert release_inherited_pin(repo) is True

    assert load_repo_config(repo) == {
        "embedder": "mock",
        "openrouter": {"base_url": "https://example.test/v1"},
    }
    state = _state(repo)
    assert state["last_sync_commit"] == "abc123"
    assert (state["provider"], state["model"]) == ("openrouter", "or-model")


def test_a_failed_review_does_not_fail_the_resolve(tmp_path, monkeypatch):
    _global(tmp_path, "openrouter", "or-model")
    repo = _indexed_repo(tmp_path, _PINNED, **_INDEX_STAMP)

    def _boom(*args, **kwargs):
        raise OSError("read-only checkout")

    monkeypatch.setattr("repowise.core.repo_config.save_repo_config", _boom)

    assert release_inherited_pin(repo) is False
    assert load_repo_config(repo)["provider"] == "openrouter"
    assert resolve_llm(repo).provider == "openrouter"
