"""upsert_repository keeps a stored url and default branch unless one is passed.

Most callers (decision commands, status, update passes) pass only name and
path; a default of ``""`` / ``"main"`` there reset a ``master`` repo to
``main`` and blanked its url on every call.
"""

from __future__ import annotations

from repowise.core.persistence import crud


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


async def test_a_new_row_without_values_gets_the_defaults(async_session) -> None:
    repo = await crud.upsert_repository(async_session, name="r", local_path="/tmp/new")

    assert repo.url == ""
    assert repo.default_branch == "main"
