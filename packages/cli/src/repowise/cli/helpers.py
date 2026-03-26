"""Shared CLI utilities — async bridge, path resolution, state, DB setup."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, TypeVar

import click

CONFIG_FILENAME = "config.yaml"

from rich.console import Console

T = TypeVar("T")

console = Console()
err_console = Console(stderr=True)

STATE_FILENAME = "state.json"
DB_FILENAME = "wiki.db"
REPOWISE_DIR = ".repowise"


# ---------------------------------------------------------------------------
# Async bridge
# ---------------------------------------------------------------------------


def run_async(coro: Any) -> Any:
    """Run an async coroutine from synchronous Click code."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def resolve_repo_path(path: str | None) -> Path:
    """Resolve the repository root path from a CLI argument.

    If *path* is ``None``, defaults to the current working directory.
    Always returns an absolute, resolved ``Path``.
    """
    if path is None:
        return Path.cwd().resolve()
    return Path(path).resolve()


def get_repowise_dir(repo_path: Path) -> Path:
    """Return the ``.repowise/`` directory for a given repo root."""
    return repo_path / REPOWISE_DIR


def ensure_repowise_dir(repo_path: Path) -> Path:
    """Create the ``.repowise/`` directory if it does not exist and return it."""
    d = get_repowise_dir(repo_path)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def get_db_url_for_repo(repo_path: Path) -> str:
    """Return a database URL for this repo.

    If ``REPOWISE_DB_URL`` is set in the environment that URL is used (allows
    multiple repos to share a single central database).  Otherwise falls back
    to the per-repo ``<repo>/.repowise/wiki.db`` SQLite file.
    """
    env_url = os.environ.get("REPOWISE_DB_URL")
    if env_url:
        return env_url
    db_path = get_repowise_dir(repo_path) / DB_FILENAME
    return f"sqlite+aiosqlite:///{db_path}"


async def _ensure_db_async(repo_path: Path) -> tuple[Any, Any]:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        init_db,
    )

    url = get_db_url_for_repo(repo_path)
    engine = create_engine(url)
    await init_db(engine)
    session_factory = create_session_factory(engine)
    return engine, session_factory


def ensure_db(repo_path: Path) -> tuple[Any, Any]:
    """Create the DB engine, initialise the schema, and return ``(engine, session_factory)``."""
    return run_async(_ensure_db_async(repo_path))


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


def load_state(repo_path: Path) -> dict[str, Any]:
    """Load ``.repowise/state.json`` or return an empty dict if absent."""
    state_path = get_repowise_dir(repo_path) / STATE_FILENAME
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {}


def save_state(repo_path: Path, state: dict[str, Any]) -> None:
    """Write *state* to ``.repowise/state.json``."""
    ensure_repowise_dir(repo_path)
    state_path = get_repowise_dir(repo_path) / STATE_FILENAME
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def get_head_commit(repo_path: Path) -> str | None:
    """Return the HEAD commit SHA or ``None`` if not a git repo."""
    try:
        import git as gitpython

        repo = gitpython.Repo(repo_path, search_parent_directories=True)
        sha = repo.head.commit.hexsha
        repo.close()
        return sha
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Config (provider / model / embedder persisted after init)
# ---------------------------------------------------------------------------


def load_config(repo_path: Path) -> dict[str, Any]:
    """Load ``.repowise/config.yaml`` or return an empty dict if absent."""
    config_path = get_repowise_dir(repo_path) / CONFIG_FILENAME
    if not config_path.exists():
        return {}
    text = config_path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-untyped]
        return yaml.safe_load(text) or {}
    except ImportError:
        # Simple line-by-line parser for the flat key: value format we write
        result: dict[str, Any] = {}
        for line in text.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                result[k.strip()] = v.strip()
        return result


def save_config(
    repo_path: Path,
    provider: str,
    model: str,
    embedder: str,
    *,
    exclude_patterns: list[str] | None = None,
) -> None:
    """Write provider/model/embedder (and optionally exclude_patterns) to ``.repowise/config.yaml``.

    Performs a round-trip load so existing keys are preserved.
    """
    ensure_repowise_dir(repo_path)
    config_path = get_repowise_dir(repo_path) / CONFIG_FILENAME

    # Round-trip: preserve any existing keys (e.g. exclude_patterns set via CLI)
    existing = load_config(repo_path)
    existing["provider"] = provider
    existing["model"] = model
    existing["embedder"] = embedder
    if exclude_patterns is not None:
        existing["exclude_patterns"] = exclude_patterns

    try:
        import yaml  # type: ignore[import-untyped]
        config_path.write_text(
            yaml.dump(existing, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
    except ImportError:
        # Fallback: write simple key-value format (lists not supported)
        lines = [f"provider: {provider}", f"model: {model}", f"embedder: {embedder}"]
        config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------


def resolve_provider(
    provider_name: str | None,
    model: str | None,
    repo_path: Path | None = None,
) -> Any:
    """Resolve a provider instance from CLI flags or environment variables.

    Resolution order:
      1. Explicit ``--provider`` flag
      2. ``REPOWISE_PROVIDER`` env var
      3. ``.repowise/config.yaml`` (written by ``repowise init``)
      4. Auto-detect from API key env vars
    """
    import os

    from repowise.core.providers import get_provider

    if provider_name is None:
        provider_name = os.environ.get("REPOWISE_PROVIDER")

    if provider_name is None and repo_path is not None:
        cfg = load_config(repo_path)
        if cfg.get("provider"):
            provider_name = cfg["provider"]
            if model is None and cfg.get("model"):
                model = cfg["model"]

    if provider_name is not None:
        kwargs: dict[str, Any] = {}
        if model:
            kwargs["model"] = model
        return get_provider(provider_name, **kwargs)

    # Auto-detect from env vars
    if os.environ.get("ANTHROPIC_API_KEY"):
        kwargs = {"model": model} if model else {}
        return get_provider("anthropic", **kwargs)
    if os.environ.get("OPENAI_API_KEY"):
        kwargs = {"model": model} if model else {}
        return get_provider("openai", **kwargs)
    if os.environ.get("OLLAMA_BASE_URL"):
        kwargs = {"model": model} if model else {}
        return get_provider("ollama", **kwargs)
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        kwargs = {"model": model} if model else {}
        return get_provider("gemini", **kwargs)

    raise click.ClickException(
        "No provider configured. Use --provider, set REPOWISE_PROVIDER, "
        "or set ANTHROPIC_API_KEY / OPENAI_API_KEY / OLLAMA_BASE_URL / GEMINI_API_KEY."
    )
