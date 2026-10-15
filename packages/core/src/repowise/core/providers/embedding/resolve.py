"""Which embedder a repo uses, its credential, and its constructor kwargs.

Shared by the CLI, the HTTP server and the MCP server, which each used to keep
their own env-var table and precedence. Unlike the LLM provider, the embedder
pin in ``config.yaml`` names the backend that *wrote* the vector table, and an
exported ``REPOWISE_EMBEDDER`` stays the documented override for after a
manual re-embed, so the order here is:

flag > ``REPOWISE_EMBEDDER`` > the repo's ``embedder`` pin > detection from
the keys present > ``~/.repowise/config.yaml``'s ``embedder`` > ``mock``.

Credentials: process env > the repo's ``.repowise/.env`` >
``~/.repowise/config.yaml``'s ``embedder_api_key`` (only when its ``embedder``
names the same backend). File reads go through the LLM resolver's
content-keyed cache.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

from repowise.core.providers.embedding.registry import _BUILTIN_EMBEDDERS, DEFAULT_EMBEDDER
from repowise.core.providers.llm.registry import EnvLookup
from repowise.core.providers.llm.resolve import (
    Step,
    cached_read,
    first_found,
    found,
    read_repo_config,
    read_repo_env,
    repo_getenv,
)
from repowise.core.providers.llm.specs import PROVIDER_SPECS

logger = logging.getLogger(__name__)

#: Keyed embedders and the env vars carrying their credential. A built-in
#: embedder reads the same key as the LLM provider of the same name; the first
#: var is the canonical one the CLI persists. Absent: needs no key.
EMBEDDER_KEY_ENVS: dict[str, tuple[str, ...]] = {
    name: PROVIDER_SPECS[name].api_key_envs
    for name in _BUILTIN_EMBEDDERS
    if name in PROVIDER_SPECS and PROVIDER_SPECS[name].api_key_envs
}

# Detection order when nothing names an embedder, and the env var that counts
# as evidence for each. Ollama needs no key, so an embedding model is its signal.
_DETECT_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gemini", EMBEDDER_KEY_ENVS["gemini"]),
    ("openai", EMBEDDER_KEY_ENVS["openai"]),
    ("openrouter", EMBEDDER_KEY_ENVS["openrouter"]),
    ("ollama", ("OLLAMA_EMBEDDING_MODEL",)),
    ("edenai", EMBEDDER_KEY_ENVS["edenai"]),
)


def global_config_path() -> Path:
    """``~/.repowise/config.yaml``, resolved per call so tests can move home."""
    return Path.home() / ".repowise" / "config.yaml"


def _parse_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    # A hand-edited file holding a scalar or a list parses fine; ignore it.
    return data if isinstance(data, dict) else {}


def read_global_embedder_config() -> Mapping[str, Any]:
    """``~/.repowise/config.yaml`` as a mapping, empty when absent or not one."""
    return cached_read(global_config_path(), _parse_yaml_mapping)


def global_config_embedder() -> str | None:
    """The backend pinned in ``~/.repowise/config.yaml``, if it can be built.

    ``None`` for ``mock`` (what a keyless run persists, not a choice) and for
    a keyed backend saved without its key.
    """
    cfg = read_global_embedder_config()
    name = str(cfg.get("embedder") or "").strip().lower()
    if not name or name == "mock":
        return None
    if name in EMBEDDER_KEY_ENVS and not str(cfg.get("embedder_api_key") or "").strip():
        return None
    return name


def detect_embedder(getenv: EnvLookup) -> str | None:
    """The first embedder whose credential (or ollama model) is present."""
    for name, env_vars in _DETECT_SIGNALS:
        if any(getenv(var) for var in env_vars):
            return name
    return None


def resolve_embedder_name(
    flag: str | None = None,
    *,
    repo_path: Path | str | None = None,
    getenv: EnvLookup | None = None,
    use_pin: bool = True,
    detect: bool = True,
) -> tuple[str, str]:
    """``(name, source)`` for the embedder, in the order the module states.

    ``use_pin=False`` skips the repo's pin (``init`` choosing afresh), and
    ``detect=False`` stops before key detection and the global file (a reader
    that must not guess a backend the index was not built with).
    """
    lookup = getenv or repo_getenv(repo_path)
    steps: list[Step] = [
        lambda: found(flag, "flag"),
        lambda: found(lookup("REPOWISE_EMBEDDER"), "env:REPOWISE_EMBEDDER"),
    ]
    if use_pin and repo_path is not None:
        steps.append(lambda: found(read_repo_config(repo_path).get("embedder"), "config.yaml"))
    if detect:
        steps.append(lambda: found(detect_embedder(lookup), "autodetect"))
        steps.append(lambda: found(global_config_embedder(), "global"))
    name, source = first_found(steps)
    return (name.lower(), source) if name and source else (DEFAULT_EMBEDDER, "default")


class KeyLookup(NamedTuple):
    """The resolved key, and the places consulted, for a degraded-run message."""

    key: str | None
    searched: tuple[str, ...]


def resolve_embedder_key(name: str, repo_path: Path | str | None = None) -> KeyLookup:
    """Find *name*'s API key: process env > repo ``.env`` > global config."""
    env_vars = EMBEDDER_KEY_ENVS.get(name, ())
    if not env_vars:
        return KeyLookup(None, ())

    places: list[tuple[str, Callable[[], str | None]]] = [
        (" or ".join(env_vars) + " in the environment", lambda: _first_var(os.environ, env_vars))
    ]
    if repo_path is not None:
        places.append(
            (
                str(Path(repo_path) / ".repowise" / ".env"),
                lambda: _first_var(read_repo_env(repo_path), env_vars),
            )
        )
    places.append((f"embedder_api_key in {global_config_path()}", lambda: _global_key(name)))

    searched: list[str] = []
    for place, lookup in places:
        searched.append(place)
        key = lookup()
        if key:
            return KeyLookup(key, tuple(searched))
    return KeyLookup(None, tuple(searched))


