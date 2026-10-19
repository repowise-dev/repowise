"""The one LLM resolver: precedence, provenance, keys, cache, and the yaml writer."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from repowise.core.providers.llm import resolve
from repowise.core.providers.llm.resolve import (
    UNSET,
    apply_llm_selection,
    llm_provider_kwargs,
    resolve_api_key,
    resolve_llm,
    save_repo_llm_selection,
    validate_base_url,
)
from repowise.core.providers.llm.specs import PROVIDER_SPECS
from repowise.core.repo_config import RepoConfigError, load_repo_config

_ENV_VARS = (
    "REPOWISE_PROVIDER",
    "REPOWISE_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY",
    "OLLAMA_BASE_URL",
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


def _repo(tmp_path: Path, config: str = "", env: str = "") -> Path:
    rw = tmp_path / "repo" / ".repowise"
    rw.mkdir(parents=True, exist_ok=True)
    if config:
        (rw / "config.yaml").write_text(config, encoding="utf-8")
    if env:
        (rw / ".env").write_text(env, encoding="utf-8")
    return rw.parent


def _global(tmp_path: Path, **data) -> None:
    path = tmp_path / "global" / "provider_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    resolve.forget_cached(path)


# -- precedence --------------------------------------------------------------


def test_flag_beats_config_global_and_env(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "provider: openai\nmodel: gpt-a\n")
    _global(tmp_path, active_provider="gemini", active_model="g")
    monkeypatch.setenv("REPOWISE_PROVIDER", "kimi")

    sel = resolve_llm(repo, provider="anthropic", model="claude-x")
    assert (sel.provider, sel.model) == ("anthropic", "claude-x")
    assert sel.provenance == {"provider": "flag", "model": "flag", "base_url": None}


def test_config_beats_global_and_env(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "provider: openai\nmodel: gpt-a\n")
    _global(tmp_path, active_provider="gemini", active_model="g")
    monkeypatch.setenv("REPOWISE_PROVIDER", "kimi")
    monkeypatch.setenv("REPOWISE_MODEL", "k")

    sel = resolve_llm(repo)
    assert (sel.provider, sel.model) == ("openai", "gpt-a")
    assert sel.provider_source == sel.model_source == "config.yaml"


def test_global_beats_env(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "embedder: mock\n")
    _global(tmp_path, active_provider="gemini", active_model="g")
    monkeypatch.setenv("REPOWISE_PROVIDER", "kimi")

    sel = resolve_llm(repo)
    assert (sel.provider, sel.model, sel.provider_source) == ("gemini", "g", "global")


def test_env_then_spec_default(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("REPOWISE_PROVIDER", "kimi")

    sel = resolve_llm(repo)
    assert sel.provider == "kimi"
    assert sel.provider_source == "env:REPOWISE_PROVIDER"
    assert (sel.model, sel.model_source) == (PROVIDER_SPECS["kimi"].default_model, "default")


def test_env_reads_the_repo_env_file(tmp_path):
    repo = _repo(tmp_path, env="REPOWISE_PROVIDER=deepseek\n")
    assert resolve_llm(repo).provider == "deepseek"


def test_autodetect_from_keys_including_stored_ones(tmp_path):
    repo = _repo(tmp_path)
    assert resolve_llm(repo).provider is None

    _global(tmp_path, keys={"openrouter": "sk-or"})
    sel = resolve_llm(repo)
    assert (sel.provider, sel.provider_source) == ("openrouter", "autodetect")
    assert resolve_llm(repo, autodetect=False).provider is None


def test_a_model_only_follows_its_own_provider(tmp_path):
    repo = _repo(tmp_path, "provider: gemini\nmodel: gemini-3.1-pro-preview\n")
    sel = resolve_llm(repo, provider="openai")
    assert sel.model == PROVIDER_SPECS["openai"].default_model


def test_a_model_without_a_provider_applies_to_the_resolved_one(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "model: my-local-model\n")
    monkeypatch.setenv("REPOWISE_PROVIDER", "openai")
    sel = resolve_llm(repo)
    assert (sel.provider, sel.model, sel.model_source) == (
        "openai",
        "my-local-model",
        "config.yaml",
    )


def test_known_filters_out_providers_the_caller_cannot_build(tmp_path):
    repo = _repo(tmp_path, "provider: mock\nmodel: m\n")
    _global(tmp_path, active_provider="anthropic")
    sel = resolve_llm(repo, known={"anthropic", "openai"})
    assert (sel.provider, sel.provider_source) == ("anthropic", "global")
    assert sel.model == PROVIDER_SPECS["anthropic"].default_model


# -- base URL ----------------------------------------------------------------


def test_base_url_precedence(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "provider: openai\nopenai:\n  base_url: http://yaml:1/v1\n")
    _global(tmp_path, active_provider="openai", active_base_url="http://global:1/v1")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://env:1/v1")

    assert resolve_llm(repo, base_url="http://flag:1/v1").base_url == "http://flag:1/v1"
    sel = resolve_llm(repo)
    assert (sel.base_url, sel.base_url_source) == ("http://yaml:1/v1", "config.yaml")

    save_repo_llm_selection(repo, "openai", None, base_url=None)
    sel = resolve_llm(repo)
    assert (sel.base_url, sel.base_url_source) == ("http://global:1/v1", "global")

    _global(tmp_path, active_provider="openai")
    sel = resolve_llm(repo)
    assert (sel.base_url, sel.base_url_source) == ("http://env:1/v1", "env:OPENAI_BASE_URL")


def test_global_base_url_only_applies_to_its_provider(tmp_path):
    repo = _repo(tmp_path, "provider: kimi\n")
    _global(tmp_path, active_provider="openai", active_base_url="http://lm:1234/v1")
    assert resolve_llm(repo).base_url is None


# -- keys and kwargs ---------------------------------------------------------


def test_key_chain_env_then_repo_env_then_stored(tmp_path, monkeypatch):
    repo = _repo(tmp_path, env="OPENAI_API_KEY=from-file\n")
    _global(tmp_path, keys={"openai": "from-store"})

    monkeypatch.setenv("OPENAI_API_KEY", "from-shell")
    assert resolve_api_key("openai", repo) == ("from-shell", "env:OPENAI_API_KEY")
    monkeypatch.delenv("OPENAI_API_KEY")
    assert resolve_api_key("openai", repo) == ("from-file", ".repowise/.env")
    assert resolve_api_key("openai", None) == ("from-store", "provider_config.json")


def test_kwargs_carry_key_url_and_repo_path(tmp_path):
    repo = _repo(
        tmp_path,
        "provider: openai\nmodel: qwen3\nopenai:\n  base_url: http://localhost:1234/v1\n",
        env="OPENAI_API_KEY=lm-studio\n",
    )
    kwargs = llm_provider_kwargs(resolve_llm(repo), repo)
    assert kwargs == {
        "model": "qwen3",
        "api_key": "lm-studio",
        "base_url": "http://localhost:1234/v1",
    }
    assert llm_provider_kwargs(resolve_llm(repo, provider="codex_cli"), repo)["repo_path"] == repo


# -- cache -------------------------------------------------------------------


def test_files_are_parsed_once_per_version(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "provider: openai\n")
    calls = []
    real = resolve._parse_repo_config
    monkeypatch.setattr(resolve, "_parse_repo_config", lambda p: calls.append(p) or real(p))

    for _ in range(5):
        resolve_llm(repo)
    assert len(calls) == 1

    save_repo_llm_selection(repo, "anthropic", "claude-x")
    assert resolve_llm(repo).provider == "anthropic"
    assert len(calls) == 2


# -- writer ------------------------------------------------------------------


def test_writer_keeps_other_keys_and_sets_or_clears_base_url(tmp_path):
    repo = _repo(tmp_path, "provider: gemini\nmodel: g\nembedder: gemini\ncommit_limit: 50\n")

    save_repo_llm_selection(repo, "openai", "qwen3", base_url="http://localhost:1234/v1")
    cfg = load_repo_config(repo)
    assert cfg["provider"] == "openai" and cfg["model"] == "qwen3"
    assert cfg["openai"] == {"base_url": "http://localhost:1234/v1"}
    assert cfg["embedder"] == "gemini" and cfg["commit_limit"] == 50

    save_repo_llm_selection(repo, "openai", "other")  # base_url left as is
    assert load_repo_config(repo)["openai"]["base_url"] == "http://localhost:1234/v1"

    save_repo_llm_selection(repo, "openai", None, base_url="")
    cfg = load_repo_config(repo)
    assert "openai" not in cfg and "model" not in cfg


def test_writer_keeps_unrelated_keys_in_the_provider_section():
    cfg = {"openai": {"base_url": "http://x/v1", "timeout": 5}}
    apply_llm_selection(cfg, "openai", "m", base_url=None)
    assert cfg["openai"] == {"timeout": 5}
    assert apply_llm_selection({}, "openai", "m", UNSET) == {"provider": "openai", "model": "m"}


def test_writer_refuses_to_overwrite_a_broken_file(tmp_path):
    repo = _repo(tmp_path, "provider: [unclosed\n")
    with pytest.raises(RepoConfigError):
        save_repo_llm_selection(repo, "openai", "m")
    assert (repo / ".repowise" / "config.yaml").read_text(encoding="utf-8") == "provider: [unclosed\n"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("", None), (None, None), ("  http://localhost:1234/v1 ", "http://localhost:1234/v1")],
)
def test_validate_base_url_accepts(value, expected):
    assert validate_base_url(value) == expected


@pytest.mark.parametrize("value", ["localhost:1234", "ftp://x", "http://", "file:///etc"])
def test_validate_base_url_rejects(value):
    with pytest.raises(ValueError, match="http"):
        validate_base_url(value)


def test_a_same_size_rewrite_is_seen(tmp_path):
    """Two writes of equal length inside one timestamp tick, as another process would."""
    repo = _repo(tmp_path, "provider: openai\n")
    path = repo / ".repowise" / "config.yaml"
    assert resolve_llm(repo).provider == "openai"
    stat = path.stat()
    path.write_text("provider: gemini\n", encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert resolve_llm(repo).provider == "gemini"


# -- base URL trust ------------------------------------------------------------

_REMOTE = "provider: openai\nopenai:\n  base_url: https://collector.example/v1\n"


def test_untrusted_yaml_base_url_does_not_get_an_outside_key(tmp_path, monkeypatch):
    repo = _repo(tmp_path, _REMOTE)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-shell")
    with pytest.raises(resolve.UntrustedBaseUrlError, match=r"collector\.example") as info:
        llm_provider_kwargs(resolve_llm(repo), repo)
    assert "sk-shell" not in str(info.value)

    _global(tmp_path, keys={"openai": "sk-stored"})
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(resolve.UntrustedBaseUrlError):
        llm_provider_kwargs(resolve_llm(repo), repo)


def test_trusted_yaml_base_url_gets_the_key(tmp_path, monkeypatch):
    repo = _repo(tmp_path, _REMOTE)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-shell")
    resolve.trust_base_url(repo, "https://collector.example/v1")
    assert llm_provider_kwargs(resolve_llm(repo), repo)["api_key"] == "sk-shell"


def test_repo_env_key_and_loopback_urls_need_no_confirmation(tmp_path, monkeypatch):
    repo = _repo(tmp_path, _REMOTE, env="OPENAI_API_KEY=sk-repo\n")
    assert llm_provider_kwargs(resolve_llm(repo), repo)["api_key"] == "sk-repo"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-shell")
    for url in ("http://localhost:1234/v1", "http://127.0.0.1:8000/v1", "http://[::1]:9/v1"):
        save_repo_llm_selection(repo, "openai", None, base_url=url)
        assert llm_provider_kwargs(resolve_llm(repo), repo)["base_url"] == url


def test_keyless_request_to_an_unconfirmed_url_is_allowed(tmp_path):
    repo = _repo(tmp_path, _REMOTE)
    assert "api_key" not in llm_provider_kwargs(resolve_llm(repo), repo)


def test_credentials_in_a_base_url_are_refused_and_never_shown(tmp_path):
    with pytest.raises(ValueError, match="user name or password") as info:
        validate_base_url("https://me:hunter2@gw.example/v1")
    assert "hunter2" not in str(info.value)

    repo = _repo(tmp_path, "provider: openai\nopenai:\n  base_url: https://me:hunter2@gw.example/v1\n")
    sel = resolve_llm(repo)
    assert sel.display_base_url == "https://gw.example/v1"
    assert "hunter2" not in sel.describe()
    with pytest.raises(ValueError, match=r"config\.yaml") as info:
        llm_provider_kwargs(sel, repo)
    assert "hunter2" not in str(info.value)


@pytest.mark.parametrize("writer", ["config.yaml", "provider_config.json"])
def test_writers_retry_while_a_reader_holds_the_file(tmp_path, monkeypatch, writer):
    """Windows refuses a replace while another thread has the file open."""
    from repowise.core import fsutils
    from repowise.core.repo_config import save_repo_config

    real_replace = os.replace
    refusals = []

    def busy_then_free(src, dst):
        if len(refusals) < 3:
            refusals.append(dst)
            raise PermissionError(13, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(fsutils.os, "replace", busy_then_free)
    monkeypatch.setattr(fsutils.time, "sleep", lambda _s: None)
    repo = _repo(tmp_path)
    if writer == "config.yaml":
        save_repo_config(repo, {"provider": "openai"})
        assert load_repo_config(repo) == {"provider": "openai"}
    else:
        resolve.update_global_config(lambda config: config.update(active_provider="kimi"))
        assert resolve.read_global_config()["active_provider"] == "kimi"
    assert len(refusals) == 3
