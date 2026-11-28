"""A linked Bug issue makes a commit a fix, end to end: sync, update, and a fresh index agree.

The commit's message names no fix (``Handle empty carts (ENG-42)``), so the git
rule alone counts nothing. The fake tracker says ENG-42 is a Bug: after a sync
and an update, the commit is a fix in ``prior_defect_count``, ``fix_events``,
``git_commits.is_fix`` and the fix history an agent reads, quoting the issue,
and a fresh index of the same store holds the same rows.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from click.testing import CliRunner
from sqlalchemy import select

from repowise.cli.main import cli
from repowise.core.ingestion.git_indexer import (
    GIT_HISTORY_ISSUE_LINKS_REWALK,
    GIT_HISTORY_VERSION,
    GIT_HISTORY_VERSION_KEY,
)
from tests.unit.issues import tracker_double
from tests.unit.issues.tracker_double import SECRET, TRACKER, FakeTracker

_LINKED = "Handle empty carts (ENG-42)"


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True)
    return out.stdout.strip()


def _commit(repo: Path, name: str, body: str, message: str) -> str:
    (repo / name).write_text(body, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _commit(repo, "cart.py", "def total(items):\n    return sum(items)\n", "add cart")
    linked = _commit(repo, "cart.py", "def total(items):\n    return sum(items or [])\n", _LINKED)
    _commit(repo, "other.py", "x = 1\n", "add other")
    return repo, linked


def _index(repo: Path) -> None:
    from repowise.cli.helpers import save_state
    from repowise.core.pipeline.full_index import index_repo_full

    asyncio.run(index_repo_full(repo))
    save_state(
        repo,
        {
            "last_sync_commit": _git(repo, "rev-parse", "HEAD"),
            "docs_enabled": False,
            GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION,
        },
    )


async def _with_session(repo: Path, fn):
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.database import resolve_db_url
    from repowise.core.persistence.models import Repository

    engine = create_engine(resolve_db_url(repo))
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo_row = (await session.execute(select(Repository))).scalars().one()
            return await fn(session, repo_row.id)
    finally:
        await engine.dispose()


def _sync(repo: Path):
    from repowise.core.issues.sync import sync_issues

    return asyncio.run(
        _with_session(
            repo,
            lambda session, repo_id: sync_issues(
                session, repo_id, repo, FakeTracker(), credentials=SECRET
            ),
        )
    )


def _snapshot(repo: Path) -> dict:
    from repowise.core.persistence.crud.issue_links import fix_issue_quotes
    from repowise.core.persistence.models import FixEvent, GitCommit, GitMetadata

    async def read(session, repo_id):
        meta = {
            m.file_path: m.prior_defect_count
            for m in (await session.execute(select(GitMetadata))).scalars()
        }
        events = sorted(
            (e.fix_sha, e.file_path, e.shape_kind)
            for e in (await session.execute(select(FixEvent))).scalars()
        )
        commits = {
            c.sha: (c.is_fix, c.change_risk_score)
            for c in (await session.execute(select(GitCommit))).scalars()
        }
        quotes = await fix_issue_quotes(session, repo_id, ["cart.py"])
        return {"meta": meta, "events": events, "commits": commits, "quotes": quotes}

    return asyncio.run(_with_session(repo, read))


def _update(repo: Path):
    from repowise.cli.commands.update_cmd.command import clear_update_queued, release_update_lock

    result = CliRunner().invoke(cli, ["update", str(repo), "--no-workspace"])
    # atexit does not run under CliRunner, so the lock outlives the run.
    release_update_lock(repo)
    clear_update_queued(repo)
    return result


def _state(repo: Path) -> dict:
    return json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))


def test_a_linked_bug_counts_as_a_fix_after_sync_and_update(tmp_path: Path) -> None:
    repo, linked = _repo(tmp_path)
    _index(repo)
    before = _snapshot(repo)
    assert before["meta"]["cart.py"] == 0
    assert before["events"] == []
    assert before["commits"][linked][0] is False

    TRACKER.clear()
    TRACKER["ENG-42"] = ("bug", "Done", "Checkout fails on an empty cart")
    try:
        outcome = _sync(repo)
    finally:
        TRACKER.clear()
    assert outcome.status == "ok", outcome.error
    assert outcome.stored[0]["defect_changed"] == [linked]
    # The commit is already indexed: the next update re-reads history once.
    assert _state(repo)[GIT_HISTORY_VERSION_KEY] == GIT_HISTORY_ISSUE_LINKS_REWALK

    result = _update(repo)
    assert result.exit_code == 0, result.output
    assert "Linked bug issues changed" in result.output
    assert _state(repo)[GIT_HISTORY_VERSION_KEY] == GIT_HISTORY_VERSION

    after = _snapshot(repo)
    assert after["meta"]["cart.py"] == 1
    assert after["events"] == [(linked, "cart.py", "code_fix")]
    assert after["commits"][linked][0] is True
    # ``is_fix`` is reported beside the score, never scored: no score moves.
    assert after["commits"][linked][1] == before["commits"][linked][1]
    assert after["quotes"] == {"cart.py": ["ENG-42 (bug): Checkout fails on an empty cart"]}

    # A fresh index of the same store gives the same rows.
    (repo / ".repowise" / "state.json").unlink()
    _index(repo)
    assert _snapshot(repo) == after


def test_a_link_that_stops_being_a_bug_retracts_the_fix(tmp_path: Path) -> None:
    repo, linked = _repo(tmp_path)
    _index(repo)
    TRACKER["ENG-42"] = ("bug", "Done", "Checkout fails on an empty cart")
    try:
        _sync(repo)
        _update(repo)
        assert _snapshot(repo)["meta"]["cart.py"] == 1

        TRACKER["ENG-42"] = ("feature", "Done", "Empty carts")
        outcome = _sync(repo)
    finally:
        TRACKER.clear()
    assert outcome.stored[0]["defect_changed"] == [linked]
    result = _update(repo)
    assert result.exit_code == 0, result.output

    after = _snapshot(repo)
    assert after["meta"]["cart.py"] == 0
    assert after["events"] == []
    assert after["commits"][linked][0] is False
    assert after["quotes"] == {}


def test_a_refused_token_stores_nothing_and_says_why(tmp_path: Path) -> None:
    from repowise.core.issues.sync import sync_issues

    repo, _ = _repo(tmp_path)
    _index(repo)
    outcome = asyncio.run(
        _with_session(
            repo,
            lambda session, repo_id: sync_issues(
                session, repo_id, repo, FakeTracker(), credentials="wrong"
            ),
        )
    )
    assert outcome.status == "refused"
    assert outcome.error == "the tracker refused the token; set FAKE_TRACKER_TOKEN"
    assert SECRET not in repr(outcome)
    assert _state(repo)[GIT_HISTORY_VERSION_KEY] == GIT_HISTORY_VERSION


def test_the_double_loads_as_a_plugin(monkeypatch) -> None:
    import importlib.metadata

    from repowise.core.issues import issue_sources
    from repowise.core.registry import plugins

    monkeypatch.setenv(plugins.ENV_VAR, f"{tracker_double.__name__}:register")
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kw: [])
    plugins.reset()
    try:
        [record] = plugins.load_plugins()
        assert record.status == "loaded", record.error
        assert "issue-source:fake-tracker" in record.seams
        assert isinstance(issue_sources.get("fake-tracker"), FakeTracker)
    finally:
        plugins.reset()
