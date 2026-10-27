"""``repowise overlap`` over a real temporary repository.

Branch listing and three-dot diffs are the whole question the command asks, so
the tests build real repositories rather than a fake that would only agree with
itself. None of them has an index: the git-only path is what a fresh clone gets.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from click.testing import CliRunner

from repowise.cli.commands.overlap_cmd import overlap_command


def _run(repo, *args) -> str:
    result = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
             "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00"},
    )
    return result.stdout.strip()


def _commit(repo, message: str, files: dict[str, str]) -> None:
    for name, text in files.items():
        (repo / name).write_text(text, encoding="utf-8")
    _run(repo, "add", "-A")
    _run(repo, "commit", "-m", message)


def _build(tmp_path, name: str, *, rival: bool) -> object:
    """A repo on ``feat/x``, optionally with another branch editing the same file."""
    path = tmp_path / name
    path.mkdir()
    _run(path, "init", "-b", "main")
    _commit(
        path, "base", {"shared.py": "0\n", "partner.py": "0\n", "other.py": "0\n"}
    )
    if rival:
        _run(path, "checkout", "-b", "rival")
        _commit(path, "rival", {"shared.py": "rival\n"})
        _run(path, "checkout", "main")
    _run(path, "checkout", "-b", "feat/x")
    _commit(path, "ours", {"shared.py": "ours\n"})
    return path


@pytest.fixture
def repo(tmp_path):
    return _build(tmp_path, "shared", rival=True)


@pytest.fixture
def lonely(tmp_path):
    return _build(tmp_path, "lonely", rival=False)


def _invoke(repo, *extra: str):
    result = CliRunner().invoke(overlap_command, ["--path", str(repo), "--base", "main", *extra])
    assert result.exit_code == 0, result.output
    return result.output


@pytest.fixture
def crowded(tmp_path):
    """Two other branches editing the file this change edits."""
    path = _build(tmp_path, "crowded", rival=True)
    _run(path, "checkout", "main")
    _run(path, "checkout", "-b", "second")
    _commit(path, "second", {"shared.py": "second\n"})
    _run(path, "checkout", "feat/x")
    return path


def test_a_narrow_limit_says_what_it_did_not_scan(crowded):
    output = _invoke(crowded, "--limit", "1")

    assert "Scanned the newest 1 of 2 branches; raise --limit to scan more." in output


def test_a_limit_below_one_is_refused(repo):
    result = CliRunner().invoke(overlap_command, ["--path", str(repo), "--limit", "0"])

    assert result.exit_code != 0


def test_a_branch_editing_the_same_file_is_listed(repo):
    output = _invoke(repo)

    assert "rival" in output
    assert "same file" in output
    assert "shared.py" in output
    # The basis is stated in words: never a score or a percentage.
    assert "%" not in output


def test_a_branch_that_shares_nothing_prints_only_the_summary(lonely):
    output = _invoke(lonely).strip()

    assert output.splitlines() == [
        "No other open branch edits a file this change edits (0 scanned of 0)."
    ]


def test_json_carries_the_full_document(repo):
    payload = json.loads(_invoke(repo, "--format", "json"))

    assert set(payload) == {
        "base",
        "current",
        "branches",
        "scanned",
        "total",
        "truncated",
        "summary",
    }
    assert payload["base"] == "main"
    assert payload["current"] == "feat/x"
    assert payload["branches"][0]["files"] == [{"file": "shared.py", "basis": "same file"}]


def test_a_change_with_nothing_in_it_says_so(repo):
    _run(repo, "checkout", "main")

    assert _invoke(repo).strip() == "Nothing changed on HEAD since main."


def test_an_unknown_ref_is_refused(repo):
    """A pruned or misspelled ref diffs to nothing, so silence would mislead."""
    result = CliRunner().invoke(
        overlap_command, ["--path", str(repo), "--base", "main", "--branch", "does-not-exist"]
    )

    assert result.exit_code != 0
    assert "Unknown ref 'does-not-exist'." in result.output


def test_a_repo_without_an_index_still_answers(repo):
    assert not (repo / ".repowise").exists()

    assert "rival" in _invoke(repo)


def _seed_index(repo, *, partners: list[dict] | None = None) -> None:
    """Just enough store for the command to open a session and look the repo up.

    With *partners*, shared.py also carries those co-change records.
    """
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.models import GitMetadata, GraphNode, Repository, _new_uuid

    async def write() -> None:
        db_path = repo / ".repowise" / "wiki.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        await init_db(engine)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add(Repository(id="repo1", name="repo", local_path=str(repo.resolve())))
            if partners is not None:
                session.add(
                    GraphNode(
                        id=_new_uuid(),
                        repository_id="repo1",
                        node_id="shared.py",
                        node_type="file",
                    )
                )
                session.add(
                    GitMetadata(
                        id=_new_uuid(),
                        repository_id="repo1",
                        file_path="shared.py",
                        co_change_partners_json=json.dumps(partners),
                    )
                )
            await session.commit()
        await engine.dispose()

    asyncio.run(write())


def test_an_indexed_repo_names_the_file_a_history_row_pairs_with(repo):
    """With an index the reader also sees which of our files the partner pairs with."""
    _run(repo, "checkout", "rival")
    _commit(repo, "rival edits the partner too", {"partner.py": "rival\n"})
    _run(repo, "checkout", "feat/x")
    _seed_index(
        repo,
        partners=[
            {
                "file_path": "partner.py",
                "co_change_count": 5.0,
                "frequency": 6,
                "self_commits": 9,
            }
        ],
    )

    output = _invoke(repo)

    assert "co-change pair, 6 of 9 commits" in output
    assert "partner.py  (with shared.py)" in output


def test_an_index_the_query_cannot_read_leaves_the_git_answer(repo, monkeypatch):
    """An index written by an older version costs the ranking, not the answer."""
    from sqlalchemy.exc import SQLAlchemyError

    from repowise.core.analysis import branch_overlap as module

    _seed_index(repo)

    async def _fail(*args, **kwargs):
        raise SQLAlchemyError("old schema")

    monkeypatch.setattr(module, "rank_with_index", _fail)
    output = _invoke(repo)

    assert "rival" in output
    assert "shared.py" in output
