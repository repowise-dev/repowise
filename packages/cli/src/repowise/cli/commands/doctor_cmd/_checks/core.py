"""Checks 1-4b: git, ``.repowise/``, hooks, settings, database, state.json, freshness, store format."""

from __future__ import annotations

import os
from pathlib import Path as _DoctorPath

from repowise.cli.helpers import db_configured, read_state, run_async

from .._types import DoctorCheck, _check
from .context import CheckContext, open_store
from .settings import settings_rows


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
        return _check(
            ".repowise/ directory",
            False,
            f"{repowise_dir} (run 'repowise init')",
            check_id="repowise-dir",
            fix="repowise init",
        )
    probe = repowise_dir / ".doctor-write-probe"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return _check(
            ".repowise/ directory",
            False,
            f"{repowise_dir} is not writable ({exc.strerror or exc}); fix its permissions",
            check_id="repowise-dir",
        )
    return _check(".repowise/ directory", True, str(repowise_dir), check_id="repowise-dir")


def _autosync_hook_check(repo_path: _DoctorPath) -> DoctorCheck:
    """Report the post-commit hook through hooks.status, which asks git for the
    real hooks directory, so worktrees and core.hooksPath read correctly.

    Never a fail: init installs it by default and ``--no-hook`` opts out, so
    a missing hook is a warn naming the command that turns auto-sync on."""
    name, check_id = "Post-commit hook", "post-commit-hook"
    try:
        from repowise.cli.hooks import status as _hook_status

        state = _hook_status(repo_path)
    except Exception as exc:
        detail = f"could not read hook state ({exc})"
        return _check(name, True, detail, check_id=check_id, warn=True)
    if state.startswith("installed"):
        return _check(name, True, state, check_id=check_id)
    if state == "not installed":
        return _check(
            name,
            True,
            "not installed; the index updates only when you run 'repowise update' "
            "('repowise hook install' turns auto-sync on)",
            check_id=check_id,
            warn=True,
            fix="repowise hook install",
        )
    # "not a git repository" is already the git row's fail; "unreadable" is not.
    return _check(name, True, state, check_id=check_id, warn=state == "unreadable")


def _attribution_notes_check(repo_path: _DoctorPath) -> DoctorCheck | None:
    """At ``attribution.capture: notes``, whether notes travel with the repo.

    None at every other level. Notes reach teammates only through a fetch
    refspec and an explicit push, and survive a rebase only via
    ``notes.rewriteRef``; without them coverage is silently low elsewhere.
    """
    name, check_id = "Attribution notes", "attribution-notes"
    try:
        from repowise.core.attribution import load_attribution_settings

        if load_attribution_settings(repo_path).capture != "notes":
            return None
        from repowise.core.edit_capture.notes import FETCH_REFSPEC, NOTES_REF, refspec_status

        status = refspec_status(_DoctorPath(repo_path))
    except OSError as exc:
        detail = f"could not read git config ({exc})"
        return _check(name, True, detail, check_id=check_id, warn=True)
    if not status["remote"]:
        return _check(
            name, True, "no origin remote, so notes stay on this machine", check_id=check_id
        )
    push = (
        "push refspec set"
        if status["push"]
        else f"share them with 'git push origin {NOTES_REF}'"
    )
    commands = []
    if not status["fetch"]:
        commands.append(f'git config --add remote.origin.fetch "{FETCH_REFSPEC}"')
    if not status["rewrite_ref"]:
        commands.append(f"git config --add notes.rewriteRef {NOTES_REF}")
    if commands:
        problems = [f"run '{c}'" for c in commands]
        # One command is the fix; two are listed in the detail instead.
        fix = commands[0] if len(commands) == 1 else None
        return _check(name, False, "; ".join([*problems, push]), check_id=check_id, fix=fix)
    return _check(name, True, f"fetch refspec and notes.rewriteRef set; {push}", check_id=check_id)


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
        return _check("Git repository", True, str(repo_path), check_id="git-repository")
    except Exception:
        return _check("Git repository", False, "Not a git repo", check_id="git-repository")


