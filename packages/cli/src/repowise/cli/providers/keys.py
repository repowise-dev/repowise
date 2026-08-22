"""One resolver for the embedder credential every CLI command needs.

The CLI used to have no resolver at all: :func:`_embedder_kwargs` never set
``api_key``, so the only route was ``OpenAIEmbedder.__init__`` reading
``os.environ`` directly. A command that forgot ``load_dotenv`` therefore built a
``KeylessEmbedder`` against an index written with real 1536-wide vectors, and
said so in a warning that named the one place the key was *not*
(``OPENAI_API_KEY``) rather than the two places it was.

The MCP server already resolved this correctly
(``mcp_server/_server.py::_persisted_embedder_key``). Centralising *persistence*
without centralising *consumption* is what let the two halves drift: the same
repo, in the same shell, answers semantically through MCP and lexically through
``repowise search``. This module is the consumption half, and it deliberately
mirrors the server's precedence rather than inventing a new one.

Pure by construction: nothing here mutates ``os.environ``. ``load_dotenv``'s
merge-into-the-process semantics are first-writer-wins, so in a workspace one
repo's key would answer for every sibling. Resolving per call with an explicit
``repo_path`` cannot do that.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, NamedTuple

# Mirrors ``mcp_server/_server.py::_EMBEDDER_KEY_ENV``. The two copies are the
# shape this module exists to stop spreading, but the server cannot import the
# CLI and neither can reach a shared home without moving the map into
# ``core.providers.embedding`` — a wider change than the defect needs. Ceiling
# noted deliberately: fold both into core when a third consumer appears.
_EMBEDDER_KEY_ENV: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
    "edenai": ("EDENAI_API_KEY",),
}


class KeyLookup(NamedTuple):
    """The resolved key, and the places that were consulted to find it.

    ``searched`` exists so the degraded-run message can name what it looked at.
    A user whose key sits in ``.repowise/.env`` was previously told to "set
    OPENAI_API_KEY", advice that does not apply to them and sends them to fix
    something that is not broken.
    """

    key: str | None
    searched: tuple[str, ...]


def embedder_key_env_vars(embedder_name: str) -> tuple[str, ...]:
    """Environment variables that carry *embedder_name*'s credential.

    Empty when the embedder needs no key (``ollama``, ``mock``), which callers
    use to skip the whole resolution rather than report a missing key for a
    backend that never wanted one.
    """
    return _EMBEDDER_KEY_ENV.get(embedder_name, ())


def resolve_embedder_api_key(embedder_name: str, repo_path: Any = None) -> KeyLookup:
    """Find *embedder_name*'s API key, in the order the MCP server uses.

    1. the process environment — an exported key is an explicit override and
       nothing may outrank it;
    2. ``<repo>/.repowise/.env`` — where ``save_config`` persists the key at
       init time, read without merging it into ``os.environ``;
    3. ``~/.repowise/config.yaml``'s ``embedder_api_key``, and only when its
       ``embedder`` names this same backend. Handing an OpenAI key to Gemini
       fails in a way that reads as a bad key rather than a mismatched one.
    """
    env_vars = embedder_key_env_vars(embedder_name)
    if not env_vars:
        return KeyLookup(None, ())

    searched: list[str] = [" or ".join(env_vars) + " in the environment"]
    for var in env_vars:
        value = os.environ.get(var)
        if value:
            return KeyLookup(value, tuple(searched))

    if repo_path is not None:
        searched.append(f"{Path(repo_path) / '.repowise' / '.env'}")
        try:
            from repowise.core.repo_config import load_repo_env

            overlay = load_repo_env(repo_path)
        except Exception:
            overlay = {}
        for var in env_vars:
            value = overlay.get(var)
            if value:
                return KeyLookup(value, tuple(searched))

    global_config = Path.home() / ".repowise" / "config.yaml"
    searched.append(f"embedder_api_key in {global_config}")
    try:
        if global_config.is_file():
            import yaml  # type: ignore[import-untyped]

            cfg = yaml.safe_load(global_config.read_text(encoding="utf-8")) or {}
            if str(cfg.get("embedder") or "").strip().lower() == embedder_name:
                key = str(cfg.get("embedder_api_key") or "").strip()
                if key:
                    return KeyLookup(key, tuple(searched))
    except Exception:
        pass

    return KeyLookup(None, tuple(searched))


__all__ = ["KeyLookup", "embedder_key_env_vars", "resolve_embedder_api_key"]
