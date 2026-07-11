"""Ambiguous symbol ids serve ALL candidates; raw source is Read-format.

* A lookup matching several rows (overloads, re-exports, conditional defs)
  returns every candidate body in one response — a deterministic-but-wrong
  silent pick reads as authoritative and sends the agent editing the wrong
  overload.
* Served source lines carry Read's exact ``cat -n`` numbering so the agent
  treats the response as an already-performed Read.
"""

from __future__ import annotations

import pytest

from repowise.server.mcp_server.tool_symbol import _number_lines

DUP_SOURCE = """try:
    def dup(x):
        return 1
except NameError:
    def dup(x):
        return 2
"""

MODULE_SOURCE = '''"""A module."""

import os

_DEFAULT_MIN_COUNT = 2
MAX_RETRIES = 5


def alpha(x):
    return x + 1
'''


@pytest.fixture
def repo_on_disk(tmp_path, monkeypatch):
    import repowise.server.mcp_server as mcp_mod

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "dup.py").write_text(DUP_SOURCE)
    (tmp_path / "pkg" / "mod.py").write_text(MODULE_SOURCE)
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))
    return tmp_path


async def _add_dup_rows(session):
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository, WikiSymbol

    repo = (await session.execute(select(Repository))).scalars().first()
    for sid, start, end, marker in (("dup1", 2, 3, "aaaa"), ("dup2", 5, 6, "bbbb")):
        session.add(
            WikiSymbol(
                id=marker + sid,
                repository_id=repo.id,
                file_path="pkg/dup.py",
                symbol_id=f"pkg/dup.py::dup#{sid[-1]}",
                name="dup",
                qualified_name="dup",
                kind="function",
                signature="def dup(x)",
                start_line=start,
                end_line=end,
                language="python",
            )
        )
    await session.flush()


def test_number_lines_matches_read_format():
    # cat -n: right-aligned width-6 line number, tab, verbatim line.
    assert _number_lines("a\nb", 5) == "     5\ta\n     6\tb"
    assert _number_lines("x", 1234567) == "1234567\tx"


@pytest.mark.asyncio
async def test_ambiguous_symbol_returns_all_candidates(setup_mcp, repo_on_disk, session):
    from repowise.server.mcp_server import get_symbol

    await _add_dup_rows(session)
    result = await get_symbol("pkg/dup.py::dup")

    assert result.get("error") is None
    assert result["ambiguous"] is True
    assert result["match_count"] == 2
    assert len(result["candidates"]) == 2
    assert "none was silently chosen" in result["note"]

    # Every candidate carries its own verified body, and the two bodies
    # differ — the whole point is letting the agent pick by content.
    first, second = result["candidates"]
    assert "return 1" in first["source"]
    assert "return 2" in second["source"]
    assert first["verified"] is True
    assert second["verified"] is True
    # Deterministic head slot: lowest id ("aaaa...") leads.
    assert first["start_line"] == 2


@pytest.mark.asyncio
async def test_unambiguous_lookup_is_unchanged_shape(setup_mcp, repo_on_disk, session):
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository, WikiSymbol
    from repowise.server.mcp_server import get_symbol

    repo = (await session.execute(select(Repository))).scalars().first()
    session.add(
        WikiSymbol(
            id="alpha1",
            repository_id=repo.id,
            file_path="pkg/mod.py",
            symbol_id="pkg/mod.py::alpha",
            name="alpha",
            qualified_name="alpha",
            kind="function",
            signature="def alpha(x)",
            start_line=9,
            end_line=10,
            language="python",
        )
    )
    await session.flush()

    result = await get_symbol("pkg/mod.py::alpha")
    assert "ambiguous" not in result
    assert result["verified"] is True
    # Read-format parity: each served line is "<n>\t<code>" with real numbers.
    assert "     9\tdef alpha(x):" in result["source"]
    assert "    10\t    return x + 1" in result["source"]


async def _add_alpha_row(session):
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository, WikiSymbol

    repo = (await session.execute(select(Repository))).scalars().first()
    session.add(
        WikiSymbol(
            id="alpha1",
            repository_id=repo.id,
            file_path="pkg/mod.py",
            symbol_id="pkg/mod.py::alpha",
            name="alpha",
            qualified_name="alpha",
            kind="function",
            signature="def alpha(x)",
            start_line=9,
            end_line=10,
            language="python",
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_filename_only_id_resolves_via_suffix(setup_mcp, repo_on_disk, session):
    # A bare filename ("mod.py::alpha") must resolve to the full indexed path
    # via the suffix ladder, not dead-end on "not found" (finding 2).
    from repowise.server.mcp_server import get_symbol

    await _add_alpha_row(session)
    result = await get_symbol("mod.py::alpha")

    assert result.get("error") is None
    assert result["symbol_id"] == "pkg/mod.py::alpha"
    assert result["verified"] is True
    assert "     9\tdef alpha(x):" in result["source"]


@pytest.mark.asyncio
async def test_total_miss_returns_symbol_id_suggestions(setup_mcp, repo_on_disk, session):
    # A wrong path with a real leaf name returns retryable path-qualified ids
    # instead of a bare "not found".
    from repowise.server.mcp_server import get_symbol

    await _add_alpha_row(session)
    result = await get_symbol("nope/wrong.py::alpha")

    assert "source" not in result
    assert result["suggestions"] == ["pkg/mod.py::alpha"]
    assert "retry" in result["error"].lower()


@pytest.mark.asyncio
async def test_range_read_source_is_numbered(setup_mcp, repo_on_disk):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("pkg/mod.py:5-6")
    assert result["source"] == "     5\t_DEFAULT_MIN_COUNT = 2\n     6\tMAX_RETRIES = 5"


@pytest.mark.asyncio
async def test_fallback_lines_are_numbered(setup_mcp, repo_on_disk):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("pkg/mod.py::MAX_RETRIES")
    assert result.get("resolution") == "live_grep"
    [match] = [m for m in result["fallback_lines"] if m["line"] == 6]
    assert "     6\tMAX_RETRIES = 5" in match["context"]


@pytest.mark.asyncio
async def test_budget_overflow_lists_unrendered_with_fetch_range(
    setup_mcp, repo_on_disk, session, monkeypatch
):
    import repowise.server.mcp_server.tool_symbol as ts
    from repowise.server.mcp_server import get_symbol

    await _add_dup_rows(session)
    # Force the sub-budget below the second candidate's size: it must be
    # listed (never dropped) with the exact range read that fetches it.
    monkeypatch.setattr(ts, "_AMBIGUITY_CHAR_BUDGET", 10)

    result = await get_symbol("pkg/dup.py::dup")
    assert len(result["candidates"]) == 1  # the first always renders
    [skipped] = result["not_rendered"]
    assert skipped["fetch_with"] == "pkg/dup.py:5-6"
    assert "not_rendered" in result["note"]
