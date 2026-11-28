"""A linked Bug issue makes a commit a fix, end to end: sync, update, and a fresh index agree.

The commit's message names no fix (``Handle empty carts (ENG-42)``), so the git
rule alone counts nothing. The fake tracker says ENG-42 is a Bug: after a sync
and an update, the commit is a fix in ``prior_defect_count``, ``fix_events``,
``git_commits.is_fix``, the health finding, the change category and the fix
history an agent reads, quoting the issue, and a fresh index of the same store
holds the same rows.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from repowise.cli.main import cli
from repowise.core.ingestion.git_indexer import (
    GIT_HISTORY_ISSUE_LINKS_REWALK,
    GIT_HISTORY_VERSION,
    GIT_HISTORY_VERSION_KEY,
)
from tests.unit.issues import tracker_double
from tests.unit.issues.tracker_double import SECRET, TOKEN, TRACKER, FakeTracker

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
    from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
    from repowise.core.pipeline.full_index import index_repo_full

    asyncio.run(index_repo_full(repo))
    save_state(
        repo,
        {
            "last_sync_commit": _git(repo, "rev-parse", "HEAD"),
            "docs_enabled": False,
            GIT_HISTORY_VERSION_KEY: GIT_HISTORY_VERSION,
            # What init stamps: no re-score is due for age or analyzer, so only
            # the history re-walk can bring the health rows up to date.
            "health_analyzer_version": HEALTH_ANALYZER_VERSION,
            "last_full_rescore_at": float(_git(repo, "log", "-1", "--format=%ct")),
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
                session, repo_id, repo, FakeTracker(), credentials={TOKEN.key: SECRET}
            ),
        )
    )


def _snapshot(repo: Path) -> dict:
    from repowise.core.persistence.crud.issue_links import fix_issue_quotes
    from repowise.core.persistence.models import (
        Change,
        FixEvent,
        GitCommit,
        GitMetadata,
        HealthFinding,
    )

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
        health = sorted(
            (f.file_path, f.severity, json.loads(f.details_json or "{}").get("prior_defect_count"))
            for f in (await session.execute(select(HealthFinding))).scalars()
            if f.biomarker_type == "prior_defect"
        )
        categories = {
            c.head_sha: c.category for c in (await session.execute(select(Change))).scalars()
        }
        return {
            "meta": meta,
            "events": events,
            "commits": commits,
            "quotes": quotes,
            "health": health,
            "categories": categories,
        }

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
    assert after["health"] == [("cart.py", "low", 1)]
    assert after["categories"][linked] == "fix"
    assert before["categories"][linked] != "fix"

    # A fresh index of the same store gives the same rows.
    (repo / ".repowise" / "state.json").unlink()
    _index(repo)
    assert _snapshot(repo) == after


def test_one_issue_counts_once_per_file_however_many_commits_link_to_it(tmp_path: Path) -> None:
    repo, linked = _repo(tmp_path)
    body = "def total(items):\n    return sum(items or ())\n"
    second = _commit(repo, "cart.py", body, "Use a tuple (ENG-42)")
    _index(repo)
    TRACKER["ENG-42"] = ("bug", "Done", "Checkout fails on an empty cart")
    try:
        _sync(repo)
    finally:
        TRACKER.clear()
    result = _update(repo)
    assert result.exit_code == 0, result.output

    after = _snapshot(repo)
    # Two commits, one bug: one defect on cart.py. Each commit keeps its row,
    # the newer one marked as repeating the issue the older one counts.
    assert after["meta"]["cart.py"] == 1
    assert after["events"] == sorted(
        [(linked, "cart.py", "code_fix"), (second, "cart.py", "issue_repeat")]
    )
    assert after["commits"][linked][0] is True and after["commits"][second][0] is True

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
    assert after["health"] == []
    assert after["categories"][linked] != "fix"


def test_a_refused_token_stores_nothing_and_says_why(tmp_path: Path) -> None:
    from repowise.core.issues.sync import sync_issues

    repo, _ = _repo(tmp_path)
    _index(repo)
    outcome = asyncio.run(
        _with_session(
            repo,
            lambda session, repo_id: sync_issues(
                session, repo_id, repo, FakeTracker(), credentials={TOKEN.key: "wrong"}
            ),
        )
    )
    assert outcome.status == "refused"
    assert outcome.error == "the tracker refused the token; set FAKE_TRACKER_TOKEN"
    assert SECRET not in repr(outcome)
    assert _state(repo)[GIT_HISTORY_VERSION_KEY] == GIT_HISTORY_VERSION


@pytest.fixture
def installed(monkeypatch):
    """The fake tracker loaded as the only plugin, its token set; tables put back afterwards."""
    import importlib.metadata

    from repowise.core.issues import issue_sources
    from repowise.core.registry import plugins
    from tests.unit.settings.plugin_double import _restore, _tables

    saved = _tables()
    impls, owners = dict(issue_sources._impls), dict(issue_sources._owners)
    eps = [
        importlib.metadata.EntryPoint(
            "faketracker", f"{tracker_double.__name__}:register", plugins.ENTRY_POINT_GROUP
        )
    ]
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: eps if kw.get("group") == plugins.ENTRY_POINT_GROUP else [],
    )
    monkeypatch.delenv(plugins.ENV_VAR, raising=False)
    monkeypatch.setenv(TOKEN.env.name, SECRET)
    plugins.reset()
    try:
        plugins.load_plugins()
        yield
    finally:
        plugins.reset()
        issue_sources._impls.clear()
        issue_sources._impls.update(impls)
        issue_sources._owners.clear()
        issue_sources._owners.update(owners)
        _restore(saved)


def test_update_syncs_the_tracker_first_and_counts_a_new_linked_commit(tmp_path, installed):
    repo, _ = _repo(tmp_path)
    _index(repo)
    TRACKER["ENG-7"] = ("incident", "Done", "Login loops after a password reset")
    try:
        new = _commit(repo, "other.py", "x = 2\n", "Retry the session read (ENG-7)")
        result = _update(repo)
    finally:
        TRACKER.clear()
    assert result.exit_code == 0, result.output
    assert "issue-source fake-tracker: stored" in " ".join(result.output.split())
    # Not indexed when the link was stored, so this update counted it without a re-walk.
    assert _state(repo)[GIT_HISTORY_VERSION_KEY] == GIT_HISTORY_VERSION
    after = _snapshot(repo)
    assert after["meta"]["other.py"] == 1
    assert (new, "other.py", "code_fix") in after["events"]
    assert after["commits"][new][0] is True


def test_the_double_loads_as_a_plugin(installed) -> None:
    from repowise.core.issues import issue_sources
    from repowise.core.registry import plugins

    [record] = plugins.load_plugins()
    assert record.status == "loaded", record.error
    assert "issue-source:fake-tracker" in record.seams
    assert isinstance(issue_sources.get("fake-tracker"), FakeTracker)


def test_repowise_plugins_lists_the_issue_source(installed) -> None:
    from repowise.cli.commands.plugins_cmd import plugins_report

    rows = {s["source"]: s for s in plugins_report()["sources"] if s["seam"] == "issue-source"}
    row = rows["fake-tracker"]
    assert (row["source"], row["plugin"], row["configured"]) == (
        "fake-tracker",
        "faketracker",
        True,
    )
    # The built-in forge source is listed beside it, with no plugin.
    assert rows["forge"]["plugin"] is None