def _first_var(source: Mapping[str, str], env_vars: tuple[str, ...]) -> str | None:
    for var in env_vars:
        value = (source.get(var) or "").strip()
        if value:
            return value
    return None


def _global_key(name: str) -> str | None:
    """The global config's key, only when it was saved for this same backend.

    An openai key handed to gemini fails in a way that reads as a bad key,
    not a mismatched one.
    """
    cfg = read_global_embedder_config()
    if str(cfg.get("embedder") or "").strip().lower() != name:
        return None
    return str(cfg.get("embedder_api_key") or "").strip() or None


def _positive_int(var: str) -> int | None:
    raw = os.environ.get(var)
    if not raw:
        return None
    try:
        value = int(raw)
    except (ValueError, OverflowError):
        value = 0
    if value > 0:
        return value
    logger.warning("embedding_dims_invalid", extra={"var": var, "value": raw})
    print(
        f"{var}={raw!r} is not a positive integer; using the embedder's default width.",
        file=sys.stderr,
    )
    return None


def embedding_model_for(name: str) -> str | None:
    """The configured embedding model for *name*, or ``None`` for its default."""
    if name == "ollama":
        return os.environ.get("OLLAMA_EMBEDDING_MODEL") or os.environ.get(
            "REPOWISE_EMBEDDING_MODEL"
        )
    return os.environ.get("REPOWISE_EMBEDDING_MODEL")


def _ollama_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    base_url = os.environ.get("OLLAMA_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    dims = _positive_int("OLLAMA_EMBEDDING_DIMS") or _positive_int("REPOWISE_EMBEDDING_DIMS")
    if dims:
        kwargs["dimensions"] = dims
    return kwargs


def _gemini_kwargs() -> dict[str, Any]:
    # Gemini spells the width ``output_dimensionality``; openai and ollama
    # read REPOWISE_EMBEDDING_DIMS in their own constructors.
    dims = _positive_int("REPOWISE_EMBEDDING_DIMS")
    return {"output_dimensionality": dims} if dims else {}


#: Constructor arguments only some embedders take.
_EXTRA_KWARGS: dict[str, Callable[[], dict[str, Any]]] = {
    "ollama": _ollama_kwargs,
    "gemini": _gemini_kwargs,
}


def embedder_kwargs(name: str, repo_path: Path | str | None = None) -> dict[str, Any]:
    """Constructor kwargs for *name*: model, width, endpoint, key.

    Only what is configured is passed; everything else falls through to the
    embedder's own defaults, which are declared once in its constructor.
    """
    model = embedding_model_for(name)
    kwargs: dict[str, Any] = {"model": model} if model else {}
    extra = _EXTRA_KWARGS.get(name)
    if extra is not None:
        kwargs.update(extra())
    # Passed explicitly so a key in the repo's .env or the global config
    # reaches the embedder, not only an exported one.
    key = resolve_embedder_key(name, repo_path).key
    if key:
        kwargs["api_key"] = key
    return kwargs


__all__ = [
    "EMBEDDER_KEY_ENVS",
    "KeyLookup",
    "detect_embedder",
    "embedder_kwargs",
    "embedding_model_for",
    "global_config_embedder",
    "read_global_embedder_config",
    "resolve_embedder_key",
    "resolve_embedder_name",
]
