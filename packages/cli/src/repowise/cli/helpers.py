"""Shared CLI utilities — async bridge, path resolution, state, DB setup."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

import click
from rich.console import Console

from repowise.core.reasoning import (
    ReasoningMode,
)
from repowise.core.reasoning import (
    resolve_reasoning as resolve_core_reasoning,
)
from repowise.core.repo_config import CONFIG_FILENAME, load_repo_config

# Update lock — coordinates concurrent `repowise update` invocations and lets
# the augment hook suppress stale-wiki warnings while a refresh is in flight.
# One shared implementation in core (the workspace updater used to carry a
# hand-synced copy); re-exported here for the existing CLI/hook imports.
from repowise.core.update_lock import (
    UPDATE_LOCK_FILENAME as UPDATE_LOCK_FILENAME,
)
from repowise.core.update_lock import (
    UPDATE_LOCK_STALE_AFTER_SECONDS as UPDATE_LOCK_STALE_AFTER_SECONDS,
)
from repowise.core.update_lock import (
    read_update_lock as read_update_lock,
)
from repowise.core.update_lock import (
    release_update_lock as release_update_lock,
)
from repowise.core.update_lock import (
    try_acquire_update_lock as try_acquire_update_lock,
)

T = TypeVar("T")

console = Console()
err_console = Console(stderr=True)

STATE_FILENAME = "state.json"
REPOWISE_DIR = ".repowise"


# ---------------------------------------------------------------------------
# Logging / structlog helpers
# ---------------------------------------------------------------------------


def silence_logs_for_machine_output() -> None:
    """Suppress info/debug log output when stdout is machine-readable (JSON/md).

    Structlog and stdlib loggers write to stdout by default. When a command
    emits JSON or Markdown, those lines corrupt the output for downstream
    consumers (e.g. ``repowise health --format json | jq .kpis``).

    Call this at the top of any command that supports ``--format json`` or
    ``--format md`` before the ingestion pipeline starts.
    """
    import logging

    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("httpcore").setLevel(logging.ERROR)
    for _name in ("repowise.core", "repowise.server"):
        logging.getLogger(_name).setLevel(logging.ERROR)
    try:
        import structlog

        # cache_logger_on_first_use=False is required: module-level
        # ``structlog.get_logger`` calls snapshot the logger before configure()
        # runs and would bypass this filter without it.
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR),
            cache_logger_on_first_use=False,
        )
    except ImportError:
        pass


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


def find_repowise_repo_root(start: Path | None = None) -> Path | None:
    """Walk upward from *start* looking for a repo with ``.repowise``."""

    current = (start or Path.cwd()).resolve()
    home = Path.home().resolve()
    for candidate in (current, *current.parents):
        if _same_path(candidate, home):
            return None
        if (candidate / REPOWISE_DIR).is_dir():
            return candidate
    return None


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        return left == right


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


def user_global_dir() -> Path:
    """Return the user-global ``~/.repowise`` dir (created), for cross-repo state.

    Home to machine-wide, repo-independent artifacts: the cached web bundle, the
    PyPI update-check cache, and the last-seen release marker. Distinct from a
    repo's local ``.repowise/`` store.
    """
    d = Path.home() / REPOWISE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


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
    """Write *state* to ``.repowise/state.json``.

    Every persist stamps the store-format markers (``store_format_version`` and
    the ``written_by_version`` package version that wrote it) so the upgrade
    layer always has a current record of the store's shape and provenance.
    """
    ensure_repowise_dir(repo_path)
    try:
        from repowise.cli import __version__ as _pkg_version
        from repowise.core.upgrade import stamp as _stamp_store_version

        _stamp_store_version(state, package_version=_pkg_version)
    except Exception:  # never let stamping block a persist
        pass
    from repowise.core.fsutils import atomic_write_text

    # Atomic so a crash mid-write can never leave a truncated state.json —
    # every later update would fail to parse it and demand a full re-init.
    state_path = get_repowise_dir(repo_path) / STATE_FILENAME
    atomic_write_text(state_path, json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Queued / pending markers — coordinate the post-commit hook with a running
# update so rapid-fire commits don't spawn N concurrent updates that race
# on save_state. Two distinct markers, deliberately:
#
#   ``.update.queued``  : written by the hook BEFORE backgrounding repowise
#                         update. Closes the race window between commit and
#                         lock acquisition — the augment hook reads this
#                         and suppresses its warning the moment the queued
#                         file appears, not 30+ seconds later when the
#                         actual lock file lands on disk.
#
#   ``.update.pending`` : written by a *new* update_cmd invocation when it
#                         finds an in-flight lock. Carries the latest HEAD
#                         so the running update can roll forward to it at
#                         the end of its current pass instead of stopping
#                         at a stale commit.
#
# Both markers are best-effort: failure to write/read them must never break
# update_cmd itself, only degrade the coalescing behaviour to "spawn but
# bail" (slightly noisier in the augment hook but still correct).
# ---------------------------------------------------------------------------

UPDATE_QUEUED_FILENAME = ".update.queued"
UPDATE_PENDING_FILENAME = ".update.pending"

# A ``.update.queued`` marker older than this is treated as stale — most
# likely a crashed hook that wrote the marker but never spawned the update.
# Short enough to avoid suppressing genuinely-stale warnings indefinitely.
UPDATE_QUEUED_STALE_AFTER_SECONDS = 5 * 60


def _update_queued_path(repo_path: Path) -> Path:
    return get_repowise_dir(repo_path) / UPDATE_QUEUED_FILENAME


def _update_pending_path(repo_path: Path) -> Path:
    return get_repowise_dir(repo_path) / UPDATE_PENDING_FILENAME


def write_update_queued(repo_path: Path, head: str | None) -> None:
    """Mark that an update has been spawned for ``head``.

    Called from the post-commit hook *before* backgrounding ``repowise
    update`` so the augment hook can suppress its stale-wiki warning during
    the brief window where the actual update process is still starting up
    (Python import, DB open, etc.) and hasn't yet written its own lock file.
    """
    import time

    try:
        ensure_repowise_dir(repo_path)
    except OSError:
        return
    payload = {"target_commit": head, "queued_at": time.time()}
    try:
        _update_queued_path(repo_path).write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def read_update_queued(repo_path: Path) -> dict[str, Any] | None:
    """Return queued payload if fresh (≤ ``UPDATE_QUEUED_STALE_AFTER_SECONDS``)."""
    import time

    path = _update_queued_path(repo_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    queued_at = payload.get("queued_at")
    if not isinstance(queued_at, (int, float)):
        return None
    if time.time() - queued_at > UPDATE_QUEUED_STALE_AFTER_SECONDS:
        return None
    return payload


def clear_update_queued(repo_path: Path) -> None:
    """Drop the queued marker. Called by update_cmd once it owns the real lock."""
    try:
        _update_queued_path(repo_path).unlink(missing_ok=True)
    except OSError:
        pass


def write_update_pending(repo_path: Path, head: str | None) -> None:
    """Record that another commit landed while an update was in flight.

    The running update reads this at the end of its pass and rolls forward
    to the new HEAD in one extra round, avoiding the failure mode where a
    rapid burst of commits leaves the wiki indexed to an outdated commit.
    """
    if head is None:
        return
    try:
        ensure_repowise_dir(repo_path)
    except OSError:
        return
    try:
        _update_pending_path(repo_path).write_text(head, encoding="utf-8")
    except OSError:
        pass


def read_update_pending(repo_path: Path) -> str | None:
    """Return the pending HEAD if any, else None."""
    path = _update_pending_path(repo_path)
    if not path.exists():
        return None
    try:
        head = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return head or None


def clear_update_pending(repo_path: Path) -> None:
    """Drop the pending marker once the rolled-forward update has consumed it."""
    try:
        _update_pending_path(repo_path).unlink(missing_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Hook output log — capped, single-file rotation so the user can diagnose
# why the post-commit hook didn't catch up without needing to chase down a
# silent subprocess. Cap is deliberately small: a few recent runs is enough
# context, and we don't want a runaway log to fill the .repowise/ dir.
# ---------------------------------------------------------------------------

UPDATE_LOG_FILENAME = ".update.log"

# Truncate the log when it grows past this size, keeping the tail.
UPDATE_LOG_MAX_BYTES = 256 * 1024
# After truncation, retain at most this much of the prior tail.
UPDATE_LOG_KEEP_TAIL_BYTES = 64 * 1024


def update_log_path(repo_path: Path) -> Path:
    return get_repowise_dir(repo_path) / UPDATE_LOG_FILENAME


def rotate_update_log_if_needed(repo_path: Path) -> None:
    """Truncate ``.update.log`` if it has grown past the size cap.

    Called opportunistically from the hook before piping a new run's output
    in. We use simple in-place truncation (rewrite the tail) rather than
    renaming, because the post-commit hook can fire in parallel with a
    `repowise update` that may still be writing — a rename would orphan
    the writer's file descriptor on POSIX and outright fail on Windows.
    """
    path = update_log_path(repo_path)
    try:
        if not path.exists() or path.stat().st_size <= UPDATE_LOG_MAX_BYTES:
            return
        with path.open("rb") as f:
            f.seek(-UPDATE_LOG_KEEP_TAIL_BYTES, 2)
            tail = f.read()
        path.write_bytes(b"... (log truncated) ...\n" + tail)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def get_head_commit(repo_path: Path) -> str | None:
    """Return the HEAD commit SHA or ``None`` if not a git repo.

    Delegates to the core implementation (``git rev-parse HEAD``) so the CLI
    and the workspace updater resolve HEAD identically — the old gitpython
    version here could diverge on worktree/detached-HEAD edge cases.
    """
    from repowise.core.workspace.update import get_head_commit as _core_head

    return _core_head(Path(repo_path))


# ---------------------------------------------------------------------------
# Config (provider / model / embedder persisted after init)
# ---------------------------------------------------------------------------


def load_config(repo_path: Path) -> dict[str, Any]:
    """Load ``.repowise/config.yaml`` or return an empty dict if absent."""
    return load_repo_config(repo_path)


def resolve_reasoning(
    reasoning: str | None = None,
    config: dict[str, Any] | None = None,
) -> ReasoningMode:
    """Resolve generation reasoning from CLI flag, env, config, then default."""
    try:
        return resolve_core_reasoning(reasoning, config)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc


def save_config(
    repo_path: Path,
    provider: str,
    model: str,
    embedder: str,
    *,
    embedding_model: str | None = None,
    exclude_patterns: list[str] | None = None,
    commit_limit: int | None = None,
    reasoning: str | None = None,
) -> None:
    """Write provider/model/embedder (and optionally exclude_patterns) to ``.repowise/config.yaml``.

    Performs a round-trip load so existing keys are preserved.

    ``embedding_model`` is persisted so ``repowise serve`` can rebuild the same
    embedder used at init time — without it the server silently falls back to a
    provider default (e.g. ``text-embedding-3-small``), which mismatches the
    indexed vectors and breaks chat/search retrieval (issue #426).
    """
    ensure_repowise_dir(repo_path)
    config_path = get_repowise_dir(repo_path) / CONFIG_FILENAME

    # Round-trip: preserve any existing keys (e.g. exclude_patterns set via CLI)
    existing = load_config(repo_path)
    existing["provider"] = provider
    existing["model"] = model
    existing["embedder"] = embedder
    if embedding_model:
        existing["embedding_model"] = embedding_model
    if exclude_patterns is not None:
        existing["exclude_patterns"] = exclude_patterns
    if commit_limit is not None:
        existing["commit_limit"] = commit_limit
    if reasoning is not None:
        existing["reasoning"] = resolve_reasoning(reasoning)

    try:
        import yaml  # type: ignore[import-untyped]

        config_path.write_text(
            yaml.dump(existing, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
    except ImportError:
        # Fallback: write simple key-value format (lists not supported)
        lines = [f"provider: {provider}", f"model: {model}", f"embedder: {embedder}"]
        if embedding_model:
            lines.append(f"embedding_model: {embedding_model}")
        if reasoning is not None:
            lines.append(f"reasoning: {resolve_reasoning(reasoning)}")
        config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_config_partial(
    repo_path: Path,
    *,
    exclude_patterns: list[str] | None = None,
    commit_limit: int | None = None,
    **extra: Any,
) -> None:
    """Merge optional keys into ``.repowise/config.yaml``, preserving existing keys.

    ``exclude_patterns`` / ``commit_limit`` are explicit for the common case;
    any other config keys (e.g. ``enable_onboarding=False``) can be passed as
    keyword arguments. ``None`` values are skipped so callers can forward
    optional flags without clobbering existing keys.

    No scalar-only fallback like :func:`save_config`: it would silently drop
    ``exclude_patterns``, and PyYAML is a hard dependency anyway.
    """
    import yaml  # type: ignore[import-untyped]

    updates: dict[str, Any] = {}
    if exclude_patterns is not None:
        updates["exclude_patterns"] = exclude_patterns
    if commit_limit is not None:
        updates["commit_limit"] = commit_limit
    updates.update({k: v for k, v in extra.items() if v is not None})
    if not updates:
        return

    ensure_repowise_dir(repo_path)
    config_path = get_repowise_dir(repo_path) / CONFIG_FILENAME
    existing = load_config(repo_path)
    existing.update(updates)

    config_path.write_text(
        yaml.dump(existing, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def save_distill_commands_enabled(repo_path: Path, *, enabled: bool) -> None:
    """Deep-merge ``distill.commands.enabled`` into ``.repowise/config.yaml``.

    :func:`save_config_partial` merges shallowly at the top level, so the
    ``distill`` block is merged here first to avoid clobbering sibling keys
    like ``disabled_filters``.
    """
    cfg = load_config(repo_path)
    distill = dict(cfg.get("distill") or {})
    commands = dict(distill.get("commands") or {})
    commands["enabled"] = enabled
    distill["commands"] = commands
    save_config_partial(repo_path, distill=distill)


def config_fingerprint(repo_path: Path) -> str:
    """SHA-256 hex of ``.repowise/config.yaml`` + ``health-rules.json`` content.

    Delegates to the shared core implementation so CLI runs and server jobs
    compute identical fingerprints for the same on-disk config.
    """
    from repowise.core.repo_config import config_fingerprint as _core_fingerprint

    return _core_fingerprint(repo_path)


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------


def _is_codex_cli_available() -> bool:
    """Check if the Codex CLI binary is available."""

    import shutil

    return shutil.which("codex") is not None


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

    cfg: dict[str, Any] = {}
    if repo_path is not None:
        cfg = load_config(repo_path)

    if provider_name is None:
        provider_name = os.environ.get("REPOWISE_PROVIDER")

    if provider_name is None and cfg.get("provider"):
        provider_name = cfg["provider"]

    # Honor the config model regardless of how the provider was resolved (#416).
    if model is None and cfg.get("model"):
        model = cfg["model"]

    def _resolve_base_url(name: str) -> str | None:
        """Return base_url from env or repo config for the provider."""
        env_vars = {
            "anthropic": ["ANTHROPIC_BASE_URL"],
            "openai": ["OPENAI_BASE_URL"],
            "gemini": ["GEMINI_BASE_URL"],
            "deepseek": ["DEEPSEEK_BASE_URL"],
            "ollama": ["OLLAMA_BASE_URL"],
            "litellm": ["LITELLM_BASE_URL", "LITELLM_API_BASE"],
        }
        for var in env_vars.get(name, []):
            val = os.environ.get(var)
            if val:
                return val
        section = cfg.get(name)
        if isinstance(section, dict):
            base_url = section.get("base_url")
            if base_url:
                return base_url
        return None

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
        base_url = _resolve_base_url(provider_name)
        if base_url:
            kwargs["base_url"] = base_url
        if provider_name == "codex_cli" and repo_path is not None:
            kwargs["repo_path"] = repo_path
        if provider_name == "opencode" and repo_path is not None:
            kwargs["repo_path"] = repo_path

        # Pass API key from environment if available
        if provider_name == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
            kwargs["api_key"] = os.environ["ANTHROPIC_API_KEY"]
        elif provider_name == "openai" and os.environ.get("OPENAI_API_KEY"):
            kwargs["api_key"] = os.environ["OPENAI_API_KEY"]
        elif provider_name == "gemini" and (
            os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        ):
            kwargs["api_key"] = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        elif provider_name == "openrouter" and os.environ.get("OPENROUTER_API_KEY"):
            kwargs["api_key"] = os.environ["OPENROUTER_API_KEY"]
        elif provider_name == "deepseek" and os.environ.get("DEEPSEEK_API_KEY"):
            kwargs["api_key"] = os.environ["DEEPSEEK_API_KEY"]
        elif provider_name == "litellm" and os.environ.get("LITELLM_API_KEY"):
            kwargs["api_key"] = os.environ["LITELLM_API_KEY"]
        elif provider_name == "ollama" and os.environ.get("OLLAMA_BASE_URL"):
            kwargs["base_url"] = os.environ["OLLAMA_BASE_URL"]

        return get_provider(provider_name, **kwargs)

    # Auto-detect from env vars
    if os.environ.get("ANTHROPIC_API_KEY") and os.environ["ANTHROPIC_API_KEY"].strip():
        kwargs = (
            {"model": model, "api_key": os.environ["ANTHROPIC_API_KEY"]}
            if model
            else {"api_key": os.environ["ANTHROPIC_API_KEY"]}
        )
        base_url = _resolve_base_url("anthropic")
        if base_url:
            kwargs["base_url"] = base_url
        return get_provider("anthropic", **kwargs)
    if os.environ.get("OPENAI_API_KEY") and os.environ["OPENAI_API_KEY"].strip():
        kwargs = (
            {"model": model, "api_key": os.environ["OPENAI_API_KEY"]}
            if model
            else {"api_key": os.environ["OPENAI_API_KEY"]}
        )
        base_url = _resolve_base_url("openai")
        if base_url:
            kwargs["base_url"] = base_url
        return get_provider("openai", **kwargs)
    if os.environ.get("OPENROUTER_API_KEY") and os.environ["OPENROUTER_API_KEY"].strip():
        kwargs = (
            {"model": model, "api_key": os.environ["OPENROUTER_API_KEY"]}
            if model
            else {"api_key": os.environ["OPENROUTER_API_KEY"]}
        )
        return get_provider("openrouter", **kwargs)
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
        base_url = _resolve_base_url("gemini")
        if base_url:
            kwargs["base_url"] = base_url
        return get_provider("gemini", **kwargs)
    if os.environ.get("DEEPSEEK_API_KEY") and os.environ["DEEPSEEK_API_KEY"].strip():
        kwargs = (
            {"model": model, "api_key": os.environ["DEEPSEEK_API_KEY"]}
            if model
            else {"api_key": os.environ["DEEPSEEK_API_KEY"]}
        )
        base_url = _resolve_base_url("deepseek")
        if base_url:
            kwargs["base_url"] = base_url
        return get_provider("deepseek", **kwargs)

    raise click.ClickException(
        "No provider configured. Use --provider, set REPOWISE_PROVIDER, "
        "or set ANTHROPIC_API_KEY / OPENAI_API_KEY / OPENROUTER_API_KEY / "
        "OLLAMA_BASE_URL / GEMINI_API_KEY / GOOGLE_API_KEY / DEEPSEEK_API_KEY / "
        "LITELLM_API_KEY. Use REPOWISE_PROVIDER=codex_cli to use an authenticated "
        "Codex CLI subscription, or REPOWISE_PROVIDER=opencode to use opencode."
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
        "openrouter": ["OPENROUTER_API_KEY"],
        "deepseek": ["DEEPSEEK_API_KEY"],
        "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],  # Either one
        "ollama": ["OLLAMA_BASE_URL"],
        "litellm": ["LITELLM_API_KEY"],  # May need others depending on backend
    }

    if provider_name:
        if provider_name == "codex_cli":
            if not _is_codex_cli_available():
                warnings.append(
                    "Provider 'codex_cli' requires the Codex CLI. "
                    "Install it with: npm install -g @openai/codex"
                )
            return warnings

        if provider_name == "opencode":
            import shutil

            if not shutil.which("opencode"):
                warnings.append(
                    "Provider 'opencode' requires the opencode CLI.\n"
                    "  Install:  curl -fsSL https://opencode.ai/install | bash\n"
                    "  Setup:    run 'opencode' once to configure your provider\n"
                    "  Models:   opencode models (list available models)\n"
                    "  More:     https://opencode.ai\n"
                    "  Usage:    repowise init --provider opencode --model opencode/openai/gpt-5"
                )
            return warnings

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


# ---------------------------------------------------------------------------
# Command target resolution — auto-detect single-repo vs workspace mode
# ---------------------------------------------------------------------------
#
# Many CLI commands (``update``, ``status``, ``watch``, ``generate-claude-md``,
# ``doctor``, ``costs``, ``search``, ``dead-code``, ``decision``, hooks) need
# to decide whether the user means "this one repo" or "the surrounding
# workspace". Historically each command did its own ad-hoc detection (or
# none), which produced the Phase A bug where ``repowise update`` from a
# workspace root errored with a misleading "No previous sync found" message
# and left a stray ``.repowise/`` directory behind.
#
# ``resolve_command_target`` is the single source of truth. Every command
# should call it before doing any work. See ``docs/WORKSPACE_ROBUSTNESS.md``
# for the UX principles.


@dataclass
class CommandTarget:
    """Resolved target for a CLI invocation — single repo or workspace.

    Attributes:
        mode: ``"single"`` or ``"workspace"``.
        repo_path: For single mode, the resolved repo path. For workspace
            mode, ``None`` (use ``ws_root`` + ``ws_config`` instead, or the
            ``primary_path()`` helper).
        ws_root: Workspace root path. Set in workspace mode; also set in
            single mode when a workspace exists *upstream* of the chosen
            repo, so commands can surface that context.
        ws_config: Loaded workspace config (workspace mode only).
        repo_filter: Optional alias filter for workspace mode (e.g.
            ``--repo backend``). ``None`` means "all repos".
        reason: Short human-readable explanation of why this target was
            chosen. Surfaced via :meth:`notice`.
        auto_detected: ``True`` when the workspace context was inferred
            rather than requested via an explicit flag. Used to decide
            whether to print a transparency notice.
    """

    mode: Literal["single", "workspace"]
    repo_path: Path | None = None
    ws_root: Path | None = None
    ws_config: Any | None = None  # WorkspaceConfig (avoid hard import here)
    repo_filter: str | None = None
    reason: str = ""
    auto_detected: bool = False

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def is_workspace(self) -> bool:
        return self.mode == "workspace"

    def primary_path(self) -> Path | None:
        """Return the workspace's primary repo path, if known."""
        if self.ws_config is None or self.ws_root is None:
            return None
        primary = self.ws_config.get_primary()
        if primary is None:
            return None
        return (self.ws_root / primary.path).resolve()

    def resolve_repo_alias(self, alias: str | None) -> Path | None:
        """Resolve an alias to an absolute repo path within the workspace.

        Returns ``None`` if the workspace is not loaded or the alias is
        unknown. Used by commands that accept ``--repo <alias>``.
        """
        if self.ws_config is None or self.ws_root is None or alias is None:
            return None
        entry = self.ws_config.get_repo(alias)
        if entry is None:
            return None
        return (self.ws_root / entry.path).resolve()

    # ------------------------------------------------------------------
    # Notice rendering — every command should call this so users always
    # know which mode they ended up in.
    # ------------------------------------------------------------------

    def notice(self, console_obj: Console, *, command: str = "") -> None:
        """Print a one-line transparency notice describing the chosen target.

        - Always printed when ``auto_detected`` is True.
        - Also printed when in workspace mode (even if flagged explicitly)
          so the repo list is visible at the top of the command output.
        - Silent when single-repo mode was explicitly requested.
        """
        if self.mode == "workspace":
            ws_root = self.ws_root.name if self.ws_root else "?"
            repos = len(self.ws_config.repos) if self.ws_config else 0
            if self.repo_filter:
                console_obj.print(
                    f"[dim][workspace][/dim] {command or 'running'} on "
                    f"[cyan]{self.repo_filter}[/cyan] within "
                    f"[cyan]{ws_root}[/cyan] ({repos} repos)"
                )
            else:
                console_obj.print(
                    f"[dim][workspace][/dim] {command or 'running'} across "
                    f"[cyan]{repos}[/cyan] repos in [cyan]{ws_root}[/cyan]"
                )
            if self.reason and self.auto_detected:
                console_obj.print(f"[dim]  ({self.reason})[/dim]")
            return

        # Single-repo mode — only narrate when the resolution was non-obvious.
        if self.auto_detected and self.ws_root is not None:
            # A workspace exists upstream but we chose single-repo anyway.
            console_obj.print(
                f"[dim][single-repo][/dim] targeting "
                f"[cyan]{self.repo_path}[/cyan] "
                f"(workspace also detected at [cyan]{self.ws_root}[/cyan]; "
                f"pass --workspace to run across all repos)"
            )


class WorkspaceNotFound(click.ClickException):
    """Raised when ``--workspace`` was requested but no workspace was found."""


def resolve_command_target(
    *,
    path: str | None = None,
    workspace_flag: bool = False,
    no_workspace_flag: bool = False,
    repo_alias: str | None = None,
) -> CommandTarget:
    """Resolve whether a command should operate on a single repo or workspace.

    The resolution rules (first match wins):

    1. ``--no-workspace`` → single-repo targeting ``path`` (or cwd). Hard
       override for users who want the old behavior.
    2. ``--workspace`` or ``--repo <alias>`` → workspace mode. Raises
       :class:`WorkspaceNotFound` if no workspace can be located.
    3. Explicit ``path`` argument:
       - If the path itself contains ``.repowise-workspace.yaml`` →
         workspace mode (treats the path as the workspace root).
       - Otherwise → single-repo mode targeting that path. We do *not*
         auto-promote to workspace when the user has explicitly typed a
         path — explicit beats implicit.
    4. No ``path``, no flags → start from cwd and:
       - If cwd is itself a workspace root → workspace mode.
       - If cwd has its own ``.repowise/state.json`` (i.e. it's a repo
         that has been indexed before) → single-repo mode, even if a
         workspace exists upstream. cd-into-the-repo is the strongest
         signal of user intent.
       - If a workspace exists upstream of cwd → workspace mode.
       - Otherwise → single-repo mode (cwd, even if not indexed).

    The returned :class:`CommandTarget` carries a ``reason`` string and an
    ``auto_detected`` flag so commands can render a transparent notice.
    """
    if workspace_flag and no_workspace_flag:
        raise click.UsageError("--workspace and --no-workspace are mutually exclusive.")

    if repo_alias is not None and no_workspace_flag:
        raise click.UsageError(
            "--repo <alias> implies workspace mode, but --no-workspace was passed."
        )

    explicit_path = path is not None
    base_path = resolve_repo_path(path)

    # Local import — avoids a circular import (core.workspace pulls in providers
    # which pull in CLI helpers in some edge cases).
    from repowise.core.workspace.config import (
        WORKSPACE_CONFIG_FILENAME,
        WorkspaceConfig,
    )

    def _load_ws(root: Path) -> Any | None:
        try:
            return WorkspaceConfig.load(root)
        except Exception:
            return None

    # ----- Rule 1: explicit --no-workspace -----
    if no_workspace_flag:
        return CommandTarget(
            mode="single",
            repo_path=base_path,
            reason="forced via --no-workspace",
            auto_detected=False,
        )

    # ----- Rule 2: --workspace or --repo -----
    if workspace_flag or repo_alias is not None:
        ws_root = find_workspace_root(base_path)
        if ws_root is None:
            raise WorkspaceNotFound(
                "No .repowise-workspace.yaml found at or above "
                f"{base_path}. Run 'repowise init <workspace-dir>' to "
                "create a workspace, or drop the --workspace flag."
            )
        ws_config = _load_ws(ws_root)
        if ws_config is None:
            raise WorkspaceNotFound(
                f"Found workspace config at {ws_root} but couldn't load it. Is it valid YAML?"
            )
        if repo_alias is not None and ws_config.get_repo(repo_alias) is None:
            available = ", ".join(ws_config.repo_aliases()) or "(none)"
            raise click.UsageError(
                f"Unknown repo alias '{repo_alias}' in workspace. Available: {available}"
            )
        reason = "via --workspace flag" if workspace_flag else f"via --repo {repo_alias}"
        return CommandTarget(
            mode="workspace",
            ws_root=ws_root,
            ws_config=ws_config,
            repo_filter=repo_alias,
            reason=reason,
            auto_detected=False,
        )

    # ----- Rule 3: explicit path argument -----
    if explicit_path:
        # Is the path itself a workspace root?
        if (base_path / WORKSPACE_CONFIG_FILENAME).is_file():
            ws_config = _load_ws(base_path)
            if ws_config is not None:
                return CommandTarget(
                    mode="workspace",
                    ws_root=base_path,
                    ws_config=ws_config,
                    reason="path argument is a workspace root",
                    auto_detected=True,
                )
        # Otherwise treat as single-repo. Surface workspace context if any.
        upstream = find_workspace_root(base_path)
        return CommandTarget(
            mode="single",
            repo_path=base_path,
            ws_root=upstream,
            reason="explicit path argument",
            auto_detected=False,
        )

    # ----- Rule 4: no path, no flags -----
    # 4a: cwd is itself a workspace root
    if (base_path / WORKSPACE_CONFIG_FILENAME).is_file():
        ws_config = _load_ws(base_path)
        if ws_config is not None:
            return CommandTarget(
                mode="workspace",
                ws_root=base_path,
                ws_config=ws_config,
                reason="cwd is the workspace root",
                auto_detected=True,
            )

    # 4b: cwd is an indexed repo — respect that even if a workspace exists upstream
    cwd_state = get_repowise_dir(base_path) / STATE_FILENAME
    if cwd_state.exists():
        upstream = find_workspace_root(base_path.parent if base_path.parent != base_path else None)
        return CommandTarget(
            mode="single",
            repo_path=base_path,
            ws_root=upstream,
            reason="cwd has its own .repowise/state.json (cd-into-repo wins)",
            auto_detected=upstream is not None,
        )

    # 4c: workspace exists upstream of cwd → workspace mode
    upstream = find_workspace_root(base_path)
    if upstream is not None:
        ws_config = _load_ws(upstream)
        if ws_config is not None:
            return CommandTarget(
                mode="workspace",
                ws_root=upstream,
                ws_config=ws_config,
                reason=f"workspace detected upstream at {upstream}",
                auto_detected=True,
            )

    # 4d: plain single-repo mode (likely uninitialized).
    return CommandTarget(
        mode="single",
        repo_path=base_path,
        reason="no workspace nearby",
        auto_detected=False,
    )


def is_interactive_session() -> bool:
    """Best-effort check for an interactive TTY.

    Centralized so commands can share one definition; some test runners
    fake stdin in ways that break ``sys.stdin.isatty()``.
    """
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False
