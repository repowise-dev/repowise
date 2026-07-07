"""Range reads and dead-end recovery in get_symbol (C3/C4).

* "path/to/file.py:140-180" serves a live, bounded, always-verified slice
* an index miss greps the live file and returns fallback_lines instead of
  a pure-cost dead end
"""

from __future__ import annotations

import pytest

MODULE_SOURCE = '''"""A module."""

import os

_DEFAULT_MIN_COUNT = 2
MAX_RETRIES = 5


def alpha(x):
    return x + 1


def beta(y):
    return y * 2
'''


@pytest.fixture
def repo_on_disk(tmp_path, monkeypatch):
    """Point the MCP repo path at a tmp dir with a real source file."""
    import repowise.server.mcp_server as mcp_mod

    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(MODULE_SOURCE)
    big = "\n".join(f"line_{i} = {i}" for i in range(1, 401))
    (tmp_path / "pkg" / "big.py").write_text(big)
    monkeypatch.setattr(mcp_mod, "_repo_path", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_range_read_serves_verified_slice(setup_mcp, repo_on_disk):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("pkg/mod.py:5-6")
    assert result.get("error") is None
    assert result["verified"] is True
    assert result["kind"] == "range"
    assert result["start_line"] == 5
    assert result["end_line"] == 6
    assert "_DEFAULT_MIN_COUNT = 2" in result["source"]
    assert "MAX_RETRIES = 5" in result["source"]


@pytest.mark.asyncio
async def test_range_read_caps_at_200_lines(setup_mcp, repo_on_disk):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("pkg/big.py:1-400")
    assert result["truncated"] is True
    assert result["end_line"] - result["start_line"] + 1 <= 200


@pytest.mark.asyncio
async def test_range_read_context_does_not_exceed_cap(setup_mcp, repo_on_disk):
    from repowise.server.mcp_server import get_symbol

    # 161-line request + 50 lines of context each side would be 261 lines —
    # the ≤200 contract must hold after context expansion, not just before.
    result = await get_symbol("pkg/big.py:100-260", context_lines=50)
    assert result["verified"] is True
    assert result["end_line"] - result["start_line"] + 1 <= 200
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_truncated_range_emits_continuation_token(setup_mcp, repo_on_disk):
    from repowise.server.mcp_server import get_symbol

    # 400-line request caps at 200; the remainder must come back as an exact,
    # ready-to-replay range read so the agent never guesses the next span.
    result = await get_symbol("pkg/big.py:1-400")
    assert result["truncated"] is True
    assert result["continuation"] == f"pkg/big.py:{result['end_line'] + 1}-400"
    assert "get_symbol" in result["note"]


@pytest.mark.asyncio
async def test_untruncated_range_has_no_continuation(setup_mcp, repo_on_disk):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("pkg/big.py:1-50")
    assert result["truncated"] is False
    assert "continuation" not in result


@pytest.mark.asyncio
async def test_truncated_symbol_emits_continuation_token(setup_mcp, repo_on_disk, session):
    from sqlalchemy import select

    from repowise.core.persistence.models import Repository, WikiSymbol
    from repowise.server.mcp_server import get_symbol

    # A whole-file function (def on line 1, body to EOF) that overruns the
    # serve cap. The served head must hand back the remaining span.
    body = "\n".join(f"    a{i} = {i}" for i in range(1, 699))
    big_fn = f"def big_fn(x):\n{body}\n    return x\n"  # 700 lines, ends at EOF
    (repo_on_disk / "pkg" / "bigfn.py").write_text(big_fn)
    total = len(big_fn.splitlines())

    repo = (await session.execute(select(Repository))).scalars().first()
    session.add(
        WikiSymbol(
            id="bigfn1",
            repository_id=repo.id,
            file_path="pkg/bigfn.py",
            symbol_id="pkg/bigfn.py::big_fn",
            name="big_fn",
            qualified_name="pkg.bigfn.big_fn",
            kind="function",
            signature="def big_fn(x)",
            start_line=1,
            end_line=total,
            language="python",
        )
    )
    await session.flush()

    result = await get_symbol("pkg/bigfn.py::big_fn")
    assert result["truncated"] is True
    assert result["verified"] is True
    assert result["continuation"] == f"pkg/bigfn.py:{result['end_line'] + 1}-{total}"
    assert "get_symbol" in result["note"]
    # The continuation token round-trips to a clean range read of the tail.
    tail = await get_symbol(result["continuation"])
    assert tail.get("error") is None
    assert tail["kind"] == "range"
    assert "return x" in tail["source"]


@pytest.mark.asyncio
async def test_range_read_swaps_reversed_bounds(setup_mcp, repo_on_disk):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("pkg/mod.py:6-5")
    assert result.get("error") is None
    assert result["start_line"] == 5


@pytest.mark.asyncio
async def test_double_colon_id_is_not_a_range(setup_mcp, repo_on_disk):
    from repowise.server.mcp_server import get_symbol

    # "::" forces symbol resolution even though the tail looks numeric-ish.
    result = await get_symbol("pkg/mod.py::alpha")
    assert result.get("kind") != "range"


@pytest.mark.asyncio
async def test_unindexed_constant_recovers_via_live_grep(setup_mcp, repo_on_disk):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("pkg/mod.py::_DEFAULT_MIN_COUNT")
    assert result.get("resolution") == "live_grep"
    assert result["verified"] is True
    [match] = [m for m in result["fallback_lines"] if m["line"] == 5]
    assert "_DEFAULT_MIN_COUNT = 2" in match["context"]
    assert "range read" in result["note"]


@pytest.mark.asyncio
async def test_missing_file_still_errors(setup_mcp, repo_on_disk):
    from repowise.server.mcp_server import get_symbol

    result = await get_symbol("pkg/ghost.py::nothing")
    assert "error" in result
