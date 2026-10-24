"""HEAD, commit and ``state.json`` helpers shared by the workspace update paths."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import subprocess
from contextlib import closing, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

from ..config import WorkspaceConfig

_log = logging.getLogger("repowise.workspace.update")


def get_head_commit(repo_path: Path) -> str | None:
    """Return the current HEAD commit SHA via git, or ``None``."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def count_commits_between(repo_path: Path, base: str, head: str) -> int:
    """Return the number of commits between *base* and *head*, or 0 on error."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{base}..{head}"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return 0


def commit_exists(repo_path: Path, sha: str) -> bool:
    """Return True when *sha* resolves to a commit in *repo_path*.

    The incremental path must verify this itself: ``ChangeDetector`` returns
    an **empty** diff for unresolvable refs, which would masquerade as "no
    changes" and let the caller bump ``last_sync_commit`` past commits that
    were never indexed (e.g. after a rebase or an aggressive ``git gc``).
    """
    try:
        result = subprocess.run(
            ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=str(repo_path),
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def clear_stale_update_pending(repo_path: Path, indexed_head: str | None) -> None:
    """Drop ``.repowise/.update.pending`` once the index has caught up to it.

    A deferred update records the latest HEAD in this marker; the update that
    holds the lock calls this on success. The marker is kept only when it
    points strictly *ahead* of ``indexed_head``; equal, ancestor and
    unresolvable commits are cleared.
    """
    pending_path = repo_path / ".repowise" / ".update.pending"
    try:
        pending_head = pending_path.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if pending_head and indexed_head and pending_head != indexed_head:
        try:
            # indexed_head is an ancestor of pending_head => strictly ahead,
            # keep it. Non-zero (incl. unresolvable pending) => safe to clear.
            ahead = subprocess.run(
                ["git", "merge-base", "--is-ancestor", indexed_head, pending_head],
                cwd=str(repo_path),
                capture_output=True,
                timeout=10,
            )
            if ahead.returncode == 0:
                return
        except Exception:
            pass
    with suppress(OSError):
        pending_path.unlink(missing_ok=True)


def read_repo_state(repo_path: Path) -> dict[str, Any]:
    """Return the parsed ``<repo>/.repowise/state.json``, or ``{}``."""
    state_path = repo_path / ".repowise" / "state.json"
    if not state_path.is_file():
        return {}
    try:
        data = _json.loads(state_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_state_commit(repo_path: Path) -> str | None:
    """Return ``last_sync_commit`` from ``<repo>/.repowise/state.json`` or None."""
    sha = read_repo_state(repo_path).get("last_sync_commit")
    return str(sha) if sha else None


def read_index_synced_at(repo_path: Path) -> str | None:
    """ISO time *repo_path*'s index last synced (``crud.index_synced_at``), or None.

    Plain ``sqlite3``, read-only, so sync callers and ones already on an event
    loop can ask. ``None`` for a non-SQLite store or an unreadable one.
    """
    import sqlite3

    from sqlalchemy.engine import make_url

    from ...persistence.database import get_configured_db_url, get_repo_db_path

    # Not ``resolve_db_url``: its default creates ``.repowise/``, which would
    # make an unindexed member look indexed.
    configured = get_configured_db_url()
    if configured is None:
        db_file = get_repo_db_path(repo_path)
    else:
        url = make_url(configured)
        if not url.drivername.startswith("sqlite"):
            return None
        db_file = Path(url.database or "")
    if not db_file.is_file():
        return None
    try:
        with closing(sqlite3.connect(f"{db_file.as_uri()}?mode=ro", uri=True)) as conn:
            if configured is None:
                # A repo-local store holds one repository row.
                row = conn.execute("SELECT updated_at FROM repositories LIMIT 1").fetchone()
            else:
                row = conn.execute(
                    "SELECT updated_at FROM repositories WHERE local_path = ?",
                    (str(repo_path),),
                ).fetchone()
    except (sqlite3.Error, ValueError):
        return None
    return synced_at_iso(row[0] if row else None)


def synced_at_iso(raw: str | None) -> str | None:
    """A raw ``repositories.updated_at`` cell as the ISO sync time, or None."""
    from ...persistence.crud import as_utc

    try:
        synced_at = as_utc(datetime.fromisoformat(raw)) if raw else None
    except ValueError:
        return None
    return synced_at.isoformat() if synced_at else None


def stamp_workspace_entry(entry: Any, repo_path: Path, head: str | None) -> None:
    """Record a member's finished sync on its workspace entry.

    The time is the store's own sync stamp so the config and every DB reader
    agree; now only when the store cannot be read.
    """
    entry.indexed_at = read_index_synced_at(repo_path) or datetime.now(UTC).isoformat()
    entry.last_commit_at_index = head


class IndexedCommit(NamedTuple):
    """The commit an index describes, or why it cannot be named."""

    sha: str | None
    problem: str | None = None


def resolve_indexed_commit(repo_path: Path | None, row_commit: str | None) -> IndexedCommit:
    """The commit the index describes, from ``state.json`` and the repository row.

    Every update writes both from one captured head, so they normally agree.
    When one is an ancestor of the other (an update interrupted between the
    two writes, or a row an older build left behind), the index describes the
    older: the files between them then count as changed since the index was
    built, which over-selects rather than misses. Two commits neither of which
    contains the other (a rebase, a restored backup), or a sha git cannot
    read, leave the commit unknown.
    """
    state = read_state_commit(repo_path) if repo_path is not None else None
    if not state or not row_commit or state == row_commit:
        return IndexedCommit(state or row_commit)
    older = _older_commit(str(repo_path), state, row_commit)
    if older is not None:
        return IndexedCommit(older)
    return IndexedCommit(
        None,
        f"The index records two commits ({state[:7]} in state.json, {row_commit[:7]} "
        "in its database) and neither contains the other, so what it describes is "
        "unknown; run `repowise update`.",
    )


# (repo_path, a, b) -> the older commit, or None when neither contains the
# other. Only definite answers: a timeout or an unknown sha is asked again.
_ancestry_cache: dict[tuple[str, str, str], str | None] = {}
_ANCESTRY_CACHE_MAX = 32


def _older_commit(repo_path: str, a: str, b: str) -> str | None:
    """The one of *a*, *b* that is an ancestor of the other, else ``None``.

    Cached because the freshness envelope asks on every response while the two
    stamps disagree. Ancestry between two known shas never changes, and the
    next update makes the stamps equal, which ends the asking.
    """
    from ...git_refs import is_ancestor

    key = (repo_path, a, b)
    if key in _ancestry_cache:
        return _ancestry_cache[key]
    a_first = is_ancestor(repo_path, a, b)
    b_first = None if a_first else is_ancestor(repo_path, b, a)
    older = a if a_first else b if b_first else None
    if older is not None or (a_first is False and b_first is False):
        if len(_ancestry_cache) >= _ANCESTRY_CACHE_MAX:
            _ancestry_cache.clear()
        _ancestry_cache[key] = older
    return older


def sync_workspace_state_from_disk(
    workspace_root: Path,
    ws_config: WorkspaceConfig,
    *,
    save_if_changed: bool = True,
) -> list[str]:
    """Refresh ``WorkspaceConfig`` entries from each repo's on-disk
    ``state.json``.

    A child repo can be updated outside the workspace orchestrator (the
    user runs ``repowise update`` inside the child dir directly), which
    drifts ``RepoEntry.last_commit_at_index`` (and ``indexed_at``, from the
    repo's own store) away from the actual values. Call this before any
    workspace-level decision that reads from ``ws_config`` so we never act on
    stale info.

    Returns the list of aliases that changed.
    """
    changed = [
        entry.alias
        for entry in ws_config.repos
        if _refresh_entry_from_disk(entry, (workspace_root / entry.path).resolve())
    ]
    if changed and save_if_changed:
        try:
            ws_config.save(workspace_root)
        except Exception:
            # Saving is best-effort — the in-memory sync still happened.
            _log.warning("Could not persist synced workspace config", exc_info=True)
    return changed


def _refresh_entry_from_disk(entry: Any, abs_path: Path) -> bool:
    """Copy the member's on-disk commit and sync time onto *entry*; True if either moved."""
    if not abs_path.is_dir():
        return False
    moved = False
    for attr, disk in (
        ("last_commit_at_index", read_state_commit(abs_path)),
        ("indexed_at", read_index_synced_at(abs_path)),
    ):
        if disk is not None and disk != getattr(entry, attr):
            setattr(entry, attr, disk)
            moved = True
    return moved


def check_repo_staleness(
    repo_path: Path,
    last_commit: str | None,
) -> tuple[bool, str | None, int]:
    """Check if a repo has new commits since *last_commit*.

    Returns ``(is_stale, current_head, commits_behind)``.
    """
    current_head = get_head_commit(repo_path)
    if current_head is None:
        return False, None, 0

    if last_commit is None:
        # Never indexed — treat as stale
        return True, current_head, 0

    if current_head == last_commit:
        # Same reconciliation as the single-repo update: a page can be stale
        # with HEAD unmoved, and only an update clears it.
        try:
            from repowise.core.persistence import load_stale_structural_file_paths

            if load_stale_structural_file_paths(repo_path):
                return True, current_head, 0
        except Exception:
            _log.debug("stale page probe failed for %s", repo_path, exc_info=True)
        return False, current_head, 0

    behind = count_commits_between(repo_path, last_commit, current_head)
    return True, current_head, behind


def restamp_changed_sync_commit(repo_path: Path, previous: str | None, current: str | None) -> None:
    """Move the repository row to a ``last_sync_commit`` a state write just changed.

    The CLI and server state writers call this (the workspace updater awaits
    :func:`reconcile_repo_head_commit` itself), so a path that advances the
    sync commit leaves the row on the same commit, whatever HEAD its persist
    step read (a pull during a long run moves it). An unchanged commit is left
    alone: the "up to date" paths stamp it themselves. The state file is
    already written, so a failure here is logged, not raised.
    """
    if not current or current == previous:
        return

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        # Ceiling: callers must be off-loop; skipped here, the resolver still
        # reads the ancestor pair this leaves correctly.
        _log.warning("Skipped the repository row restamp for %s: called on an event loop", repo_path)
        return
    try:
        asyncio.run(reconcile_repo_head_commit(repo_path, current))
    except Exception:
        _log.warning("Could not restamp the repository row for %s", repo_path, exc_info=True)


async def reconcile_repo_head_commit(repo_path: Path, head: str | None) -> None:
    """Advance the DB freshness for *repo_path* after a sync-check to *head*.

    The "up to date" skip and the no-relevant-changes incremental path bump
    ``last_sync_commit`` without re-running the persistence that stamps the
    ``repositories`` row, which the server reads for the indexed commit and
    the "indexed at" time. Stamps ``head_commit`` only on drift but always
    advances ``updated_at``. Creates a missing row in an existing store (a
    configured database counts as existing); a no-op when no store exists.
    The single head-commit stamper for the CLI and workspace update paths.
    """
    from ...persistence.database import has_db_store, resolve_db_url

    if not head or not has_db_store(repo_path):
        return

    from ...persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from ...persistence.crud import get_repository_by_path

    url = resolve_db_url(repo_path)
    engine = create_engine(url)
    try:
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                await upsert_repository(
                    session,
                    name=repo_path.name,
                    local_path=str(repo_path),
                    head_commit=head,
                )
            else:
                if repo.head_commit != head:
                    repo.head_commit = head
                repo.updated_at = datetime.now(UTC)
                await session.flush()
    finally:
        await engine.dispose()


async def stored_index_counts(repo_path: Path) -> tuple[int, int] | None:
    """``(files, symbols)`` as persisted for *repo_path*, or ``None`` when unreadable.

    The ``count_files`` / ``count_symbols`` figures ``workspace list`` and
    ``status`` show, so an update reports what it stored rather than what it
    parsed this run: a run with nothing to re-parse parsed nothing.
    """
    # Never raises: a count the store cannot give must not fail the update.
    try:
        from ...persistence import create_engine, create_session_factory, get_session
        from ...persistence.crud import count_files, count_symbols, get_repository_by_path
        from ...persistence.database import has_db_store, resolve_db_url

        if not has_db_store(repo_path):
            return None
        engine = create_engine(resolve_db_url(repo_path))
        try:
            async with get_session(create_session_factory(engine)) as session:
                repo = await get_repository_by_path(session, str(repo_path))
                if repo is None:
                    return None
                return (
                    await count_files(session, repo.id),
                    await count_symbols(session, repo.id),
                )
        finally:
            await engine.dispose()
    except Exception:
        _log.debug("Could not read stored index counts for %s", repo_path, exc_info=True)
        return None
