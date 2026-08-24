"""Shared CLI utilities — async bridge, path resolution, state, DB setup."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

import click
from rich.console import Console

from repowise.cli.output import resolve_console_width
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

# Width is pinned only when the stream is not a terminal — see
# `output.resolve_console_width`. Without it rich renders a pipe at 80 columns
# and ellipsises the very paths an agent needs to act on.
console = Console(width=resolve_console_width(sys.stdout))
err_console = Console(stderr=True, width=resolve_console_width(sys.stderr))

STATE_FILENAME = "state.json"
REPOWISE_DIR = ".repowise"

# Suppresses mirroring the provider key into `.repowise/.env`. The env
# spelling of `init --no-save-key`, mirroring REPOWISE_SKIP_EDITOR_SETUP: CI
# and shared machines configure the process, not the command line. It is also
# how the interactive key prompt records a "no": that answer is taken at the
# start of the run and has to survive until config is written at the end.
NO_SAVE_KEY_ENV = "REPOWISE_NO_SAVE_KEY"


def _clean_flag(value: str | None) -> bool:
    """Whether an env var is set to something meaning "on"."""
    return (value or "").strip().lower() not in ("", "0", "false", "no")


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


#: Busy timeout for the reconcile's own connection. The engine default is 30s,
#: sized for bulk graph-edge writes; inheriting it here is a trap, because a
#: read command opens several engines (``status`` four, ``doctor`` five) and a
#: store locked by a concurrent ``repowise update`` would stall each one in
#: turn — measured at 133s of silence for ``status`` before this was bounded.
#: The reconcile is a best-effort secondary write, so it takes the same lever
#: issue #326 gave the cost tracker: fail fast and be dropped rather than block.
_RECONCILE_BUSY_TIMEOUT_MS = 2000


async def reconcile_schema_best_effort(db_url: str) -> None:
    """Back-fill additive schema drift on the store at *db_url*, best-effort.

    An index built by an older repowise is missing whatever columns the models
    have gained since, and the ORM then fails with a raw ``no such column`` on
    the first query — which for a read command means every read. ``init_db``
    back-fills those columns in place and is idempotent, so the CLI pairs it
    with ``create_engine`` everywhere it opens a store, the way the MCP server,
    the workspace registry and the FastAPI app already do in their lifespans.

    Opportunistic, not a precondition, which is the part worth having in one
    place. Reconciling needs a write, and a store can be read-only or
    exclusively locked by a concurrent ``repowise update``. Aborting there would
    be a regression twice over: a store already on the current schema needs no
    DDL and would have read fine, and where the drift is real the following
    query fails with the ``no such column`` that the failure shield turns into
    "this index predates the installed repowise, run repowise update" — a
    better answer than whichever write error stopped the repair.

    Takes a URL rather than an engine so the repair runs on its own short-timeout
    connection: the caller's engine keeps the full 30s window for the work it was
    opened to do, and a contended repair gives up in two seconds instead of
    stalling the command behind it.

    Never CREATES a store. ``init_db`` on a fresh path would materialise a
    42-table file, so a read command run in a repo that was never indexed would
    leave a database behind where the user expects "not indexed yet".
    """
    from repowise.core.persistence import create_engine, init_db

    if _missing_sqlite_file(db_url):
        return

    engine = create_engine(db_url, busy_timeout_ms=_RECONCILE_BUSY_TIMEOUT_MS)
    try:
        with contextlib.suppress(Exception):
            await init_db(engine)
    finally:
        with contextlib.suppress(Exception):
            await engine.dispose()


def _missing_sqlite_file(db_url: str) -> bool:
    """True when *db_url* names a SQLite file that does not exist yet.

    Only SQLite is checked: a server-backed URL has no file to create, and the
    caller is not the component that decides whether that database should exist.
    """
    if not db_url.startswith("sqlite"):
        return False
    _, sep, tail = db_url.partition(":///")
    if not sep or not tail or tail.startswith(":memory:"):
        return False
    return not Path(tail).exists()


def db_configured() -> bool:
    """True when ``REPOWISE_DB_URL`` or ``REPOWISE_DATABASE_URL`` is set.

    The DB may still be the repo-local ``wiki.db`` default — the file
    existence check the callers pair this with decides that.
    """
    from repowise.core.persistence import get_configured_db_url

    return get_configured_db_url() is not None


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------


def load_state(repo_path: Path) -> dict[str, Any]:
    """Load ``.repowise/state.json`` or return an empty dict if absent."""
    state_path = get_repowise_dir(repo_path) / STATE_FILENAME
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {}


#: Slots the last whole-repo generation put in front of this repository's
#: signals, which is not the same as the slots that produced a page. The two
#: differ, and only the first answers "has this index ever been offered a
#: Glossary?".
ONBOARDING_SLOTS_OFFERED_KEY = "onboarding_slots_offered"


def stamp_offered_slots(state: dict[str, Any], *, enabled: bool = True) -> None:
    """Record which onboarding slots this whole-repo run evaluated.

    Only a run that generates the whole repository may call this: the slot
    gates read whole-repo signals, so a scoped run that saw one changed file
    has not offered anything to anything.

    Written because a missing onboarding row has two causes that look identical
    in a store and want opposite responses. A slot registered after this index
    was built has never been evaluated here, and ``update --full`` would build
    it; a slot that *was* evaluated and whose gate refused the repository will
    be refused again by the same signals, and telling the user to spend a model
    run on it is a lie. Measured on ``test-repos/microdot``: of the five
    registered slots, two produce pages and three (``getting_started``,
    ``active_landscape``, ``glossary``) are gate-skipped on every run, full or
    fresh. A notice driven by the rows alone would name all three, forever, and
    none of them would ever arrive.

    ``enabled`` is the run's ``enable_onboarding``. A run with onboarding off
    offered nothing, and recording otherwise would silence the notice for a
    user who later turns it on.
    """
    from repowise.core.generation.onboarding import iter_specs

    state[ONBOARDING_SLOTS_OFFERED_KEY] = (
        sorted(spec.slot for spec in iter_specs()) if enabled else []
    )


def save_state(repo_path: Path, state: dict[str, Any], *, full_index: bool = False) -> None:
    """Write *state* to ``.repowise/state.json``.

    Every persist stamps the store-format markers (``store_format_version`` and
    the ``written_by_version`` package version that wrote it) so the upgrade
    layer always has a current record of the store's shape and provenance.

    ``full_index`` marks a persist that follows a full-repo (re)generation, so
    the store may be stamped to the terminal store-format version. A routine
    incremental persist leaves it False and the version clamps below any
    ``REINDEX_RECOMMENDED`` gate, keeping a reindex recommendation alive until
    an actual re-index clears it.
    """
    ensure_repowise_dir(repo_path)
    try:
        from repowise.cli import __version__ as _pkg_version
        from repowise.core.upgrade import stamp as _stamp_store_version

        _stamp_store_version(state, package_version=_pkg_version, full_index=full_index)
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
    with contextlib.suppress(OSError):
        _update_queued_path(repo_path).write_text(json.dumps(payload), encoding="utf-8")


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
    with contextlib.suppress(OSError):
        _update_queued_path(repo_path).unlink(missing_ok=True)


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
    with contextlib.suppress(OSError):
        _update_pending_path(repo_path).write_text(head, encoding="utf-8")


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
    with contextlib.suppress(OSError):
        _update_pending_path(repo_path).unlink(missing_ok=True)


def _pending_commit_still_ahead(
    repo_path: Path, pending_head: str, indexed_head: str | None
) -> bool:
    """True only when ``pending_head`` is a resolvable commit strictly ahead of
    ``indexed_head`` — a newer commit the index has not caught up to yet.

    Equal commits, ancestors of ``indexed_head``, and commits that no longer
    resolve (rebased or gc'd away) all return ``False``, so the caller clears
    them instead of leaving the marker behind forever.
    """
    if not indexed_head or pending_head == indexed_head:
        return False
    import subprocess

    try:
        # ``indexed_head`` is an ancestor of ``pending_head`` => pending is
        # newer than what we indexed and worth keeping. A non-zero exit
        # (including an unresolvable pending commit) means "not ahead".
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", indexed_head, pending_head],
            cwd=str(repo_path),
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def consume_update_pending(repo_path: Path, indexed_head: str | None) -> None:
    """Clear the ``.update.pending`` marker once the index has caught up to it.

    A bailed update writes the latest HEAD to ``.update.pending`` (see
    :func:`write_update_pending`) so the in-flight update can tell more commits
    landed. The update that actually holds the lock calls this when it
    finishes: the marker is obsolete unless it points to a commit strictly
    *ahead* of the one just indexed. The previous consumer only cleared on an
    exact ``pending == head`` match, so once HEAD advanced past the pending
    commit (or that commit was rebased away) the marker leaked indefinitely.
    """
    pending_head = read_update_pending(repo_path)
    if pending_head is None:
        return
    if not _pending_commit_still_ahead(repo_path, pending_head, indexed_head):
        clear_update_pending(repo_path)


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


def head_commit_ts(repo_path: Path) -> float | None:
    """Committer timestamp of the repo's HEAD, or None when git is unavailable.

    Anchors the periodic idle-file health re-score gate (#728) to repo time
    rather than wall clock, so the cadence is deterministic under
    ``REPOWISE_GIT_WINDOW_ANCHOR`` and correct for historical checkouts.

    Shared with ``init`` so a fresh index can stamp ``last_full_rescore_at`` in
    the same units the gate reads it back in.
    """
    try:
        import git

        repo = git.Repo(repo_path, search_parent_directories=True)
        try:
            return float(repo.head.commit.committed_date)
        finally:
            repo.close()
    except Exception:
        return None


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


def resolve_max_file_pages(
    chosen: int | None = None,
    config: dict[str, Any] | None = None,
) -> int | None:
    """Resolve the file-page cap: explicit choice, then config.yaml, then unset.

    Three states reach ``GenerationConfig.max_file_pages`` (see its comment and
    ``selection/selector.py``): ``None`` leaves the volume policy in charge, ``0``
    is an explicit refusal to cap, and a positive value is a cap.

    A negative or unparseable ``max_file_pages`` in config.yaml resolves to
    ``None`` rather than to zero pages, so a typo hands the decision back to the
    policy instead of silently deleting the file layer.
    """
    raw = chosen if chosen is not None else (config or {}).get("max_file_pages")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value == 0:
        return 0  # "all of them", and it survives the policy
    return value if value > 0 else None


def _persist_provider_key(repo_path: Path, provider: str) -> None:
    """Mirror ``provider``'s credential from the environment into ``.repowise/.env``.

    Key persistence used to hang off the interactive key *prompt*, so it only
    ran when the user typed a key. Supplying the key through the environment is
    precisely the no-prompt case, which meant a scripted
    ``init --provider openai --yes`` indexed fine and wrote ``provider:`` to
    config.yaml but left no credential behind: ``repowise mcp`` against that
    repo then answered ``degraded: "no-llm-provider"``. Scripted init is the
    primary path for agents and CI, so a run that succeeds has to leave a repo
    whose MCP server can actually answer.

    The rule, stated out loud: a key that successfully indexed this repo is the
    key this repo needs, so it is saved, with a notice naming the file and
    ``--no-save-key`` to opt out. Nothing is written for a provider that needs
    no credential.

    Routed through :func:`save_repo_env_key` rather than writing the file here:
    the ``.gitignore`` entry and the owner-only mode come with it, and a
    hand-rolled write would leave a committable secret.

    Best-effort by design. It runs at the tail of a completed init, after the
    index has been paid for, and it is the first thing on that path to write
    outside ``.repowise/``, so a read-only checkout or an unwritable
    ``.gitignore`` must cost the user a warning, not the run.
    """
    from repowise.core.providers.llm.registry import PROVIDER_API_KEY_ENVS
    from repowise.core.repo_config import save_repo_env_key

    if _clean_flag(os.environ.get(NO_SAVE_KEY_ENV)):
        return

    # Keys only. ``provider_required_envs`` would also hand back ollama's
    # OLLAMA_BASE_URL, and pinning an endpoint into the repo is a different
    # decision from saving a credential: a later run on another network
    # would silently reuse the stale URL.
    #
    # Some providers accept either of two vars (gemini: GEMINI_API_KEY /
    # GOOGLE_API_KEY). Persist the one actually carrying the value, not both,
    # so the var written is the var `provider_kwargs` will read back.
    for env_var in PROVIDER_API_KEY_ENVS.get(provider, ()):
        value = (os.environ.get(env_var) or "").strip()
        if not value:
            continue
        try:
            save_repo_env_key(repo_path, env_var, value)
        except (OSError, ValueError) as exc:
            err_console.print(
                f"[yellow]Warning:[/yellow] could not save {env_var} to "
                f".repowise/.env ({exc}). The index is complete, but "
                f"`repowise mcp` will need {env_var} in its environment."
            )
            return
        console.print(
            f"[dim]Saved {env_var} to .repowise/.env (gitignored). "
            f"--no-save-key or {NO_SAVE_KEY_ENV}=1 to skip.[/dim]"
        )
        return


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
    save_key: bool = True,
) -> None:
    """Write provider/model/embedder (and optionally exclude_patterns) to ``.repowise/config.yaml``.

    Performs a round-trip load so existing keys are preserved.

    ``embedding_model`` is persisted so ``repowise serve`` can rebuild the same
    embedder used at init time — without it the server silently falls back to a
    provider default (e.g. ``text-embedding-3-small``), which mismatches the
    indexed vectors and breaks chat/search retrieval (issue #426).

    ``save_key`` also mirrors the provider's credential into ``.repowise/.env``.
    It belongs here because this function *is* the "this run committed to this
    provider for this repo" moment, shared by all three flows that reach it
    (single-repo init, workspace init, ``workspace add``). All three otherwise
    leave an indexed repo their MCP server cannot authenticate against.
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

    if save_key:
        _persist_provider_key(repo_path, provider)


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


#: The hook surfaces that *replace* a tool result rather than adding to it.
#: One consent turns them all on; each has its own ``repowise hook <name>``
#: toggle afterwards, so a surface that turns out to be wrong can be dropped
#: without taking the others with it.
HOOK_REPLACEMENT_SURFACES = ("read_skeleton", "search_digest")


def save_hook_surface_enabled(repo_path: Path, surface: str, *, enabled: bool) -> None:
    """Deep-merge ``hooks.<surface>`` into ``.repowise/config.yaml``.

    Same shape as :func:`save_distill_commands_enabled`, and written by the
    same consent: the rewrite-hook prompt means "let repowise's hooks
    intervene in your agent's tool calls", and rewriting a Bash command is a
    larger intervention than serving a Read as its skeleton or a search as its
    digest, not a smaller one. There is deliberately no second question.
    """
    cfg = load_config(repo_path)
    hooks = dict(cfg.get("hooks") or {})
    hooks[surface] = enabled
    save_config_partial(repo_path, hooks=hooks)


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
    from repowise.core.providers.llm.registry import (
        PROVIDER_AUTODETECT_ORDER,
        provider_credentials_present,
        provider_kwargs,
    )

    cfg: dict[str, Any] = {}
    if repo_path is not None:
        cfg = load_config(repo_path)

    if provider_name is None:
        # An empty or whitespace-only value means "not set", not "a provider
        # named ''". CI systems and agent harnesses declare env vars with empty
        # values routinely (``REPOWISE_PROVIDER: ""`` in a workflow matrix),
        # and the explicit-provider branch below keys off ``is not None``, so
        # an empty string reached get_provider() and raised a bare ValueError
        # traceback instead of falling through to auto-detection.
        provider_name = (os.environ.get("REPOWISE_PROVIDER") or "").strip() or None

    if provider_name is None and cfg.get("provider"):
        provider_name = cfg["provider"]

    # Honor the config model regardless of how the provider was resolved (#416).
    if model is None and cfg.get("model"):
        model = cfg["model"]

    def _config_base_url(name: str) -> str | None:
        """Return a base_url the repo config sets for the provider, if any.

        Env vars are handled by :func:`provider_kwargs` from the shared
        registry mapping; this covers only the config-file source, which is
        CLI-specific.
        """
        section = cfg.get(name)
        if isinstance(section, dict):
            base_url = section.get("base_url")
            if base_url:
                return base_url
        return None

    def _build(name: str) -> Any:
        """Instantiate ``name`` with env-derived kwargs plus config fallbacks."""
        kwargs = provider_kwargs(name, model=model, repo_path=repo_path)
        # Applied to any name, as before: openrouter takes a base_url without
        # having an env var for one, so gating this on the env map would drop a
        # config value that used to be honored. (A stray `mock: {base_url: …}`
        # in config.yaml still reaches a constructor that has no such
        # parameter and raises TypeError. Longstanding, orthogonal to #1119.)
        config_base_url = _config_base_url(name)
        if config_base_url:
            kwargs.setdefault("base_url", config_base_url)
        try:
            return get_provider(name, **kwargs)
        except click.ClickException:
            raise
        except Exception as exc:
            # Everything this constructor reads is user input: env vars and
            # .repowise/config.yaml, base_url above all. A typo there is a
            # configuration error, not a repowise bug, but it used to surface
            # as a raw traceback that escaped every caller's handler —
            # OLLAMA_BASE_URL=http://localhost:abc makes httpx raise
            # InvalidURL, which killed `init` outright.
            # Imported here, not at module scope: the telemetry spool imports
            # this module back for the global config dir.
            from repowise.cli.platform import telemetry

            telemetry.add_command_outcome(failure_reason="provider_setup_failed")
            raise click.ClickException(
                f"Could not set up the {name} provider: {exc}. Check its "
                "settings in your environment and .repowise/config.yaml."
            ) from exc

    if provider_name is not None:
        # Validate configuration before attempting to create provider
        warnings = validate_provider_config(provider_name)
        if warnings:
            for warning in warnings:
                err_console.print(f"[yellow]Warning:[/yellow] {warning}")
            # For explicit provider requests, we still try to create it
            # The provider constructor will fail if the API key is actually required

        return _build(provider_name)

    # Auto-detect from whatever credentials the env carries, in the shared
    # priority order. The per-provider env-var mapping lives in the registry
    # beside the provider table, so adding a provider wires it into the CLI and
    # the MCP server at once instead of into whichever copy got remembered.
    for candidate in PROVIDER_AUTODETECT_ORDER:
        if provider_credentials_present(candidate):
            return _build(candidate)

    from repowise.cli.platform import telemetry

    telemetry.add_command_outcome(failure_reason="no_provider_configured")
    raise click.ClickException(
        "No provider configured. Use --provider, set REPOWISE_PROVIDER, "
        "or set ANTHROPIC_API_KEY / OPENAI_API_KEY / OPENROUTER_API_KEY / "
        "OLLAMA_BASE_URL / GEMINI_API_KEY / GOOGLE_API_KEY / DEEPSEEK_API_KEY / "
        "KIMI_API_KEY / EDENAI_API_KEY / LITELLM_API_KEY. Use "
        "REPOWISE_PROVIDER=claude_cli to use an "
        "authenticated Claude Code subscription, REPOWISE_PROVIDER=codex_cli to use "
        "an authenticated Codex CLI subscription, or REPOWISE_PROVIDER=opencode "
        "to use opencode."
    )


def resolve_provider_or_prompt(
    provider_name: str | None,
    model: str | None,
    repo_path: Path,
    *,
    reasoning: str | None = None,
    interactive: bool,
) -> Any:
    """Resolve a provider, falling back to init's interactive setup on a miss.

    Ordinary resolution is :func:`resolve_provider`. When that fails because no
    provider/key is configured and ``interactive`` is set (an interactive
    terminal, not a hook / CI / ``--progress json`` run), reuse init's exact
    provider + API-key prompt, persist the choice, and retry — so a docs run
    that only just asked for LLM pages onboards the same way ``init`` does
    instead of dying with "No provider configured".

    When ``interactive`` is False the original error propagates unchanged, so
    background runs (post-commit hook, CI, machine-driven ``--progress json``)
    keep their clean, non-blocking failure.

    Persistence reuses init's helpers: the key lands in ``.repowise/.env`` (from
    the prompt) and provider/model in ``.repowise/config.yaml`` here, so the
    prompt only ever appears once per repo.
    """
    try:
        return resolve_provider(provider_name, model, repo_path=repo_path)
    except Exception as original_error:
        if not interactive:
            raise
        from repowise.cli.ui import interactive_provider_config_select

        # ``interactive`` rests on ``isatty()``, which lies: under Git Bash,
        # some pty wrappers, and ``docker run -t`` without -i it claims a
        # terminal it cannot read from, and agents drive us through exactly
        # those shapes. Treat the prompt as the probe (as init does): if stdin
        # cannot answer, click raises EOFError/Abort, so fall back to the clean,
        # actionable "No provider configured" error instead of a bare "Aborted!".
        try:
            selection = interactive_provider_config_select(
                console, model, reasoning, repo_path=repo_path
            )
        except (EOFError, click.Abort):
            raise original_error from None

        # Persist provider/model so future runs resolve without re-prompting.
        # The API key was already persisted to .repowise/.env by the prompt.
        save_config_partial(
            repo_path,
            provider=selection.provider_name,
            model=selection.model,
        )
        return resolve_provider(selection.provider_name, selection.model, repo_path=repo_path)


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

    # Required environment variables per provider, read from the registry that
    # also drives resolution, so a provider added there is validated here without
    # a second edit. The agent-CLI providers are absent by design: they need no
    # env var, so they are handled by the binary checks below instead.
    from repowise.core.providers.llm.registry import (
        PROVIDER_API_KEY_ENVS,
        provider_required_envs,
    )

    provider_env_vars = {
        name: list(provider_required_envs(name)) for name in (*PROVIDER_API_KEY_ENVS, "ollama")
    }

    if provider_name:
        if provider_name == "codex_cli":
            if not _is_codex_cli_available():
                warnings.append(
                    "Provider 'codex_cli' requires the Codex CLI. "
                    "Install it with: npm install -g @openai/codex"
                )
            return warnings

        if provider_name == "claude_cli":
            import shutil

            if not shutil.which("claude"):
                warnings.append(
                    "Provider 'claude_cli' requires the Claude Code CLI.\n"
                    "  Install:  https://claude.com/claude-code\n"
                    "  Setup:    run 'claude login' once to authenticate"
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
                    f"[dim]\\[workspace][/dim] {command or 'running'} on "
                    f"[cyan]{self.repo_filter}[/cyan] within "
                    f"[cyan]{ws_root}[/cyan] ({repos} repos)"
                )
            else:
                console_obj.print(
                    f"[dim]\\[workspace][/dim] {command or 'running'} across "
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
