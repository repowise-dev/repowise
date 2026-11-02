"""One line pointing at get_why where an accepted decision governs a named file.

The line appears only when a decision binds a file the call names, so a call
about a file nobody ruled on keeps its bytes. The server instructions say what
get_why holds for the repository, bounded so the whole string stays in its cap.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from repowise.core.persistence.crud import bulk_upsert_decisions
from repowise.core.persistence.crud.authority import accept_decision
from repowise.core.persistence.models import DecisionRecord
from repowise.server.mcp_server import _why_pointer
from repowise.server.mcp_server._server import _WORKSPACE_TOOL_LINES, _instructions
from repowise.server.mcp_server._why_pointer import (
    _MAX_LINE_CHARS,
    decisions_line,
    governed_areas_line,
    why_pointer,
)
from tests.unit.server.mcp.test_server_instructions import _MAX_INSTRUCTIONS_CHARS

_FILE = "src/auth/service.py"


def _ctx(factory, alias=None):
    return SimpleNamespace(session_factory=factory, alias=alias)


async def _accept(session, repo_id: str, title: str, files: list[str]) -> None:
    await bulk_upsert_decisions(
        session,
        repo_id,
        [
            {
                "title": title,
                "decision": f"{title}: do the thing",
                "rationale": "because the alternative was measured slower",
                "source": "session",
                "status": "proposed",
                "affected_files": files,
                "evidence_file": files[0],
                "confidence": 0.7,
                "verification": "exact",
                "source_quote": title,
            }
        ],
    )
    record = (
        await session.execute(select(DecisionRecord).where(DecisionRecord.title == title))
    ).scalar_one()
    await accept_decision(session, record, accepter="tester", scope=files)
    await session.commit()


@pytest.mark.asyncio
async def test_a_governed_target_gets_one_line_naming_the_decision_and_the_call(
    session, factory, repo_id
):
    await _accept(session, repo_id, "Tokens expire after one hour", [_FILE])

    line = await why_pointer(_ctx(factory), [f"{_FILE}::AuthService", "src/other.py"])

    assert line == f'Decision "Tokens expire after one hour" governs {_FILE}: get_why(targets=["{_FILE}"])'


@pytest.mark.asyncio
async def test_an_ungoverned_target_adds_nothing(session, factory, repo_id):
    await _accept(session, repo_id, "Tokens expire after one hour", [_FILE])

    assert await why_pointer(_ctx(factory), ["src/other.py"]) is None


@pytest.mark.asyncio
async def test_a_candidate_governs_nothing(session, factory, repo_id):
    await bulk_upsert_decisions(
        session,
        repo_id,
        [
            {
                "title": "Unreviewed",
                "decision": "d",
                "rationale": "r",
                "source": "session",
                "status": "proposed",
                "affected_files": [_FILE],
                "evidence_file": _FILE,
                "confidence": 0.7,
                "verification": "exact",
                "source_quote": "q",
            }
        ],
    )
    await session.commit()

    assert await why_pointer(_ctx(factory), [_FILE]) is None
    assert "No decisions recorded yet" in (await governed_areas_line(factory) or "")


@pytest.mark.asyncio
async def test_the_instructions_line_names_where_decisions_sit(session, factory, repo_id):
    await _accept(session, repo_id, "Tokens expire after one hour", [_FILE])

    line = await governed_areas_line(factory)

    assert line is not None
    assert line.startswith("- 1 decisions govern files, most in auth/")
    assert "get_why(targets=[file])" in line


@pytest.mark.asyncio
async def test_long_area_names_keep_the_line_bounded(session, factory, repo_id):
    deep = "src/" + "a_very_long_directory_name_" * 4
    for i in range(3):
        await _accept(session, repo_id, f"Rule {i}", [f"{deep}{i}/x.py"])

    line = await governed_areas_line(factory)

    assert line is not None
    assert len(line) <= _MAX_LINE_CHARS


def test_instructions_with_the_longest_repo_line_fit_the_cap():
    longest = "x" * _MAX_LINE_CHARS
    text = _instructions(list(_WORKSPACE_TOOL_LINES), longest)
    assert longest in text
    assert len(text) < _MAX_INSTRUCTIONS_CHARS


@pytest.mark.asyncio
async def test_in_a_workspace_the_call_names_the_repository(
    session, factory, repo_id, monkeypatch
):
    await _accept(session, repo_id, "Tokens expire after one hour", [_FILE])
    monkeypatch.setattr(_why_pointer, "_is_workspace_mode", lambda: True)

    line = await why_pointer(_ctx(factory, alias="api"), [_FILE])

    assert line is not None
    assert line.endswith(f'get_why(targets=["{_FILE}"], repo="api")')


@pytest.mark.asyncio
async def test_the_startup_line_is_skipped_under_a_running_loop(tmp_path):
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "wiki.db").write_bytes(b"")

    assert decisions_line(str(tmp_path)) is None


def test_the_startup_line_is_none_for_an_unreadable_store(tmp_path):
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "wiki.db").write_bytes(b"not a database")

    assert decisions_line(str(tmp_path)) is None
