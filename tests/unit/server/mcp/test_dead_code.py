"""Unit tests for repowise MCP server tools.

Tests all 9 MCP tools using an in-memory SQLite database with pre-populated
test data, mirroring the conftest pattern from the REST API tests.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_get_dead_code(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code()
    assert result["summary"]["total_findings"] == 3
    # dc1 is stored safe but is a whole file, so it reads as review-only.
    assert result["summary"]["safe_to_delete_count"] == 1

    # Engine bands (0.7 / 0.4): dc1 (0.9) + dc2 (0.7) high, dc3 (0.5) medium
    tiers = result["tiers"]
    assert tiers["high"]["count"] == 2
    assert tiers["medium"]["count"] == 1
    assert tiers["low"]["count"] == 0

    # High tier findings sorted by confidence desc
    high_findings = tiers["high"]["findings"]
    assert high_findings[0]["confidence"] >= high_findings[1]["confidence"] >= 0.7
    assert high_findings[0]["kind"] == "unreachable_file"
    assert high_findings[0]["safe_to_delete"] is False

    # Impact estimate present
    assert result["impact"]["total_lines_reclaimable"] > 0


@pytest.mark.asyncio
async def test_get_dead_code_summary_says_what_it_counts(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    plain = (await get_dead_code())["summary"]
    assert "filtered_findings" in plain["scope"]
    assert "filters" not in plain  # defaults are not echoed

    # A filter that keeps nothing: the totals still count every open finding,
    # and the summary names the filters that emptied the tiers.
    result = await get_dead_code(directory="src/db", min_confidence="high")
    summary = result["summary"]
    assert summary["total_findings"] == 3
    assert summary["filtered_findings"] == 0
    assert summary["filters"] == {"directory": "src/db", "min_confidence": 0.7}
    assert summary["scope"] == plain["scope"]


@pytest.mark.asyncio
async def test_get_dead_code_safe_only(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(safe_only=True)
    for tier_data in result["tiers"].values():
        for f in tier_data["findings"]:
            assert f["safe_to_delete"] is True
    # A deletion-ready finding exists, so there is no empty view to explain.
    assert "safe_only_note" not in result["summary"]


@pytest.mark.asyncio
async def test_get_dead_code_by_kind(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(kind="unreachable_file", min_confidence=0.0)
    for tier_data in result["tiers"].values():
        for f in tier_data["findings"]:
            assert f["kind"] == "unreachable_file"


@pytest.mark.asyncio
async def test_get_dead_code_low_confidence(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(min_confidence=0.0)
    total = sum(t["count"] for t in result["tiers"].values())
    assert total == 3  # All 3 findings included


@pytest.mark.asyncio
async def test_get_dead_code_tier_filter(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(tier="high")
    assert "high" in result["tiers"]
    assert "medium" not in result["tiers"]
    assert "low" not in result["tiers"]
    assert result["tiers"]["high"]["count"] == 2


@pytest.mark.asyncio
async def test_get_dead_code_group_by_directory(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(group_by="directory", min_confidence=0.0)
    assert "by_directory" in result
    dirs = result["by_directory"]
    assert len(dirs) >= 1
    # Each dir entry has count, lines, safe_count
    for d in dirs:
        assert "directory" in d
        assert "count" in d
        assert "lines" in d


@pytest.mark.asyncio
async def test_get_dead_code_group_by_owner(setup_mcp):
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code(group_by="owner", min_confidence=0.0)
    assert "by_owner" in result
    owners = result["by_owner"]
    assert len(owners) >= 1
    owner_names = [o["owner"] for o in owners]
    assert "Bob" in owner_names  # Bob owns dc2 + dc3


class _EveryFileConsumed:
    def has_cross_repo_consumers(self, repo_alias, file_path):
        return [{"repo": "web"}]

    def get_repos_depending_on(self, repo_alias):
        return ["web"]

    def get_package_consumers(self, repo_alias):
        return []

    def get_contract_links_as_provider(self, repo_alias, file_path):
        return []


@pytest.mark.asyncio
async def test_cross_repo_cut_reaches_summary_and_rollups(setup_mcp, monkeypatch):
    from repowise.server.mcp_server import get_dead_code, tool_dead_code

    monkeypatch.setattr(
        tool_dead_code, "_cross_repo_cut", lambda alias: {"lookup": _EveryFileConsumed(), "alias": alias}
    )

    result = await get_dead_code(group_by="directory", min_confidence=0.0)
    summary = result["summary"]
    # The safe counts agree with the tiers: a consumed finding is never safe.
    assert summary["total_findings"] == 3
    assert summary["safe_to_delete_count"] == 0
    assert summary["deletable_lines"] == 0
    assert all(d["safe_count"] == 0 for d in result["by_directory"])
    assert not any(f["safe_to_delete"] for t in result["tiers"].values() for f in t["findings"])

    # The cut empties a safe-only view, and the summary says why.
    safe = await get_dead_code(safe_only=True)
    assert safe["summary"]["filtered_findings"] == 0
    assert "safe_only_note" in safe["summary"]


class _EveryFileCalled(_EveryFileConsumed):
    def get_contract_links_as_provider(self, repo_alias, file_path):
        return [{"consumer_repo": "web"}]


@pytest.mark.asyncio
async def test_the_default_floor_reads_the_cut_confidence(setup_mcp, monkeypatch):
    from repowise.server.mcp_server import get_dead_code, tool_dead_code

    monkeypatch.setattr(
        tool_dead_code, "_cross_repo_cut", lambda alias: {"lookup": _EveryFileCalled(), "alias": alias}
    )

    # 0.9 / 0.7 / 0.5 cut to 0.27 / 0.21 / 0.15: none clears 0.4.
    result = await get_dead_code()
    assert not any(t["findings"] for t in result["tiers"].values())
    assert result["summary"]["filtered_findings"] == 0
    assert result["summary"]["total_findings"] == 3
    low = await get_dead_code(min_confidence="low")
    assert [f["confidence"] for f in low["tiers"]["low"]["findings"]] == [0.27, 0.21, 0.15]


@pytest.mark.asyncio
async def test_get_dead_code_names_a_directory_it_does_not_know(setup_mcp):
    """A directory with no indexed file is an error, never an empty clean list."""
    from repowise.server.mcp_server import get_dead_code

    missing = await get_dead_code(directory="packages/does-not-exist")
    assert "summary" not in missing
    assert "packages/does-not-exist" in missing["error"]

    # An indexed directory with nothing dead, and an unreachable file's
    # directory that has no graph node, both still answer.
    assert (await get_dead_code(directory="tests"))["summary"]["filtered_findings"] == 0
    assert (await get_dead_code(directory="src/legacy"))["summary"]["filtered_findings"] == 1


@pytest.mark.asyncio
async def test_get_dead_code_headline_counts_shown_and_withheld(setup_mcp):
    """The first line says how many findings are shown and how many were held back."""
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code()
    assert next(iter(result)) == "headline"
    assert result["headline"].startswith("3 shown")

    one = await get_dead_code(limit=1, tier="high")
    assert one["headline"].startswith("1 shown of 2 matching")


def test_dead_code_headline_names_withheld_kinds():
    from repowise.server.mcp_server.tool_dead_code import _headline

    summary = {
        "withheld_types": {"unused_internal": {"count": 2, "status": "hidden"}},
    }
    tiers = {name: {"count": 0, "findings": []} for name in ("high", "medium", "low")}
    assert _headline(summary, tiers) == (
        "0 shown, 2 withheld (unused_internal; see summary.withheld_types)."
    )


@pytest.mark.asyncio
async def test_get_dead_code_lists_no_private_symbols_by_default(setup_mcp):
    """``unused_internal`` measured almost all false: never listed unless asked."""
    from repowise.server.mcp_server import get_dead_code

    result = await get_dead_code()
    kinds = {f["kind"] for t in result["tiers"].values() for f in t["findings"]}
    assert "unused_internal" not in kinds


def test_shared_basis_note_is_said_once():
    from repowise.server.mcp_server.tool_dead_code import _attach_resolution_basis

    note = "Call sites the resolver could not bind are not counted."
    summary: dict = {}
    _attach_resolution_basis(
        summary,
        [{"language": "python", "note": note}, {"language": "typescript", "note": note}],
    )
    assert summary["call_resolution_note"] == note
    assert summary["call_resolution_basis"] == [{"language": "python"}, {"language": "typescript"}]
