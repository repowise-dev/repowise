"""Shared CLI utilities — async bridge, path resolution, state, DB setup."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, TypeVar

import click
from rich.console import Console

CONFIG_FILENAME = "config.yaml"

T = TypeVar("T")

console = Console()
err_console = Console(stderr=True)

STATE_FILENAME = "state.json"
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


def find_workspace_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) looking for ``.repowise-workspace.yaml``.

    Returns the directory containing the file, or ``None`` if not found.
    Delegates to :func:`repowise.core.workspace.config.find_workspace_root`.
    """
    from repowise.core.workspace.config import find_workspace_root as _find

    return _find(start)


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

    Prefers ``REPOWISE_DB_URL``, then the legacy ``REPOWISE_DATABASE_URL``.
    Otherwise defaults to the repo-local ``<repo>/.repowise/wiki.db``.
    """
    from repowise.core.persistence.database import resolve_db_url

    return resolve_db_url(repo_path)


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
    commit_limit: int | None = None,
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
    if commit_limit is not None:
        existing["commit_limit"] = commit_limit

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
        # Validate configuration before attempting to create provider
        warnings = validate_provider_config(provider_name)
        if warnings:
            for warning in warnings:
                err_console.print(f"[yellow]Warning:[/yellow] {warning}")
            # For explicit provider requests, we still try to create it
            # The provider constructor will fail if the API key is actually required

        kwargs: dict[str, Any] = {}
        if model:
            kwargs["model"] = model

        # Pass API key from environment if available
        if provider_name == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
            kwargs["api_key"] = os.environ["ANTHROPIC_API_KEY"]
        elif provider_name == "openai" and os.environ.get("OPENAI_API_KEY"):
            kwargs["api_key"] = os.environ["OPENAI_API_KEY"]
        elif provider_name == "gemini" and (
            os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        ):
            kwargs["api_key"] = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        elif provider_name == "ollama" and os.environ.get("OLLAMA_BASE_URL"):
            kwargs["base_url"] = os.environ["OLLAMA_BASE_URL"]
        elif provider_name == "litellm":
            # LiteLLM: API key for cloud, base URL for local proxy
            if os.environ.get("LITELLM_API_KEY"):
                kwargs["api_key"] = os.environ["LITELLM_API_KEY"]
            if os.environ.get("LITELLM_BASE_URL"):
                kwargs["api_base"] = os.environ["LITELLM_BASE_URL"]
        elif provider_name == "zai":
            # Z.AI: API key, plan, base URL, and thinking mode
            if os.environ.get("ZAI_API_KEY"):
                kwargs["api_key"] = os.environ["ZAI_API_KEY"]
            if os.environ.get("ZAI_PLAN"):
                kwargs["plan"] = os.environ["ZAI_PLAN"]
            if os.environ.get("ZAI_BASE_URL"):
                kwargs["base_url"] = os.environ["ZAI_BASE_URL"]
            if os.environ.get("ZAI_THINKING"):
                kwargs["thinking"] = os.environ["ZAI_THINKING"]
            if os.environ.get("ZAI_TIER"):
                kwargs["tier"] = os.environ["ZAI_TIER"]

        return get_provider(provider_name, **kwargs)

    # Auto-detect from env vars
    if os.environ.get("ANTHROPIC_API_KEY") and os.environ["ANTHROPIC_API_KEY"].strip():
        kwargs = (
            {"model": model, "api_key": os.environ["ANTHROPIC_API_KEY"]}
            if model
            else {"api_key": os.environ["ANTHROPIC_API_KEY"]}
        )
        return get_provider("anthropic", **kwargs)
    if os.environ.get("OPENAI_API_KEY") and os.environ["OPENAI_API_KEY"].strip():
        kwargs = (
            {"model": model, "api_key": os.environ["OPENAI_API_KEY"]}
            if model
            else {"api_key": os.environ["OPENAI_API_KEY"]}
        )
        return get_provider("openai", **kwargs)
    if os.environ.get("OLLAMA_BASE_URL") and os.environ["OLLAMA_BASE_URL"].strip():
        kwargs = (
            {"model": model, "base_url": os.environ["OLLAMA_BASE_URL"]}
            if model
            else {"base_url": os.environ["OLLAMA_BASE_URL"]}
        )
        return get_provider("ollama", **kwargs)
    if (os.environ.get("GEMINI_API_KEY") and os.environ["GEMINI_API_KEY"].strip()) or (
        os.environ.get("GOOGLE_API_KEY") and os.environ["GOOGLE_API_KEY"].strip()
    ):
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        kwargs = {"model": model, "api_key": api_key} if model else {"api_key": api_key}
        return get_provider("gemini", **kwargs)
    # LiteLLM: check for API key (cloud) or base URL (local proxy)
    if os.environ.get("LITELLM_API_KEY") and os.environ["LITELLM_API_KEY"].strip():
        kwargs = (
            {"model": model, "api_key": os.environ["LITELLM_API_KEY"]}
            if model
            else {"api_key": os.environ["LITELLM_API_KEY"]}
        )
        return get_provider("litellm", **kwargs)
    if os.environ.get("LITELLM_BASE_URL") and os.environ["LITELLM_BASE_URL"].strip():
        kwargs = (
            {"model": model, "api_base": os.environ["LITELLM_BASE_URL"]}
            if model
            else {"api_base": os.environ["LITELLM_BASE_URL"]}
        )
        return get_provider("litellm", **kwargs)
    # Z.AI: check for API key
    if os.environ.get("ZAI_API_KEY") and os.environ["ZAI_API_KEY"].strip():
        kwargs = {"api_key": os.environ["ZAI_API_KEY"]}
        if model:
            kwargs["model"] = model
        if os.environ.get("ZAI_PLAN"):
            kwargs["plan"] = os.environ["ZAI_PLAN"]
        if os.environ.get("ZAI_BASE_URL"):
            kwargs["base_url"] = os.environ["ZAI_BASE_URL"]
        if os.environ.get("ZAI_THINKING"):
            kwargs["thinking"] = os.environ["ZAI_THINKING"]
        if os.environ.get("ZAI_TIER"):
            kwargs["tier"] = os.environ["ZAI_TIER"]
        return get_provider("zai", **kwargs)

    raise click.ClickException(
        "No provider configured. Use --provider, set REPOWISE_PROVIDER, "
        "or set ANTHROPIC_API_KEY / OPENAI_API_KEY / OLLAMA_BASE_URL / GEMINI_API_KEY / "
        "LITELLM_API_KEY / LITELLM_BASE_URL / ZAI_API_KEY."
    )


# ---------------------------------------------------------------------------
# Provider validation
# ---------------------------------------------------------------------------


def validate_provider_config(provider_name: str | None = None) -> list[str]:
    """Validate that required API keys/environment variables are set for the provider.

    Args:
        provider_name: The provider name to validate. If None, checks all possible providers.

    Returns:
        List of warning messages for missing or invalid configuration.
        Empty list means all required config is present.
    """
    warnings = []

    def _is_env_var_set(var_name: str) -> bool:
        """Check if environment variable is set and non-empty."""
        value = os.environ.get(var_name)
        return value is not None and value.strip() != ""

    def _is_env_var_exists(var_name: str) -> bool:
        """Check if environment variable exists (even if empty)."""
        return var_name in os.environ

    # Define required environment variables for each provider
    provider_env_vars = {
        "anthropic": ["ANTHROPIC_API_KEY"],
        "openai": ["OPENAI_API_KEY"],
        "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],  # Either one
        "ollama": ["OLLAMA_BASE_URL"],
        "litellm": [
            "LITELLM_API_KEY",
            "LITELLM_BASE_URL",
        ],  # Either one (API key for cloud, base URL for local)
        "zai": ["ZAI_API_KEY"],
    }

    if provider_name:
        # Validate specific provider
        if provider_name not in provider_env_vars:
            warnings.append(f"Unknown provider '{provider_name}' - cannot validate configuration")
            return warnings

        env_vars = provider_env_vars[provider_name]
        missing_vars = []

        if provider_name == "gemini":
            # Special case: either GEMINI_API_KEY or GOOGLE_API_KEY
            if not (_is_env_var_set("GEMINI_API_KEY") or _is_env_var_set("GOOGLE_API_KEY")):
                missing_vars = env_vars
        elif provider_name == "litellm":
            # Special case: LITELLM_API_KEY (cloud) OR LITELLM_BASE_URL (local proxy)
            if not (_is_env_var_set("LITELLM_API_KEY") or _is_env_var_set("LITELLM_BASE_URL")):
                missing_vars = env_vars
        else:
            for var in env_vars:
                if not _is_env_var_set(var):
                    missing_vars.append(var)

        if missing_vars:
            warnings.append(
                f"Provider '{provider_name}' requires environment variables: {', '.join(missing_vars)}"
            )
    else:
        # Check all providers - warn about any that could be configured but are missing keys
        for name, env_vars in provider_env_vars.items():
            if name == "gemini":
                if os.environ.get("REPOWISE_PROVIDER") == "gemini" and not (
                    _is_env_var_set("GEMINI_API_KEY") or _is_env_var_set("GOOGLE_API_KEY")
                ):
                    # Only warn if it looks like they might be trying to use gemini
                    warnings.append(
                        "Provider 'gemini' requires GEMINI_API_KEY or GOOGLE_API_KEY environment variable"
                    )
                continue

            if name == "litellm":
                # Special case: LITELLM_API_KEY (cloud) OR LITELLM_BASE_URL (local proxy)
                # Only warn if explicitly requested and neither is set
                if os.environ.get("REPOWISE_PROVIDER") == "litellm" and not (
                    _is_env_var_set("LITELLM_API_KEY") or _is_env_var_set("LITELLM_BASE_URL")
                ):
                    warnings.append(
                        "Provider 'litellm' requires LITELLM_API_KEY or LITELLM_BASE_URL environment variable"
                    )
                continue

            missing = [var for var in env_vars if not _is_env_var_set(var)]
            if missing:
                # Only warn if this provider is explicitly requested OR
                # if the env var exists but is invalid (empty)
                env_var_exists = any(_is_env_var_exists(var) for var in env_vars)
                explicitly_requested = os.environ.get("REPOWISE_PROVIDER") == name

                if explicitly_requested or env_var_exists:
                    warnings.append(
                        f"Provider '{name}' requires environment variables: {', '.join(missing)}"
                    )

    return warnings
