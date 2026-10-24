"""Checks 1-4b: git, ``.repowise/``, hooks, database, state.json, store format."""

from __future__ import annotations

import os
from pathlib import Path as _DoctorPath

from repowise.cli.helpers import db_configured, load_state, run_async

from .._types import DoctorCheck, _check
from .context import CheckContext, open_store


async def _page_count(session: object, repo_id: str) -> int:
    """Count this repository's pages in SQL.

    Counted in the database rather than by measuring a fetched list. The
    previous spelling took ``len()`` of a ``list_pages`` page, whose ``limit``
    defaults to 100 and was passed 10000 here, so every repository with more
    than 10000 pages reported exactly 10000 — a plausible-looking number that
    never moves.
    """
    from sqlalchemy import func, select

    from repowise.core.persistence.models import Page

    result = await session.execute(  # type: ignore[attr-defined]
        select(func.count()).select_from(Page).where(Page.repository_id == repo_id)
    )
    return int(result.scalar_one())


def _repowise_dir_check(repowise_dir: _DoctorPath) -> DoctorCheck:
    """Exists and writable, probed with a real file: os.access lies on Windows."""
    if not repowise_dir.exists():
        return _check(".repowise/ directory", False, f"{repowise_dir} (run 'repowise init')")
    probe = repowise_dir / ".doctor-write-probe"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return _check(
            ".repowise/ directory",
            False,
            f"{repowise_dir} is not writable ({exc.strerror or exc}); fix its permissions",
        )
    return _check(".repowise/ directory", True, str(repowise_dir))


def _autosync_hook_check(repo_path: _DoctorPath) -> DoctorCheck:
    """Report the post-commit hook through hooks.status, which asks git for the
    real hooks directory, so worktrees and core.hooksPath read correctly."""
    try:
        from repowise.cli.hooks import status as _hook_status

        state = _hook_status(repo_path)
    except Exception as exc:
        return _check("Post-commit hook", True, f"could not read hook state ({exc})")
    if state.startswith("installed"):
        return _check("Post-commit hook", True, state)
    if state == "not installed":
        return _check(
            "Post-commit hook",
            True,
            "not installed; the index updates only when you run 'repowise update' "
            "('repowise hook install' turns auto-sync on)",
        )
    return _check("Post-commit hook", True, state)


def _attribution_notes_check(repo_path: _DoctorPath) -> DoctorCheck | None:
    """At ``attribution.capture: notes``, whether notes travel with the repo.

    None at every other level. Notes reach teammates only through a fetch
    refspec and an explicit push, and survive a rebase only via
    ``notes.rewriteRef``; without them coverage is silently low elsewhere.
    """
    name = "Attribution notes"
    try:
        from repowise.core.attribution import load_attribution_settings

        if load_attribution_settings(repo_path).capture != "notes":
            return None
        from repowise.core.edit_capture.notes import FETCH_REFSPEC, NOTES_REF, refspec_status

        status = refspec_status(_DoctorPath(repo_path))
    except OSError as exc:
        return _check(name, True, f"could not read git config ({exc})")
    if not status["remote"]:
        return _check(name, True, "no origin remote, so notes stay on this machine")
    push = (
        "push refspec set"
        if status["push"]
        else f"share them with 'git push origin {NOTES_REF}'"
    )
    problems = []
    if not status["fetch"]:
        problems.append(f"run 'git config --add remote.origin.fetch \"{FETCH_REFSPEC}\"'")
    if not status["rewrite_ref"]:
        problems.append(f"run 'git config --add notes.rewriteRef {NOTES_REF}'")
    if problems:
        return _check(name, False, "; ".join([*problems, push]))
    return _check(name, True, f"fetch refspec and notes.rewriteRef set; {push}")


def _events_config_check(repo_path: _DoctorPath) -> DoctorCheck | None:
    """The ``events:`` block, when it has values that fell back to defaults."""
    try:
        from repowise.core.events.store import load_event_settings

        errors = load_event_settings(repo_path).errors
    except Exception as exc:
        return _check("Events config", True, f"could not read it ({exc})")
    if not errors:
        return None
    return _check("Events config", False, "; ".join(errors) + " (defaults used)")


def _env_repo_covers(repo_path: _DoctorPath) -> bool:
    """Whether the repo ``GIT_DIR`` / ``GIT_WORK_TREE`` name holds ``repo_path`` (#1931).

    A linked worktree or container can have a ``.git`` file the explicit-path
    constructor cannot follow. Without those variables ``Repo()`` searches up
    from the cwd, which says nothing about ``repo_path``.
    """
    if not (os.environ.get("GIT_DIR") or os.environ.get("GIT_WORK_TREE")):
        return False
    from repowise.core.git_repo import Repo

    found = Repo()
    try:
        work_tree = found.working_tree_dir
    finally:
        found.close()
    if work_tree is None:
        return False
    return _DoctorPath(repo_path).resolve().is_relative_to(_DoctorPath(work_tree).resolve())


def _git_check(repo_path: _DoctorPath) -> DoctorCheck:
    try:
        from git import InvalidGitRepositoryError, NoSuchPathError

        from repowise.core.git_repo import Repo

        try:
            Repo(repo_path, search_parent_directories=True).close()
        except (InvalidGitRepositoryError, NoSuchPathError):
            if not _env_repo_covers(repo_path):
                raise
        return _check("Git repository", True, str(repo_path))
    except Exception:
        return _check("Git repository", False, "Not a git repo")


