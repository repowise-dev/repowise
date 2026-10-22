"""upsert_repository keeps a stored url and default branch unless one is passed.

Most callers (decision commands, status, update passes) pass only name and
path; a default of ``""`` / ``"main"`` there reset a ``master`` repo to
``main`` and blanked its url on every call.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from repowise.core.persistence import crud


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _checkout(tmp_path: Path, branch: str) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", branch)
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    _git(root, "add", "a.txt")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "a")
    return root


async def test_callers_without_url_or_branch_keep_the_stored_values(async_session) -> None:
    await crud.upsert_repository(
        async_session,
        name="r",
        local_path="/tmp/r",
        url="https://example.com/r.git",
        default_branch="master",
    )

    repo = await crud.upsert_repository(async_session, name="r", local_path="/tmp/r")

    assert repo.url == "https://example.com/r.git"
    assert repo.default_branch == "master"


async def test_passed_values_overwrite_and_empty_url_clears(async_session) -> None:
    await crud.upsert_repository(
        async_session, name="r", local_path="/tmp/r", url="https://a", default_branch="master"
    )

    repo = await crud.upsert_repository(
        async_session, name="r", local_path="/tmp/r", url="", default_branch="develop"
    )

    assert repo.url == ""
    assert repo.default_branch == "develop"


async def test_an_empty_branch_is_never_stored(async_session) -> None:
    new = await crud.upsert_repository(
        async_session, name="r", local_path="/tmp/e", default_branch=""
    )
    assert new.default_branch == "main"

    await crud.upsert_repository(
        async_session, name="r", local_path="/tmp/e", default_branch="master"
    )
    kept = await crud.upsert_repository(
        async_session, name="r", local_path="/tmp/e", default_branch=""
    )
    assert kept.default_branch == "master"


async def test_a_new_row_without_values_gets_the_defaults(async_session) -> None:
    repo = await crud.upsert_repository(async_session, name="r", local_path="/tmp/new")

    assert repo.url == ""
    assert repo.default_branch == "main"


async def test_a_sync_records_the_branch_and_a_credential_free_remote(
    async_session, tmp_path: Path
) -> None:
    root = _checkout(tmp_path, "master")
    _git(root, "remote", "add", "origin", "https://user:ghp_secret@github.com/acme/legacy.git")

    repo = await crud.upsert_repository(
        async_session, name="legacy", local_path=str(root), stamp_head=True
    )

    assert repo.default_branch == "master"
    assert repo.url == "https://github.com/acme/legacy.git"


async def test_a_sync_passing_an_empty_branch_reads_it_from_git(
    async_session, tmp_path: Path
) -> None:
    root = _checkout(tmp_path, "master")

    repo = await crud.upsert_repository(
        async_session, name="r", local_path=str(root), default_branch="", stamp_head=True
    )

    assert repo.default_branch == "master"


async def test_the_origin_head_target_wins_over_a_local_trunk(
    async_session, tmp_path: Path
) -> None:
    root = _checkout(tmp_path, "main")
    _git(root, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    _git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")

    repo = await crud.upsert_repository(async_session, name="r", local_path=str(root))

    assert repo.default_branch == "trunk"


async def test_a_sync_git_cannot_answer_keeps_the_stored_values(
    async_session, tmp_path: Path
) -> None:
    root = _checkout(tmp_path, "topic")  # no remote, no origin/HEAD, no trunk
    await crud.upsert_repository(
        async_session,
        name="r",
        local_path=str(root),
        url="https://example.com/r.git",
        default_branch="develop",
    )

    repo = await crud.upsert_repository(
        async_session, name="r", local_path=str(root), stamp_head=True
    )

    assert repo.url == "https://example.com/r.git"
    assert repo.default_branch == "develop"


async def test_a_call_that_only_needs_the_row_does_not_read_git(
    async_session, tmp_path: Path
) -> None:
    root = _checkout(tmp_path, "master")
    await crud.upsert_repository(
        async_session, name="r", local_path=str(root), default_branch="release"
    )

    repo = await crud.upsert_repository(async_session, name="r", local_path=str(root))

    assert repo.default_branch == "release"


async def test_a_broken_git_dir_never_answers_for_a_parent_repo(
    async_session, tmp_path: Path
) -> None:
    parent = _checkout(tmp_path, "master")
    child = parent / "child"
    (child / ".git").mkdir(parents=True)

    repo = await crud.upsert_repository(async_session, name="c", local_path=str(child))

    assert repo.default_branch == "main"
    assert repo.url == ""
