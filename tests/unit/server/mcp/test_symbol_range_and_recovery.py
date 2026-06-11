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
