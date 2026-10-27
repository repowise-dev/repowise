"""The CLI must find the key ``init`` persisted.

Immediately after an init that generated and embedded 6,072 pages with a real
key, in the same shell, ``repowise search`` printed:

    Embedder 'openai' could not be built: ValueError: OpenAI API key required.
    Falling back to keyless embeddings, which means no semantic search.

The index was fine — 1536-wide vectors, all of them. What was missing was a key
*resolver*: the CLI's ``_embedder_kwargs`` never set ``api_key``, so the only
route was the adapter reading ``os.environ`` directly, and the tool surface
never merged ``.repowise/.env`` into it. The MCP server resolved the same repo
correctly, so the two disagreed about the same directory in the same shell.

These cover the resolver's precedence and, separately, the call site — a helper
test alone would keep passing if the threading were reverted.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from repowise.cli.providers.keys import resolve_embedder_api_key

KEY_ENV_VARS = ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY")


@pytest.fixture(autouse=True)
def _no_ambient_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A developer's exported key must not decide these assertions.

    Precedence tests are meaningless if the highest-precedence source is
    populated by the machine running them. ``HOME`` is redirected too, so the
    global-config tier reads a directory this test owns rather than the
    author's real ``~/.repowise/config.yaml``.
    """
    for var in (*KEY_ENV_VARS, "REPOWISE_EMBEDDER", "REPOWISE_EMBEDDING_MODEL"):
        monkeypatch.delenv(var, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))


def _write_env(repo_path: Path, **keys: str) -> None:
    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{k}={v}\n" for k, v in keys.items())
    (repowise_dir / ".env").write_text(body, encoding="utf-8")


def _write_config(repo_path: Path, **keys: object) -> None:
    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir(parents=True, exist_ok=True)
    (repowise_dir / "config.yaml").write_text(yaml.safe_dump(keys), encoding="utf-8")


def _write_global_config(**keys: object) -> None:
    global_dir = Path.home() / ".repowise"
    global_dir.mkdir(parents=True, exist_ok=True)
    (global_dir / "config.yaml").write_text(yaml.safe_dump(keys), encoding="utf-8")


# --- precedence -------------------------------------------------------------


