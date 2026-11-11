"""A linked worktree with no index of its own reads its main checkout's index.

Built on a real ``git worktree add``: the hooks resolve the main checkout's
store, relativise paths against the worktree, write their ledger rows to the
main store, and stay quiet where the main checkout's index cannot describe the
branch.
"""

from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli._helpers.paths import WORKTREE_FALLBACK_ENV, borrowed_index_base
from repowise.cli.commands.augment_cmd import _shared
from repowise.cli.commands.augment_cmd.bash_staleness import _handle_bash_post
from repowise.cli.commands.augment_cmd.command import _run_augment
from repowise.cli.commands.augment_cmd.coverage_reingest import _local_index
from repowise.cli.commands.augment_cmd.session_start import _handle_claude_session_start
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import GitMetadata, Repository

_PATH = "src/core/pipeline.py"


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


async def _build_wiki_db(main: Path) -> None:
    """An index of *main* whose one file has a recent run of bug fixes."""
    db_path = main / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Repository(id="repo1", name="main", local_path=str(main)))
        session.add(
            GitMetadata(
                id="gm1",
                repository_id="repo1",
                file_path=_PATH,
                prior_defect_count=5,
                last_fix_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=3),
            )
        )
        await session.commit()
    await engine.dispose()


@pytest.fixture(autouse=True)
def _fresh_roots(monkeypatch):
    monkeypatch.delenv(WORKTREE_FALLBACK_ENV, raising=False)
    _shared._find_roots.cache_clear()
    yield
    _shared._find_roots.cache_clear()


@pytest.fixture()
def main(tmp_path: Path) -> Path:
    repo = tmp_path / "main"
    (repo / "src" / "core").mkdir(parents=True)
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@t.t"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / _PATH).write_text("def run_pipeline():\n    return 1\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".repowise/\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "init"], repo)
    return repo


@pytest.fixture()
def indexed(main: Path) -> Path:
    asyncio.run(_build_wiki_db(main))
    head = _git(["rev-parse", "HEAD"], main)
    (main / ".repowise" / "state.json").write_text(
        json.dumps({"last_sync_commit": head}), encoding="utf-8"
    )
    return main


@pytest.fixture()
def worktree(main: Path, tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    _git(["worktree", "add", "-b", "feature", str(wt)], main)
    return wt.resolve()


def _run_hook(payload: dict, monkeypatch, tmp_path: Path) -> str:
    out = io.StringIO()
    marker_dir = tmp_path / "markers"
    marker_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(tempfile, "tempdir", str(marker_dir))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "stdout", out)
    _run_augment(client=None)
    return out.getvalue()


def _edit_payload(worktree: Path, session_id: str) -> dict:
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(worktree / _PATH)},
        "tool_response": {},
        "cwd": str(worktree),
        "session_id": session_id,
    }


def test_edit_notice_fires_in_worktree_and_ledger_lands_in_main(
    indexed: Path, worktree: Path, monkeypatch, tmp_path: Path
) -> None:
    # Checked first: with no fallback the walk would carry on above tmp_path
    # and run the hook against whatever store it meets there.
    assert _shared._find_repo_root(worktree) == indexed.resolve()
    _shared._find_roots.cache_clear()
    out = _run_hook(_edit_payload(worktree, "sess-wt"), monkeypatch, tmp_path)

    context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "bug-fixed 5x" in context
    # The path is the worktree's repo-relative one, the same the index keys on.
    assert _PATH in context

    con = sqlite3.connect(indexed / ".repowise" / "sessions" / "sessions.db")
    try:
        runs = con.execute(
            "SELECT event, tool, emitted FROM hook_runs WHERE session_id = 'sess-wt'"
        ).fetchall()
        injections = con.execute(
            "SELECT surface FROM injections WHERE session_id = 'sess-wt'"
        ).fetchall()
    finally:
        con.close()
    assert runs == [("PostToolUse", "Edit", 1)]
    assert injections
    # Nothing was created in the worktree: the post-commit hook keys on
    # `.repowise/state.json` there and must stay inert.
    assert not (worktree / ".repowise").exists()