def _hook_rows(repo_path: _DoctorPath) -> list[DoctorCheck]:
    # The post-commit hook is installed by init by default; not having it is a
    # choice (--no-hook), so its absence is informational, not a fail.
    rows = [_autosync_hook_check(repo_path)]
    for optional in (_attribution_notes_check(repo_path), _events_config_check(repo_path)):
        if optional is not None:
            rows.append(optional)
    return rows


async def _count_pages(repo_path: _DoctorPath) -> int:
    from repowise.core.persistence import get_repository_by_path, get_session

    engine, sf = await open_store(repo_path)
    count = 0
    async with get_session(sf) as session:
        repo = await get_repository_by_path(session, str(repo_path))
        if repo:
            count = await _page_count(session, repo.id)
    await engine.dispose()
    return count


def _database_rows(ctx: CheckContext) -> list[DoctorCheck]:
    """Sets ``ctx.db_ok`` and ``ctx.page_count`` for the later groups."""
    probed = (ctx.repowise_dir / "wiki.db").exists() or db_configured()
    if not probed:
        return [_check("Database", False, "wiki.db not found")]
    try:
        ctx.page_count = run_async(_count_pages(ctx.repo_path))
        ctx.db_ok = True
    except Exception as e:
        return [_check("Database", False, str(e))]
    return [_check("Database", True, f"{ctx.page_count} pages")]


def _state_row(ctx: CheckContext) -> DoctorCheck:
    ctx.state = load_state(ctx.repo_path)
    ctx.state_ok = bool(ctx.state)
    return _check(
        "state.json",
        ctx.state_ok,
        f"last_sync: {(ctx.state.get('last_sync_commit') or '—')[:8]}"
        if ctx.state_ok
        else "Not found or empty",
    )


def _store_format_row(repo_path: _DoctorPath) -> DoctorCheck:
    """Does this store predate a capability the current build offers?

    A REINDEX_RECOMMENDED migration never auto-runs, so doctor is where an
    un-upgraded store learns a re-index is available. Reported OK, not FAIL:
    the store works degraded, it is not broken, and failing every pre-upgrade
    store's doctor run would cry wolf. The row carries the exact command so it
    is actionable even after the once-per-store update nag was suppressed.
    """
    try:
        from repowise.cli.upgrade import assess_store

        verdict = assess_store(repo_path)
        if verdict.reindex_recommended and verdict.reindex_command:
            notice = verdict.user_notice or "A re-index adds new capabilities."
            return _check("Store format", True, f"{notice} Run: {verdict.reindex_command}")
        return _check("Store format", True, "Current")
    except Exception as e:
        return _check("Store format", True, f"Could not check: {e}")


def _runtime_grammar_rows(repo_path: _DoctorPath) -> list[DoctorCheck]:
    """One row per runtime-downloaded grammar the repo needs, read from the cache only."""
    import subprocess

    from repowise.core.ingestion.languages.registry import REGISTRY
    from repowise.core.ingestion.parser.grammars import (
        downloads_grammar_at_runtime,
        runtime_grammar_cache,
    )

    by_ext = {
        ext: tag
        for ext, tag in REGISTRY.all_extensions().items()
        if downloads_grammar_at_runtime(tag)
    }
    try:
        listed = subprocess.run(
            # Untracked, unignored files too: ingestion walks them as well.
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=repo_path,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.CalledProcessError):
        return []
    counts: dict[str, int] = {}
    for path in filter(None, listed.split("\0")):
        tag = by_ext.get(os.path.splitext(path)[1].lower())
        if tag:
            counts[tag] = counts.get(tag, 0) + 1

    rows = []
    for tag, count in sorted(counts.items()):
        spec = REGISTRY.get(tag)
        name = f"{spec.display_name if spec else tag} grammar"
        try:
            cached, cache_dir = runtime_grammar_cache(tag)
        except Exception as exc:
            rows.append(_check(name, False, f"could not read the grammar cache ({exc})"))
            continue
        if cached:
            rows.append(_check(name, True, f"cached in {cache_dir}"))
        else:
            rows.append(
                _check(
                    name,
                    False,
                    f"not downloaded; {count:,} files would index without symbols. "
                    "Re-run 'repowise init' with network access, or set "
                    "REPOWISE_GRAMMAR_DIR to a pre-staged grammar cache first",
                )
            )
    return rows


def core_checks(ctx: CheckContext) -> list[DoctorCheck]:
    """Checks 1-4b, in order. Fills the database and state fields of *ctx*."""
    checks = [_git_check(ctx.repo_path)]
    # The MCP server refuses to start on a .repowise/ it cannot write, so an
    # existence-only row would say OK for the one state that fails every call.
    checks.append(_repowise_dir_check(ctx.repowise_dir))
    checks.extend(_hook_rows(ctx.repo_path))
    checks.extend(_database_rows(ctx))
    checks.append(_state_row(ctx))
    if ctx.state_ok:
        checks.append(_store_format_row(ctx.repo_path))
    checks.extend(_runtime_grammar_rows(ctx.repo_path))
    return checks