def test_env_wins_over_everything(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An exported key is an explicit override and nothing may outrank it."""
    _write_env(tmp_path, OPENAI_API_KEY="sk-from-dotenv")
    _write_global_config(embedder="openai", embedder_api_key="sk-from-global")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")

    assert resolve_embedder_api_key("openai", tmp_path).key == "sk-from-env"


def test_dotenv_is_read_without_an_exported_key(tmp_path: Path) -> None:
    """The defect, at its narrowest: this is where ``init`` puts the key."""
    _write_env(tmp_path, OPENAI_API_KEY="sk-from-dotenv")

    assert resolve_embedder_api_key("openai", tmp_path).key == "sk-from-dotenv"


def test_dotenv_outranks_the_global_config(tmp_path: Path) -> None:
    """Repo-local beats machine-global — the repo's own key describes it."""
    _write_env(tmp_path, OPENAI_API_KEY="sk-from-dotenv")
    _write_global_config(embedder="openai", embedder_api_key="sk-from-global")

    assert resolve_embedder_api_key("openai", tmp_path).key == "sk-from-dotenv"


def test_global_config_is_the_last_resort(tmp_path: Path) -> None:
    _write_global_config(embedder="openai", embedder_api_key="sk-from-global")

    assert resolve_embedder_api_key("openai", tmp_path).key == "sk-from-global"


def test_global_key_is_ignored_for_a_different_embedder(tmp_path: Path) -> None:
    """Handing an OpenAI key to Gemini fails as "bad key", not "wrong key".

    The gate is the server's, mirrored: the saved credential belongs to the
    saved embedder, and only to it.
    """
    _write_global_config(embedder="openai", embedder_api_key="sk-from-global")

    assert resolve_embedder_api_key("gemini", tmp_path).key is None


def test_gemini_accepts_either_of_its_two_variables(tmp_path: Path) -> None:
    _write_env(tmp_path, GOOGLE_API_KEY="goog-key")

    assert resolve_embedder_api_key("gemini", tmp_path).key == "goog-key"


def test_keyless_backends_resolve_to_nothing(tmp_path: Path) -> None:
    """``ollama`` and ``mock`` want no credential, so none is "missing"."""
    for name in ("ollama", "mock", "unknown-backend"):
        lookup = resolve_embedder_api_key(name, tmp_path)
        assert lookup.key is None
        assert lookup.searched == ()


def test_resolution_does_not_mutate_the_environment(tmp_path: Path) -> None:
    """``load_dotenv``'s merge is first-writer-wins across the whole process.

    In a workspace that leaks repo A's key into every sibling's resolution.
    This resolver takes an explicit ``repo_path`` precisely so it cannot.
    """
    import os

    _write_env(tmp_path, OPENAI_API_KEY="sk-from-dotenv")

    assert resolve_embedder_api_key("openai", tmp_path).key == "sk-from-dotenv"
    assert "OPENAI_API_KEY" not in os.environ


def test_a_repoless_lookup_still_reads_env_and_global(tmp_path: Path) -> None:
    """Callers without a repo in hand must not crash, only resolve less far."""
    _write_global_config(embedder="openai", embedder_api_key="sk-from-global")

    assert resolve_embedder_api_key("openai", None).key == "sk-from-global"


# --- the key reaches the constructor ---------------------------------------


def test_embedder_kwargs_passes_the_dotenv_key_through(tmp_path: Path) -> None:
    """The adapter reads ``os.environ`` itself, so an unset var raises there.

    Passing ``api_key`` explicitly is the only way a key that lives on disk
    reaches it.
    """
    from repowise.cli.providers.embedders import _embedder_kwargs

    _write_env(tmp_path, OPENAI_API_KEY="sk-from-dotenv")

    assert _embedder_kwargs("openai", tmp_path).get("api_key") == "sk-from-dotenv"


def test_embedder_kwargs_omits_api_key_when_there_is_none(tmp_path: Path) -> None:
    """An empty ``api_key=`` would mask the adapter's own clearer refusal."""
    from repowise.cli.providers.embedders import _embedder_kwargs

    assert "api_key" not in _embedder_kwargs("openai", tmp_path)


def test_build_embedder_hands_the_key_to_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.cli import providers

    _write_env(tmp_path, OPENAI_API_KEY="sk-from-dotenv")
    seen: dict = {}

    def _spy(name: str, **kwargs: object):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr("repowise.core.providers.embedding.registry.get_embedder", _spy)
    providers.build_embedder("openai", tmp_path)

    assert seen.get("api_key") == "sk-from-dotenv"


# --- the call site that actually broke --------------------------------------


def test_the_tool_bridge_resolves_the_persisted_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``search`` and ``ask`` both build their store here.

    This is the reproducer for the reported symptom: an init-shaped repo, a
    real key on disk, nothing exported, and the store must come up with the
    configured embedder rather than the keyless fallback.
    """
    from repowise.cli import tool_bridge

    _write_config(tmp_path, embedder="openai")
    _write_env(tmp_path, OPENAI_API_KEY="sk-from-dotenv")
    (tmp_path / ".repowise" / "lancedb").mkdir(parents=True, exist_ok=True)

    seen: dict = {}

    def _spy(name: str, **kwargs: object):
        seen["name"] = name
        seen.update(kwargs)
        return object()

    monkeypatch.setattr("repowise.core.providers.embedding.registry.get_embedder", _spy)
    asyncio.run(tool_bridge._open_vector_store(tmp_path))

    assert seen.get("name") == "openai"
    assert seen.get("api_key") == "sk-from-dotenv"


def test_the_tool_bridge_does_not_report_a_degraded_embedder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user-visible half: ``_meta.embedder_degraded`` must come back false.

    A degraded status is what tells an agent the answer skipped semantic
    retrieval, and it was true for every ``search`` on a correctly indexed repo.
    """
    from repowise.cli import tool_bridge
    from repowise.server.mcp_server import _state

    _write_config(tmp_path, embedder="openai")
    _write_env(tmp_path, OPENAI_API_KEY="sk-from-dotenv")

    def _needs_a_key(name: str, **kwargs: object):
        # What ``OpenAIEmbedder.__init__`` does: no key, no embedder. A stub
        # that succeeds unconditionally would pass with the fix reverted.
        if not kwargs.get("api_key"):
            raise ValueError("OpenAI API key required")
        return object()

    monkeypatch.setattr("repowise.core.providers.embedding.registry.get_embedder", _needs_a_key)
    monkeypatch.setattr(_state, "_embedder_status", None, raising=False)
    asyncio.run(tool_bridge._open_vector_store(tmp_path))

    assert _state._embedder_status["degraded"] is False
    assert _state._embedder_status["active"] == "openai"


# --- the degraded message must name what it looked at -----------------------


def test_the_degraded_message_names_the_places_it_searched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Set OPENAI_API_KEY" is wrong advice for a user whose key is on disk.

    It sends them to fix something that is already correct, which is how this
    defect survived a user looking straight at it.
    """
    from repowise.cli import helpers, providers

    def _boom(name: str, **kwargs: object):
        raise ValueError("OpenAI API key required")

    monkeypatch.setattr("repowise.core.providers.embedding.registry.get_embedder", _boom)
    printed: list[str] = []
    monkeypatch.setattr(
        helpers.err_console, "print", lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    )

    providers.build_embedder("openai", tmp_path)

    said = " ".join(printed)
    assert "OPENAI_API_KEY" in said
    assert str(tmp_path / ".repowise" / ".env") in said
    assert "config.yaml" in said


# --- the CLI and the MCP server must not disagree ---------------------------


@pytest.mark.parametrize(
    "seed",
    [
        pytest.param("dotenv", id="key-in-dotenv"),
        pytest.param("global", id="key-in-global-config"),
        pytest.param("nowhere", id="no-key-anywhere"),
    ],
)
def test_cli_and_mcp_resolve_the_same_key_for_the_same_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seed: str
) -> None:
    """The disagreement is itself the bug, not just the CLI's half of it.

    An agent that reaches this repo over MCP got semantic search while the same
    agent shelling out to ``repowise search`` got full-text only, from the same
    directory in the same shell. The two resolvers are separate code (the
    server cannot import the CLI), so only a test holds them together.
    """
    from repowise.server.mcp_server import _server, _state

    if seed == "dotenv":
        _write_env(tmp_path, OPENAI_API_KEY="sk-from-dotenv")
        expected = "sk-from-dotenv"
    elif seed == "global":
        _write_global_config(embedder="openai", embedder_api_key="sk-from-global")
        expected = "sk-from-global"
    else:
        expected = None

    monkeypatch.setattr(_state, "_repo_path", str(tmp_path), raising=False)

    assert resolve_embedder_api_key("openai", tmp_path).key == expected
    assert _server._persisted_embedder_key("openai") == expected


def test_env_export_wins_in_both_cli_and_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #1711: an exported key is an explicit override on BOTH sides.

    The server previously never consulted the process env, so on a machine with
    an exported credential it resolved a different key (or none) than the CLI,
    and the same repo in the same shell disagreed over MCP vs ``repowise
    search``. The server now reads the env tier first, matching the CLI.
    """
    from repowise.server.mcp_server import _server, _state

    _write_env(tmp_path, OPENAI_API_KEY="«redacted:sk-repo»")
    _write_global_config(embedder="openai", embedder_api_key="«redacted:sk-global»")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    monkeypatch.setattr(_state, "_repo_path", str(tmp_path), raising=False)

    assert resolve_embedder_api_key("openai", tmp_path).key == "sk-from-env"
    assert _server._persisted_embedder_key("openai") == "sk-from-env"


def test_no_key_search_is_reported_for_a_keyless_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ollama refusing a connection is not a missing-credential problem.

    Listing key locations there would send the user hunting for a key that
    backend never wanted.
    """
    from repowise.cli import helpers, providers

    def _boom(name: str, **kwargs: object):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("repowise.core.providers.embedding.registry.get_embedder", _boom)
    printed: list[str] = []
    monkeypatch.setattr(
        helpers.err_console, "print", lambda *a, **k: printed.append(" ".join(str(x) for x in a))
    )

    providers.build_embedder("ollama", tmp_path)

    said = " ".join(printed)
    assert "connection refused" in said
    assert "No API key was found" not in said