def test_roots_split_checkout_from_store(indexed: Path, worktree: Path) -> None:
    nested = worktree / "src" / "core"
    assert _shared._find_repo_root(nested) == indexed.resolve()
    assert _shared._checkout_root(nested, indexed.resolve()) == worktree
    assert _shared._borrowed_index(nested, indexed.resolve())
    # The main checkout itself is unchanged.
    assert _shared._checkout_root(indexed, indexed.resolve()) == indexed.resolve()
    assert not _shared._borrowed_index(indexed, indexed.resolve())


def test_silent_when_main_checkout_has_no_index(main: Path, worktree: Path) -> None:
    assert borrowed_index_base(worktree) is None
    roots = _shared._find_roots(worktree)
    assert roots is None or main.resolve() not in roots


def test_worktree_with_its_own_index_reads_its_own(indexed: Path, worktree: Path) -> None:
    (worktree / ".repowise").mkdir()
    assert _shared._find_roots(worktree) == (worktree, worktree)


def test_opt_out_env_turns_the_fallback_off(
    indexed: Path, worktree: Path, monkeypatch
) -> None:
    monkeypatch.setenv(WORKTREE_FALLBACK_ENV, "0")
    assert borrowed_index_base(worktree) is None


def test_nested_plain_repo_is_not_taken_for_a_worktree(indexed: Path) -> None:
    nested = indexed / "vendor" / "other"
    nested.mkdir(parents=True)
    _git(["init"], nested)
    assert borrowed_index_base(nested) is None
    # Same answer as before worktrees were considered: the enclosing index.
    assert _shared._find_roots(nested) == (indexed.resolve(), indexed.resolve())


def test_unregistered_gitdir_pointer_does_not_borrow(indexed: Path, tmp_path: Path) -> None:
    """A hand-written `.git` file cannot claim a repository's index."""
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / ".git").write_text(f"gitdir: {indexed / '.git'}\n", encoding="utf-8")
    assert borrowed_index_base(fake) is None


def test_commit_in_worktree_does_not_warn_about_main_index(
    indexed: Path, worktree: Path
) -> None:
    # The main checkout has moved past its index, which a commit made there
    # would be told about; a commit in the worktree says nothing about it.
    (indexed / "main_only.py").write_text("y = 1\n", encoding="utf-8")
    _git(["add", "main_only.py"], indexed)
    _git(["commit", "-m", "main work"], indexed)
    (worktree / "new.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], worktree)
    _git(["commit", "-m", "branch work"], worktree)
    notice = _handle_bash_post(
        {"command": "git commit -m 'branch work'"}, {"exit_code": 0}, str(worktree)
    )
    assert notice is None


def test_coverage_ingest_never_targets_main_store_from_worktree(
    indexed: Path, worktree: Path, monkeypatch
) -> None:
    monkeypatch.setenv("REPOWISE_HOOK_COVERAGE_REINGEST", "1")
    assert _local_index(str(worktree)) is None
    assert _local_index(str(indexed)) is not None


def test_session_start_describes_the_borrowed_index_without_update_advice(
    indexed: Path, worktree: Path
) -> None:
    (worktree / "new.py").write_text("x = 1\n", encoding="utf-8")
    _git(["add", "-A"], worktree)
    _git(["commit", "-m", "branch work"], worktree)

    block = _handle_claude_session_start(str(worktree), "")

    assert block is not None
    assert f"reads the codebase index of {indexed.resolve()}" in block
    assert "1 files differ" in block
    assert "repowise update" not in block


def test_rewrite_hook_takes_main_checkout_opt_in(indexed: Path, worktree: Path) -> None:
    from repowise.cli.rewrite_hook import _find_repo_root

    assert Path(_find_repo_root(str(worktree))) == indexed.resolve()


def test_mcp_server_started_in_worktree_serves_main_for_the_worktree(
    indexed: Path, worktree: Path
) -> None:
    from repowise.cli.commands.mcp_cmd import _borrowed_index
    from repowise.server.mcp_server import _checkout

    assert _borrowed_index(worktree, worktree) == (worktree, indexed.resolve())
    # A path that holds its own index is served as itself.
    assert _borrowed_index(indexed, indexed) is None

    _checkout.serve_worktree(worktree, indexed.resolve())
    try:
        token = _checkout.open_call()
        try:
            caller = _checkout.current()
            assert caller is not None and caller.caller == worktree
            assert _checkout.borrowed_checkout(str(indexed.resolve())) == caller
        finally:
            _checkout.close_call(token)
    finally:
        _checkout._server_caller = None
