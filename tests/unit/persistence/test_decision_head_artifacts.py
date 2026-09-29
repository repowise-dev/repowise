"""A decision whose named files are all gone at HEAD reads as stale history.

Renames are followed, so a moved file is not missing; a deleted directory is.
The record is never retired for this: its status and successor stay as they were.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from repowise.core.analysis.decisions.head_artifacts import (
    HeadTree,
    apply_head_artifact_check,
    artifact_paths,
)
from repowise.core.analysis.decisions.lifecycle import effective_currency
from repowise.core.persistence.crud.authority import (
    count_decisions_by_lane,
    current_currency,
)
from repowise.core.persistence.models import DecisionRecord
from tests.unit.persistence.helpers import accept, insert_repo


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _write(repo: Path, rel: str, text: str = "x = 1\n") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "config", "commit.gpgsign", "false")
    for rel in (
        "src/old_name.py",
        "legacy/a.py",
        "legacy/b.py",
        "pkg/core.py",
        "pkg/util.py",
        "tools/why.py",
    ):
        _write(repo, rel, f"# {rel}\n" + "value = 1\n" * 20)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    # A rename, then a second rename of the same file: the trail follows both.
    _git(repo, "mv", "src/old_name.py", "src/mid_name.py")
    _git(repo, "commit", "-q", "-m", "rename once")
    (repo / "lib").mkdir()
    _git(repo, "mv", "src/mid_name.py", "lib/new_name.py")
    _git(repo, "commit", "-q", "-m", "rename twice")
    # A whole directory deleted, and another moved.
    _git(repo, "rm", "-q", "-r", "legacy")
    _git(repo, "mv", "pkg", "core")
    _git(repo, "commit", "-q", "-m", "drop legacy, move pkg")
    # A module split into a package of the same name, too different to be a rename.
    _git(repo, "rm", "-q", "tools/why.py")
    _write(repo, "tools/why/__init__.py", "from .modes import run\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "split why into a package")
    return repo


async def _record(session, repo_id: str, title: str, files: list[str], **kw) -> DecisionRecord:
    rec = DecisionRecord(
        repository_id=repo_id,
        title=title,
        source="git_archaeology",
        affected_files_json=json.dumps(files),
        **kw,
    )
    session.add(rec)
    await session.flush()
    return rec


def test_artifact_paths_reads_files_and_backticked_paths():
    rec = DecisionRecord(
        title="Use `src/app.py` for the entry point",
        affected_files_json=json.dumps(["src/a.py"]),
        decision="Keep config in `conf/settings.toml`, never in prose like and/or.",
        rationale="",
        context="",
    )
    assert artifact_paths(rec) == ["src/a.py", "src/app.py", "conf/settings.toml"]


def test_head_tree_follows_renames_and_directory_moves():
    tree = HeadTree(
        ["lib/new.py", "core/x.py"],
        [("src/mid.py", "lib/new.py"), ("src/old.py", "src/mid.py"), ("pkg/x.py", "core/x.py")],
    )
    assert tree.present("src/old.py")
    assert tree.present("pkg")  # every file moved, so the directory lives on
    assert tree.present("new.py")  # a bare name found anywhere
    assert tree.present("core.py")  # a module split into the package core/
    assert tree.present("/abs/path.py")  # not repo-relative, so not judged
    assert not tree.present("legacy")
    assert tree.all_gone(["legacy/a.py", "gone.py"])
    assert not tree.all_gone(["legacy/a.py", "src/old.py"])
    assert not tree.all_gone([])


def test_stale_is_derived_only_for_an_active_scoped_record():
    assert (
        effective_currency("active", has_scope=True, staleness=1.0, artifacts_gone=True) == "stale"
    )
    assert effective_currency("active", has_scope=False, staleness=0.0, artifacts_gone=True) == (
        "uncheckable"
    )
    # A currency a person set is not argued with.
    assert (
        effective_currency("superseded", has_scope=True, staleness=0.0, artifacts_gone=True)
        == "superseded"
    )


async def test_renamed_file_is_not_missing_and_deleted_directory_is(async_session, repo_root):
    repo = await insert_repo(async_session)
    renamed = await _record(async_session, repo.id, "Entry point", ["src/old_name.py"])
    deleted = await _record(async_session, repo.id, "Legacy layer", ["legacy/a.py", "legacy/b.py"])
    # A path named only in the text counts too.
    text_only = await _record(
        async_session, repo.id, "Legacy text", [], decision="`legacy/b.py` stays sync."
    )
    moved_dir = await _record(async_session, repo.id, "Core pkg", ["pkg/core.py"])
    mixed = await _record(async_session, repo.id, "Mixed", ["legacy/a.py", "pkg/util.py"])
    split = await _record(async_session, repo.id, "Why module", ["tools/why.py"])
    # Untracked but present in the working tree: not deleted.
    _write(repo_root, "notes/local.md")
    local = await _record(async_session, repo.id, "Local notes", ["notes/local.md"])
    text_keeps = await _record(
        async_session,
        repo.id,
        "Mentions a live file",
        ["legacy/a.py"],
        rationale="See `core/util.py` for the replacement.",
    )
    await async_session.flush()

    result = await apply_head_artifact_check(async_session, repo.id, str(repo_root))

    assert result == {"gone": 2, "back": 0}
    assert deleted.artifacts_gone and text_only.artifacts_gone
    for rec in (renamed, moved_dir, mixed, text_keeps, local, split):
        assert not rec.artifacts_gone, rec.title
    # Idempotent: a second pass changes nothing.
    assert await apply_head_artifact_check(async_session, repo.id, str(repo_root)) == {
        "gone": 0,
        "back": 0,
    }


async def test_gone_decision_is_stale_history_never_superseded(async_session, repo_root):
    repo = await insert_repo(async_session)
    why = {"rationale": "Chosen deliberately.", "evidence_commits_json": '["abc1234"]'}
    gone = await _record(async_session, repo.id, "Legacy layer", ["legacy/a.py"], **why)
    live = await _record(async_session, repo.id, "Entry point", ["src/old_name.py"], **why)
    await accept(async_session, gone.id)
    await accept(async_session, live.id)

    await apply_head_artifact_check(async_session, repo.id, str(repo_root))

    assert await current_currency(async_session, gone) == "stale"
    assert await current_currency(async_session, live) == "active"
    assert gone.status == "active" and gone.superseded_by is None
    lanes = await count_decisions_by_lane(async_session, repo.id)
    assert lanes["history"] == 1 and lanes["active"] == 1 and lanes["governing"] == 1


async def test_restored_file_clears_the_mark(async_session, repo_root):
    repo = await insert_repo(async_session)
    rec = await _record(async_session, repo.id, "Legacy layer", ["legacy/a.py"])
    await apply_head_artifact_check(async_session, repo.id, str(repo_root))
    assert rec.artifacts_gone

    _write(repo_root, "legacy/a.py")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "bring legacy back")

    assert await apply_head_artifact_check(async_session, repo.id, str(repo_root)) == {
        "gone": 0,
        "back": 1,
    }
    assert not rec.artifacts_gone


async def test_no_checkout_changes_nothing(async_session, tmp_path):
    repo = await insert_repo(async_session)
    rec = await _record(async_session, repo.id, "Legacy layer", ["legacy/a.py"])
    assert await apply_head_artifact_check(async_session, repo.id, None) == {"gone": 0, "back": 0}
    # A directory git cannot answer for is not an empty tree.
    assert await apply_head_artifact_check(async_session, repo.id, str(tmp_path)) == {
        "gone": 0,
        "back": 0,
    }
    assert not rec.artifacts_gone


async def test_failed_rename_walk_changes_nothing(async_session, repo_root, monkeypatch):
    """Without renames a moved file would read as deleted, so nothing is judged."""
    from repowise.core.analysis.decisions import head_artifacts

    monkeypatch.setattr(head_artifacts, "_history_renames", lambda _root: None)
    repo = await insert_repo(async_session)
    rec = await _record(async_session, repo.id, "Legacy layer", ["legacy/a.py"])

    assert await apply_head_artifact_check(async_session, repo.id, str(repo_root)) == {
        "gone": 0,
        "back": 0,
    }
    assert not rec.artifacts_gone
