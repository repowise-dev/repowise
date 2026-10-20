"""HEAD, commit and ``state.json`` helpers shared by the workspace update paths."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from ..config import WorkspaceConfig

# Re-exported from the pipeline-free ``repowise.core.workspace.state`` so
# every existing ``from repowise.core.workspace.update import ...`` import
# keeps working.
from ..state import (  # noqa: F401
    _ANCESTRY_CACHE_MAX,
    IndexedCommit,
    _ancestry_cache,
    _older_commit,
    read_repo_state,
    read_state_commit,
    resolve_indexed_commit,
)

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
    drifts ``RepoEntry.last_commit_at_index`` away from the actual
    ``state.json`` value. Call this before any workspace-level decision
    that reads from ``ws_config`` so we never act on stale info.

    Returns the list of aliases that changed.
    """
    changed: list[str] = []
    for entry in ws_config.repos:
        abs_path = (workspace_root / entry.path).resolve()
        if not abs_path.is_dir():
            continue
        disk_commit = read_state_commit(abs_path)
        if disk_commit is not None and disk_commit != entry.last_commit_at_index:
            entry.last_commit_at_index = disk_commit
            changed.append(entry.alias)
    if changed and save_if_changed:
        try:
            ws_config.save(workspace_root)
        except Exception:
            # Saving is best-effort — the in-memory sync still happened.
            _log.warning("Could not persist synced workspace config", exc_info=True)
    return changed


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
