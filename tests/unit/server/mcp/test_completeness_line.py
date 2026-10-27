"""``_meta.complete``: a response says once what it served whole.

Completeness is what stops an agent from re-opening a file it already holds,
so the claim is only ever made for a whole unit, never a sliced body or a
partial range.
"""

from __future__ import annotations

import pytest

from repowise.server.mcp_server._meta import completeness_line

MODULE_SOURCE = '''"""A module."""

import os

_DEFAULT_MIN_COUNT = 2
MAX_RETRIES = 5


def alpha(x):
    return x + 1
'''


def test_bodies_only_wording():
    assert completeness_line(bodies=1) == (
        "Complete: 1 symbol body served whole from live source; do not re-open it."
    )
    assert completeness_line(bodies=2) == (
        "Complete: 2 symbol bodies served whole from live source; do not re-open them."
    )


def test_files_only_wording():
    assert completeness_line(files=1) == "Complete: 1 file served whole; do not re-open it."
    assert completeness_line(files=3) == "Complete: 3 files served whole; do not re-open them."


def test_both_counts_are_one_sentence():
    line = completeness_line(bodies=1, files=1)
    assert line == (
        "Complete: 1 symbol body served whole from live source and 1 file "
        "served whole; do not re-open them."
    )
    assert line.count(";") == 1
    assert "—" not in line


def test_nothing_whole_says_nothing():
    assert completeness_line() is None
    assert completeness_line(bodies=0, files=0) is None


# --- get_answer -------------------------------------------------------------


def _projected(raw: dict):
    from repowise.server.mcp_server.tool_answer.projection import projected_answer

    async def _fn(*, question, scope, repo, include):
        return raw

    return projected_answer(_fn)


@pytest.mark.asyncio
async def test_projected_answer_counts_only_whole_bodies(setup_mcp):
    raw = {
        "answer": "It authenticates.",
        "confidence": "low",
        "citations": ["src/auth/service.py"],
        "symbol_bodies": [
            {
                "path": "src/auth/service.py",
                "name": "login",
                "source": "def login(): ...",
                "verified": True,
            },
            {"path": "src/auth/service.py", "name": "drifted", "source": "def drifted(): ..."},
            {
                "path": "src/auth/service.py",
                "name": "big",
                "source": "def big(): ...",
                "truncated": True,
                "continuation": "repowise#deadbeef",
            },
        ],
    }

    result = await _projected(raw)(question="how does login work")

    assert result["_meta"]["complete"] == (
        "Complete: 1 symbol body served whole from live source; do not re-open it."
    )


@pytest.mark.asyncio
async def test_projected_answer_pops_a_stale_claim(setup_mcp):
    raw = {
        "answer": "No grounded answer was found.",
        "confidence": "low",
        "citations": ["src/auth/service.py"],
        "_meta": {"complete": "Complete: 4 symbol bodies served whole from live source."},
    }

    result = await _projected(raw)(question="how does login work")

    assert "complete" not in result["_meta"]


# --- get_symbol -------------------------------------------------------------


@pytest.fixture
def repo_on_disk(tmp_path, monkeypatch):
    import repowise.server.mcp_server as mcp_mod

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(MODULE_SOURCE)
    huge = "def huge(x):\n" + "\n".join(f"    v{i} = {i}" for i in range(1, 900)) + "\n"
    (tmp_path / "pkg" / "big.py").write_text(huge)
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))
    return tmp_path


async def _add_symbol(session, *, symbol_id, path, name, start, end, marker):
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository, WikiSymbol

    repo = (await session.execute(select(Repository))).scalars().first()
    session.add(
        WikiSymbol(
            id=marker,
            repository_id=repo.id,
            file_path=path,
            symbol_id=symbol_id,
            name=name,
            qualified_name=name,
            kind="function",
            signature=f"def {name}(x)",
            start_line=start,
            end_line=end,
            language="python",
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_whole_symbol_body_carries_the_claim(setup_mcp, repo_on_disk, session):
    from repowise.server.mcp_server import get_symbol

    await _add_symbol(
        session,
        symbol_id="pkg/mod.py::alpha",
        path="pkg/mod.py",
        name="alpha",
        start=9,
        end=10,
        marker="cmpl-alpha",
    )
    result = await get_symbol("pkg/mod.py::alpha")

    assert result["truncated"] is False
    assert result["_meta"]["complete"] == (
        "Complete: 1 symbol body served whole from live source; do not re-open it."
    )


@pytest.mark.asyncio
async def test_a_truncated_body_claims_nothing(setup_mcp, repo_on_disk, session):
    from repowise.server.mcp_server import get_symbol

    await _add_symbol(
        session,
        symbol_id="pkg/big.py::huge",
        path="pkg/big.py",
        name="huge",
        start=1,
        end=900,
        marker="cmpl-huge",
    )
    result = await get_symbol("pkg/big.py::huge")

    assert result["truncated"] is True
    assert "complete" not in result["_meta"]


@pytest.mark.asyncio
async def test_a_range_covering_the_whole_file_is_a_unit(setup_mcp, repo_on_disk):
    from repowise.server.mcp_server import get_symbol

    total = len(MODULE_SOURCE.splitlines())
    result = await get_symbol(f"pkg/mod.py:1-{total}")

    assert result["start_line"] == 1
    assert result["end_line"] == result["total_lines"] == total
    assert result["_meta"]["complete"] == "Complete: 1 file served whole; do not re-open it."


@pytest.mark.asyncio
async def test_a_partial_range_claims_nothing(setup_mcp, repo_on_disk):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("pkg/mod.py:5-6")

    assert "complete" not in result["_meta"]