def _hook_rows(repo_path: _DoctorPath) -> list[DoctorCheck]:
    # The post-commit hook is installed by init by default; not having it is a
    # choice (--no-hook), so its absence is informational, not a fail.
    rows = [_autosync_hook_check(repo_path)]
    notes = _attribution_notes_check(repo_path)
    if notes is not None:
        rows.append(notes)
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
        return [
            _check("Database", False, "wiki.db not found", check_id="database", fix="repowise init")
        ]
    try:
        ctx.page_count = run_async(_count_pages(ctx.repo_path))
        ctx.db_ok = True
    except Exception as e:
        return [_check("Database", False, str(e), check_id="database")]
    return [_check("Database", True, f"{ctx.page_count} pages", check_id="database")]


def _state_row(ctx: CheckContext) -> DoctorCheck:
    result = read_state(ctx.repo_path)
    ctx.state = result.state
    ctx.state_ok = result.status == "ok"
    if result.status == "ok":
        detail = f"last_sync: {(ctx.state.get('last_sync_commit') or '—')[:8]}"
    elif result.status == "missing":
        detail = "missing"
    elif result.status == "empty":
        detail = "Not found or empty"
    else:
        state_path = ctx.repowise_dir / "state.json"
        detail = f"unreadable: {state_path}: {result.error}"
    return _check(
        "state.json",
        ctx.state_ok,
        detail,
        check_id="state-json",
        fix=None if ctx.state_ok else "repowise init",
    )


def _index_freshness_row(repo_path: _DoctorPath, state: dict) -> DoctorCheck | None:
    """Whether the index still describes HEAD, as the web Setup page judges it.

    HEAD is read from the ref files, so a quick run spawns nothing while the
    two agree; git is asked only once they differ, to tell a commit HEAD moved
    past from one this history no longer has (a rebase, a reset, another
    clone's state.json). ``None`` without both commits: the git and state.json
    rows already cover those.
    """
    from repowise.core.git_head import read_head_commit
    from repowise.core.index_freshness import commit_state, indexed_commit_missing

    name, check_id = "Index freshness", "index-freshness"
    indexed = state.get("last_sync_commit")
    live = read_head_commit(repo_path)
    if not indexed or not live:
        return None
    if commit_state(indexed, live) == "up_to_date":
        return _check(name, True, f"indexed at HEAD ({live[:8]})", check_id=check_id)
    if indexed_commit_missing(repo_path, indexed, live):
        return _check(
            name,
            True,
            f"last_sync_commit {indexed[:8]} is not in this repository's history, so "
            "'repowise update' has no base to diff from; pass a commit the index "
            "covers with --since",
            check_id=check_id,
            warn=True,
            fix="repowise update --since <ref>",
        )
    return _check(
        name,
        True,
        f"indexed at {indexed[:8]}; HEAD has moved on to {live[:8]}",
        check_id=check_id,
        warn=True,
        fix="repowise update",
    )


def _update_lock_row(repo_path: _DoctorPath) -> DoctorCheck | None:
    """A live update holding the lock past the suspect age, worded as ``update`` words it."""
    from repowise.core.update_lock import stuck_update_line

    detail = stuck_update_line(repo_path)
    if detail is None:
        return None
    return _check("Update lock", True, detail, check_id="update-lock", warn=True)


