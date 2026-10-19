"""_get_repo finds a repository by any spelling of its path, not only the stored one."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from repowise.core.persistence.models import Repository
from repowise.server.mcp_server._helpers import _get_repo


@pytest.fixture
async def repository(session, populated_db) -> Repository:
    return await session.get(Repository, populated_db)


async def test_finds_by_stored_path(session, repository):
    assert (await _get_repo(session, repository.local_path)).id == repository.id


async def test_finds_by_trailing_separator(session, repository):
    path = repository.local_path + ("\\" if sys.platform == "win32" else "/")
    assert (await _get_repo(session, path)).id == repository.id


async def test_finds_by_forward_slashes(session, repository):
    path = Path(repository.local_path).as_posix()
    assert (await _get_repo(session, path)).id == repository.id


async def test_finds_by_dotdot_segment(session, repository):
    path = str(Path(repository.local_path) / "sub" / "..")
    assert (await _get_repo(session, path)).id == repository.id


@pytest.mark.skipif(sys.platform != "win32", reason="case-insensitive paths")
async def test_finds_by_lowercase_path(session, repository):
    assert (await _get_repo(session, repository.local_path.lower())).id == repository.id


async def test_finds_by_id_and_name(session, repository):
    assert (await _get_repo(session, repository.id)).id == repository.id
    assert (await _get_repo(session, repository.name)).id == repository.id


async def test_unknown_absolute_path_still_raises(session, repository, tmp_path):
    unknown = str(tmp_path / "elsewhere")
    with pytest.raises(LookupError, match="Repository not found"):
        await _get_repo(session, unknown)
