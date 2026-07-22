"""Per-repo chat provider resolution (issue #426).

The web-UI chat must use the provider/model/API-key/base-url that
``repowise init`` wrote to each repo's ``.repowise/config.yaml`` +
``.repowise/.env`` — the same configuration ``repowise update`` already
honours — instead of falling back to a hardcoded catalog default. Selecting a
model for one repo must not shadow another repo in a workspace.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from repowise.server import provider_config as pc


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Replace ``os.environ`` with a minimal dict and isolate the config file.

    The dev machine often has real ``*_API_KEY`` vars exported, which would
    otherwise leak into auto-detection and make these assertions flaky.
    """
    env: dict[str, str] = {"REPOWISE_CONFIG_DIR": str(tmp_path / "server")}
    monkeypatch.setattr(pc.os, "environ", env)
    return env


def _make_repo(root: Path, *, config: str = "", env: str = "") -> Path:
    """Create a repo dir with ``.repowise/config.yaml`` and ``.repowise/.env``."""
    rw = root / ".repowise"
    rw.mkdir(parents=True, exist_ok=True)
    if config:
        (rw / "config.yaml").write_text(textwrap.dedent(config), encoding="utf-8")
    if env:
        (rw / ".env").write_text(textwrap.dedent(env), encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# The reported bug: repo config.yaml model is ignored, default is used.
# ---------------------------------------------------------------------------


def test_repo_config_model_used_without_ui_selection(clean_env, tmp_path):
    repo = _make_repo(
        tmp_path / "repo",
        config="""
            provider: openai
            model: gemma4
            embedder: openai
        """,
        env="OPENAI_API_KEY=admin\nOPENAI_BASE_URL=http://localhost:4000/v1\n",
    )

    provider, model = pc.get_active_provider(repo_id="r1", repo_path=str(repo))
    assert provider == "openai"
    assert model == "gemma4"  # not the catalog default gpt-5.4-nano


def test_get_chat_provider_passes_repo_key_model_and_base_url(clean_env, tmp_path, monkeypatch):
    repo = _make_repo(
        tmp_path / "repo",
        config="""
            provider: openai
            model: gemma4
            embedder: openai
        """,
        env="OPENAI_API_KEY=admin\nOPENAI_BASE_URL=http://localhost:4000/v1\n",
    )

    captured: dict = {}

    def fake_get_provider(provider_id, **kwargs):
        captured["provider_id"] = provider_id
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("repowise.core.providers.llm.registry.get_provider", fake_get_provider)

    pc.get_chat_provider_instance(repo_path=str(repo), repo_id="r1")

    assert captured["provider_id"] == "openai"
    assert captured["model"] == "gemma4"
    assert captured["api_key"] == "admin"  # from .repowise/.env
    assert captured["base_url"] == "http://localhost:4000/v1"


def test_kimi_repo_config_passes_key_model_and_base_url(clean_env, tmp_path, monkeypatch):
    repo = _make_repo(
        tmp_path / "repo",
        config="""
            provider: kimi
            model: kimi-k2.6
            embedder: mock
        """,
        env="KIMI_API_KEY=sk-kimi-test\nKIMI_BASE_URL=https://kimi.example/v1\n",
    )

    captured: dict = {}

    def fake_get_provider(provider_id, **kwargs):
        captured["provider_id"] = provider_id
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("repowise.core.providers.llm.registry.get_provider", fake_get_provider)

    pc.get_chat_provider_instance(repo_path=str(repo), repo_id="kimi-repo")

    assert captured["provider_id"] == "kimi"
    assert captured["model"] == "kimi-k2.6"
    assert captured["api_key"] == "sk-kimi-test"
    assert captured["base_url"] == "https://kimi.example/v1"


# ---------------------------------------------------------------------------
# Precedence + no cross-repo shadowing.
# ---------------------------------------------------------------------------


def test_per_repo_selection_does_not_shadow_other_repo(clean_env, tmp_path):
    repo_a = _make_repo(
        tmp_path / "a",
        config="provider: openai\nmodel: gemma4\nembedder: openai\n",
        env="OPENAI_API_KEY=admin\n",
    )
    repo_b = _make_repo(
        tmp_path / "b",
        config="provider: openai\nmodel: codellama\nembedder: openai\n",
        env="OPENAI_API_KEY=admin\n",
    )

    # User picks a different model for repo A via the UI.
    pc.set_active_provider("openai", "gpt-5.4", repo_id="ra")

    a_provider, a_model = pc.get_active_provider(repo_id="ra", repo_path=str(repo_a))
    b_provider, b_model = pc.get_active_provider(repo_id="rb", repo_path=str(repo_b))

    assert (a_provider, a_model) == ("openai", "gpt-5.4")  # A's override
    assert (b_provider, b_model) == ("openai", "codellama")  # B untouched


def test_request_override_beats_repo_config(clean_env, tmp_path, monkeypatch):
    repo = _make_repo(
        tmp_path / "repo",
        config="provider: openai\nmodel: gemma4\nembedder: openai\n",
        env="OPENAI_API_KEY=admin\nANTHROPIC_API_KEY=sk-ant\n",
    )

    captured: dict = {}
    monkeypatch.setattr(
        "repowise.core.providers.llm.registry.get_provider",
        lambda provider_id, **kw: captured.update({"id": provider_id, **kw}) or object(),
    )

    pc.get_chat_provider_instance(
        repo_path=str(repo),
        repo_id="r1",
        provider_override="anthropic",
        model_override="claude-opus-4-6",
    )

    assert captured["id"] == "anthropic"
    assert captured["model"] == "claude-opus-4-6"


# ---------------------------------------------------------------------------
# Status surfaces the configured (possibly custom) model for the picker.
# ---------------------------------------------------------------------------


def test_list_provider_status_surfaces_custom_model(clean_env, tmp_path):
    repo = _make_repo(
        tmp_path / "repo",
        config="provider: openai\nmodel: gemma4\nembedder: openai\n",
        env="OPENAI_API_KEY=admin\n",
    )

    status = pc.list_provider_status(repo_id="r1", repo_path=str(repo))

    assert status["active"] == {"provider": "openai", "model": "gemma4"}
    openai = next(p for p in status["providers"] if p["id"] == "openai")
    assert "gemma4" in openai["models"]  # custom model is selectable
    assert openai["configured"] is True  # key seen via repo .env


def test_repo_env_key_enables_autodetect(clean_env, tmp_path):
    # No provider in config — only a key in the repo .env. Auto-detect should
    # still find it (without the server holding the key in its own environ).
    repo = _make_repo(
        tmp_path / "repo",
        config="embedder: openai\n",
        env="OPENAI_API_KEY=admin\n",
    )

    provider, _ = pc.get_active_provider(repo_id="r1", repo_path=str(repo))
    assert provider == "openai"


def test_unknown_provider_override_raises(clean_env, tmp_path):
    repo = _make_repo(
        tmp_path / "repo",
        config="provider: openai\nmodel: gemma4\nembedder: openai\n",
        env="OPENAI_API_KEY=admin\n",
    )
    with pytest.raises(ValueError, match="Unknown provider"):
        pc.get_chat_provider_instance(repo_path=str(repo), repo_id="r1", provider_override="bogus")


def test_global_selection_used_when_repo_has_no_config(clean_env, tmp_path):
    repo = _make_repo(tmp_path / "repo", config="embedder: mock\n")
    pc.set_active_provider("anthropic", "claude-sonnet-4-6")  # global default

    provider, model = pc.get_active_provider(repo_id="r1", repo_path=str(repo))
    assert (provider, model) == ("anthropic", "claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# D6: set_api_key mirrors into the repo's .repowise/.env so the CLI sees it.
# ---------------------------------------------------------------------------


def test_set_api_key_mirrors_into_repo_env(clean_env, tmp_path):
    repo = _make_repo(tmp_path / "repo", config="embedder: mock\n")

    pc.set_api_key("anthropic", "sk-ant-xyz", repo_path=str(repo))

    # Server store updated...
    assert pc._get_key_for_provider("anthropic") == "sk-ant-xyz"
    # ...and mirrored under the provider's canonical env var so a later CLI run
    # (which reads .env, not the server store) picks it up.
    from repowise.core.repo_config import load_repo_env

    assert load_repo_env(repo)["ANTHROPIC_API_KEY"] == "sk-ant-xyz"


def test_set_api_key_removal_clears_repo_env(clean_env, tmp_path):
    repo = _make_repo(tmp_path / "repo", config="embedder: mock\n")
    pc.set_api_key("anthropic", "sk-ant-xyz", repo_path=str(repo))

    pc.set_api_key("anthropic", None, repo_path=str(repo))

    from repowise.core.repo_config import load_repo_env

    assert "ANTHROPIC_API_KEY" not in load_repo_env(repo)


def test_set_api_key_without_repo_path_is_global_only(clean_env, tmp_path):
    repo = _make_repo(tmp_path / "repo", config="embedder: mock\n")

    pc.set_api_key("anthropic", "sk-ant-xyz")  # no repo_path

    assert pc._get_key_for_provider("anthropic") == "sk-ant-xyz"
    assert not (repo / ".repowise" / ".env").exists()


def test_set_api_key_keyless_provider_writes_no_env(clean_env, tmp_path):
    # Ollama takes no key (empty env_keys); there is nothing to mirror.
    repo = _make_repo(tmp_path / "repo", config="embedder: mock\n")

    pc.set_api_key("ollama", "irrelevant", repo_path=str(repo))

    assert not (repo / ".repowise" / ".env").exists()


def test_set_api_key_rejects_newline_in_value(clean_env, tmp_path):
    # A value with a newline would inject extra env lines a later CLI run parses.
    repo = _make_repo(tmp_path / "repo", config="embedder: mock\n")
    with pytest.raises(ValueError, match="newline"):
        pc.set_api_key("anthropic", "sk-ant\nEVIL=1", repo_path=str(repo))


def test_config_path_defaults_to_home(monkeypatch, tmp_path):
    # No REPOWISE_CONFIG_DIR: the store lives in ~/.repowise, not the CWD.
    monkeypatch.setattr(pc.os, "environ", {})
    monkeypatch.setattr(pc.Path, "home", classmethod(lambda cls: tmp_path))
    assert pc._config_path() == tmp_path / ".repowise" / "provider_config.json"


def test_list_provider_status_never_returns_key_material(clean_env, tmp_path):
    repo = _make_repo(
        tmp_path / "repo",
        config="provider: openai\nmodel: gpt-5.4-nano\nembedder: openai\n",
        env="OPENAI_API_KEY=super-secret-key\n",
    )

    status = pc.list_provider_status(repo_id="r1", repo_path=str(repo))

    serialized = repr(status)
    assert "super-secret-key" not in serialized
    for provider in status["providers"]:
        assert set(provider) == {"id", "name", "models", "default_model", "configured"}
        assert "key" not in provider
        assert "api_key" not in provider