def _store_format_row(repo_path: _DoctorPath) -> DoctorCheck:
    """Does this store predate a capability the current build offers?

    A REINDEX_RECOMMENDED migration never auto-runs, so doctor is where an
    un-upgraded store learns a re-index is available. Reported WARN, not FAIL:
    the store works degraded, it is not broken, and failing every pre-upgrade
    store's doctor run would cry wolf. The row carries the exact command so it
    is actionable even after the once-per-store update nag was suppressed.
    """
    name, check_id = "Store format", "store-format"
    try:
        from repowise.cli.upgrade import assess_store

        verdict = assess_store(repo_path)
        if verdict.reindex_recommended and verdict.reindex_command:
            notice = verdict.user_notice or "A re-index adds new capabilities."
            return _check(
                name,
                True,
                f"{notice} Run: {verdict.reindex_command}",
                check_id=check_id,
                warn=True,
                fix=verdict.reindex_command,
            )
        if verdict.rebuilds_wiki:
            # Owed until an update rebuilds the wiki; a model wiki waits on the cost question.
            return _check(
                name,
                True,
                "The wiki is due a one-time rebuild for this release. Run: repowise update --yes",
                check_id=check_id,
                warn=True,
                fix="repowise update --yes",
            )
        return _check(name, True, "Current", check_id=check_id)
    except Exception as e:
        return _check(name, True, f"Could not check: {e}", check_id=check_id, warn=True)


def _oversize_source_rows(repo_path: _DoctorPath) -> list[DoctorCheck]:
    """One row naming the source files ingestion drops on size (#5415).

    A file excluded at ingestion leaves no index row, so no other check here can
    see it: the repo reported healthy while its largest classes were absent from
    every search and every answer. The verdict comes from ``size_verdict``, the
    same rule that dropped them, so this row cannot drift from the exclusion.

    A warning rather than a failure: dropping a 12 MB generated file is correct,
    and the point is that the user is told which files it happened to.
    """
    import subprocess

    from repowise.core.ingestion.traverser import size_verdict

    try:
        listed = subprocess.run(
            # Untracked, unignored files too: ingestion walks them as well.
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=repo_path,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return []

    skipped: list[tuple[str, int]] = []
    for rel in filter(None, listed.split("\0")):
        abs_path = repo_path / rel
        try:
            size = abs_path.stat().st_size
        except OSError:
            continue
        verdict = size_verdict(abs_path, size)
        if verdict is not None and verdict.is_source:
            skipped.append((rel, size))

    if not skipped:
        return []
    skipped.sort(key=lambda item: -item[1])
    shown = ", ".join(f"{rel} ({size // 1024:,} KB)" for rel, size in skipped[:5])
    more = "" if len(skipped) <= 5 else f", and {len(skipped) - 5} more"
    return [
        _check(
            "Oversize sources",
            True,
            f"{len(skipped)} source file(s) exceed the ingestion size ceiling, so "
            f"they have no symbols and no pages: {shown}{more}. Split them or raise "
            "the ceiling.",
            check_id="oversize-sources",
            warn=True,
        )
    ]


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
        check_id = f"grammar-{tag.lower().replace('_', '-')}"
        try:
            cached, cache_dir = runtime_grammar_cache(tag)
        except Exception as exc:
            rows.append(
                _check(name, False, f"could not read the grammar cache ({exc})", check_id=check_id)
            )
            continue
        if cached:
            rows.append(_check(name, True, f"cached in {cache_dir}", check_id=check_id))
        else:
            rows.append(
                _check(
                    name,
                    False,
                    f"not downloaded; {count:,} files would index without symbols. "
                    "Re-run 'repowise init' with network access, or stage a cache with "
                    "'repowise grammars fetch <dir>' and set REPOWISE_GRAMMAR_DIR to it",
                    check_id=check_id,
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
    checks.extend(settings_rows(ctx))
    checks.extend(_database_rows(ctx))
    checks.append(_state_row(ctx))
    if ctx.state_ok:
        freshness = _index_freshness_row(ctx.repo_path, ctx.state)
        if freshness is not None:
            checks.append(freshness)
        checks.append(_store_format_row(ctx.repo_path))
    stuck = _update_lock_row(ctx.repo_path)
    if stuck is not None:
        checks.append(stuck)
    checks.extend(_runtime_grammar_rows(ctx.repo_path))
    checks.extend(_oversize_source_rows(ctx.repo_path))
    return checks
