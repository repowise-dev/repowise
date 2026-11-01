"""A ``repo`` path in another worktree: per-call state, revspecs and freshness."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.core.git_head import match_checkout
from repowise.server.mcp_server import _checkout, _meta

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.x",
    }
    done = subprocess.run(
        ["git", "-c", "core.hooksPath=", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return done.stdout.strip()


@pytest.fixture
def checkouts(tmp_path: Path) -> SimpleNamespace:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (repo / "pkg" / "b.py").write_text("B = 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "x", str(wt))
    (wt / "pkg" / "a.py").write_text("A = 2\n", encoding="utf-8")
    _git(wt, "commit", "-q", "-am", "edit a")
    _meta._dirty_paths_cache.clear()
    return SimpleNamespace(
        repo=repo.resolve(),
        wt=wt.resolve(),
        main_head=_git(repo, "rev-parse", "HEAD"),
        wt_head=_git(wt, "rev-parse", "HEAD"),
    )


@pytest.fixture
def call():
    token = _checkout.open_call()
    yield
    _checkout.close_call(token)


def _borrow(c: SimpleNamespace) -> None:
    assert not _checkout.note_match(match_checkout(c.wt, c.repo), c.repo)


def test_no_slot_means_nothing_recorded(checkouts) -> None:
    _checkout.note_match(match_checkout(checkouts.wt, checkouts.repo), checkouts.repo)
    assert _checkout.current() is None
    assert _checkout.checkout_meta() == {}


def test_same_checkout_records_nothing(checkouts, call) -> None:
    match = match_checkout(checkouts.repo / "pkg", checkouts.repo)
    assert not _checkout.note_match(match, checkouts.repo)
    assert _checkout.current() is None


def test_other_worktree_borrows_the_served_index(checkouts, call) -> None:
    _borrow(checkouts)
    caller = _checkout.current()
    assert caller.caller == checkouts.wt and caller.served == checkouts.repo
    assert not caller.own_index
    assert _checkout.checkout_meta() == {"served_checkout": str(checkouts.repo)}
    assert _checkout.borrowed_checkout(str(checkouts.repo)) is caller
    assert _checkout.borrowed_checkout(str(checkouts.wt)) is None
    assert _checkout.borrowed_checkout(None) is None


def test_worktree_with_an_index_is_its_own(checkouts, call) -> None:
    (checkouts.wt / ".repowise").mkdir()
    (checkouts.wt / ".repowise" / "wiki.db").write_bytes(b"")
    assert _checkout.note_match(match_checkout(checkouts.wt, checkouts.repo), checkouts.repo)
    caller = _checkout.current()
    assert caller.own_index
    assert _checkout.borrowed_checkout(str(checkouts.wt)) is None
    assert _checkout.caller_revspec("HEAD") == ("HEAD", None)
    row = SimpleNamespace(id="r", local_path=str(checkouts.wt))
    assert _meta.served_root(row) == str(checkouts.wt)


def test_revspecs_read_as_the_callers(checkouts, call) -> None:
    _borrow(checkouts)
    head = checkouts.wt_head
    assert _checkout.caller_revspec("HEAD~1..HEAD") == (f"{head}~1..{head}", None)
    assert _checkout.caller_revspec("main..@") == (f"main..{head}", None)
    assert _checkout.caller_revspec(checkouts.main_head) == (checkouts.main_head, None)
    assert _checkout.caller_revspec(None) == (head, None)
    refused = _checkout.caller_revspec("HEAD@{1}")[1]
    assert "reflog" in refused["error"] and "revspec" in refused["remedy"]


def test_uncommitted_caller_work_is_refused(checkouts, call) -> None:
    _borrow(checkouts)
    (checkouts.wt / "pkg" / "b.py").write_text("B = 3\n", encoding="utf-8")
    _meta._dirty_paths_cache.clear()
    revspec, refused = _checkout.caller_revspec(None)
    assert revspec is None and str(checkouts.wt) in refused["error"]


def test_unreadable_caller_head_refuses_head_but_keeps_shas(checkouts, call, monkeypatch) -> None:
    _borrow(checkouts)
    monkeypatch.setattr(_checkout, "read_head_commit", lambda _path: None)
    assert _checkout.caller_revspec("HEAD")[1] is not None
    assert _checkout.caller_revspec("abc..def") == ("abc..def", None)


def _row(c: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        id="r", head_commit=c.main_head, local_path=str(c.repo), updated_at=None
    )


def test_freshness_states_both_commits(checkouts, call) -> None:
    _borrow(checkouts)
    row = _row(checkouts)
    changed = _meta.freshness_from_repo(row, targets=["pkg/a.py"], root=str(checkouts.repo))
    fact = changed["stale_warning"]
    assert str(checkouts.repo) in fact and checkouts.wt_head[:12] in fact
    assert "differs in" in fact and "repowise update" not in fact
    assert changed["index_behind"] is True

    untouched = _meta.freshness_from_repo(row, targets=["pkg/b.py"], root=str(checkouts.repo))
    assert "stale_warning" not in untouched

    repo_level = _meta.freshness_from_repo(row, targets=None, root=str(checkouts.repo))
    assert f"is at {checkouts.wt_head[:12]}" in repo_level["stale_warning"]

    assert "stale_warning" not in _meta.freshness_from_repo(
        row, targets=[], root=str(checkouts.repo)
    )


def test_freshness_flags_uncommitted_caller_edits(checkouts, call) -> None:
    _borrow(checkouts)
    (checkouts.wt / "pkg" / "b.py").write_text("B = 4\n", encoding="utf-8")
    _meta._dirty_paths_cache.clear()
    out = _meta.freshness_from_repo(
        _row(checkouts), targets=["pkg/b.py"], root=str(checkouts.repo)
    )
    assert out["working_tree_dirty"] == 1
    assert "differs in" in out["stale_warning"]


def test_unknown_diff_is_not_called_a_file_change(checkouts, call, monkeypatch) -> None:
    _borrow(checkouts)
    monkeypatch.setattr(_meta, "_changed_files_between", lambda *_a: None)
    out = _meta.freshness_from_repo(
        _row(checkouts), targets=["pkg/b.py"], root=str(checkouts.repo)
    )
    assert "differs in" not in out["stale_warning"]


def test_envelope_names_the_served_checkout(checkouts, call) -> None:
    _borrow(checkouts)
    result = _meta.finalize_trust_envelope({"_meta": {}})
    assert result["_meta"]["served_checkout"] == str(checkouts.repo)


def test_scoped_targets_follow_the_tools(checkouts, monkeypatch) -> None:
    from repowise.server.mcp_server import _http_auth, _state

    monkeypatch.setattr(_state, "_registry", None)
    monkeypatch.setattr(_state, "_repo_path", str(checkouts.repo))
    monkeypatch.setattr(_state, "_served_repo_id", "rid")
    assert _http_auth._call_targets({"repo": str(checkouts.wt)}) == [("rid", "repo")]
    assert _http_auth._call_targets({"repo": "elsewhere"}) == [("elsewhere",)]


def test_own_index_contexts_are_kept_then_retired(tmp_path, monkeypatch) -> None:
    from repowise.core.workspace import registry as registry_mod
    from repowise.server.mcp_server import _server

    closed: list[Path] = []

    class _Registry:
        def __init__(self, root, config, embedder_factory=None):
            self.root = root

        async def get(self, alias):
            return SimpleNamespace(alias=alias, path=self.root)

        async def close(self):
            closed.append(self.root)

    monkeypatch.setattr(registry_mod, "RepoRegistry", _Registry)
    monkeypatch.setattr(_server, "_query_embedder", lambda: None)
    monkeypatch.setattr(_checkout, "_RETIRE_AFTER_S", 3600.0)

    async def scenario() -> None:
        roots = [tmp_path / f"w{i}" for i in range(3)]
        for root in roots:
            ctx = await _checkout.own_index_context("default", root)
            assert ctx.path == root and ctx.alias == "default"
        # The oldest is evicted but not closed while a call may hold it.
        assert closed == [] and len(_checkout._retiring) == 1
        await _checkout.close_own_index_contexts()
        assert set(closed) == set(roots)

    asyncio.run(scenario())


def test_a_retired_index_closes_when_its_timer_is_cancelled(monkeypatch) -> None:
    closed: list[str] = []

    class _Registry:
        async def close(self):
            closed.append("x")

    monkeypatch.setattr(_checkout, "_RETIRE_AFTER_S", 3600.0)

    async def scenario() -> None:
        _checkout._retire(_Registry())
        (task,) = list(_checkout._retiring)
        await asyncio.sleep(0)  # the timer is running
        task.cancel()  # e.g. the loop shutting down without the lifespan teardown
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert closed == ["x"] and not _checkout._retiring

    asyncio.run(scenario())
