"""The file-level caller rollup cuts calls edges by the shared rule.

``tool_context/enrichment.py`` kept a private calls-to-files rollup with its own
0.7 floor while every other file-pair surface (the zoom map, ``_flow_path``)
uses the 0.5 floor in ``_graph_files``. The same call edge was therefore kept by
one surface and dropped by another: 1,436 of 45,755 reliable execution edges
(3.1%) across 8 indexed repos, 24.8% of them on eShopOnWeb. The rollup also had
no cross-extension guard, so a projection could stitch two languages together.

The two symbol-level sites keep the 0.7 floor deliberately. The totals query and
the displayed rows have to be cut by the same rule as each other, because two
symbols can be joined by more than one edge and a mismatched total re-arms a
previous truncation bug. The last test here is that invariant.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import GraphEdge, GraphNode

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)
_TARGET = "src/auth/service.py"
_TARGET_SYM = "src/auth/service.py::login"

_WEAK = "src/reports/exporter.py"        # 0.6: over the shared floor, under 0.7
_CROSS_LANG = "web/dashboard.ts"         # 0.95 but a different extension
_NOISE = "src/legacy/old_auth.py"        # 0.4: under every floor
_STRONG = "src/db/models.py"             # 0.95, same extension


def _sym(node_id: str, sid: str) -> GraphNode:
    file_path, name = node_id.split("::", 1)
    return GraphNode(
        id=sid,
        repository_id=None,  # set by the caller
        node_id=node_id,
        node_type="symbol",
        name=name,
        file_path=file_path,
        kind="function",
        start_line=10,
        end_line=20,
        created_at=_NOW,
    )


async def _seed(session, repo_id: str) -> None:
    """One symbol in the target file, called from four different places."""
    callers = {
        _WEAK: 0.6,
        _CROSS_LANG: 0.95,
        _NOISE: 0.4,
        _STRONG: 0.95,
        _TARGET: 0.95,  # intra-file: a self-loop once projected onto files
    }
    rows: list[object] = []
    target = _sym(_TARGET_SYM, "cf_target")
    target.repository_id = repo_id
    rows.append(target)
    for i, (path, confidence) in enumerate(callers.items()):
        caller = _sym(f"{path}::caller_{i}", f"cf_src_{i}")
        caller.repository_id = repo_id
        rows.append(caller)
        rows.append(
            GraphEdge(
                id=f"cf_edge_{i}",
                repository_id=repo_id,
                source_node_id=caller.node_id,
                target_node_id=_TARGET_SYM,
                edge_type="calls",
                confidence=confidence,
                created_at=_NOW,
            )
        )
    session.add_all(rows)
    await session.flush()


@pytest.mark.asyncio
async def test_file_rollup_keeps_edges_above_the_shared_floor(setup_mcp, session):
    """A 0.6 call edge is a caller here, as it already is on every other surface."""
    from repowise.server.mcp_server import get_context

    await _seed(session, setup_mcp)

    t = (await get_context([_TARGET], include=["callers"], compact=False))["targets"][_TARGET]
    by_file = {c["file"]: c for c in t["callers"]}

    assert by_file[_WEAK]["inbound_calls"] == 1
    assert by_file[_STRONG]["inbound_calls"] == 1


@pytest.mark.asyncio
async def test_file_rollup_drops_noise_self_loops_and_cross_language(setup_mcp, session):
    """The other two guards the shared helper brings, and the one it already had."""
    from repowise.server.mcp_server import get_context

    await _seed(session, setup_mcp)

    t = (await get_context([_TARGET], include=["callers"], compact=False))["targets"][_TARGET]
    calls_by_file = {c["file"] for c in t["callers"] if c.get("inbound_calls")}

    assert _CROSS_LANG not in calls_by_file, "a .ts caller of a .py symbol is a coincidence"
    assert _NOISE not in calls_by_file, "0.4 is under the shared floor too"
    assert _TARGET not in calls_by_file, "an intra-file call is not a caller of the file"


@pytest.mark.asyncio
async def test_symbol_level_floor_and_totals_are_unchanged(setup_mcp, session):
    """INVARIANT: the symbol rows and the symbol totals still cut at 0.7 together.

    If either site drifted, the total and the row count would disagree and
    `callers_truncated` would appear (or wrongly stay absent) on a set that is
    in fact complete.
    """
    from repowise.server.mcp_server import get_context

    await _seed(session, setup_mcp)

    t = (await get_context([_TARGET_SYM], include=["callers"], compact=False))["targets"][
        _TARGET_SYM
    ]
    files = {c["file"] for c in t["callers"]}

    # Only the three edges at >= 0.7 survive; the self-loop is a caller at the
    # symbol layer, where it is a real recursive relation.
    assert files == {_CROSS_LANG, _STRONG, _TARGET}
    assert _WEAK not in files, "the symbol layer must still cut at 0.7"
    # Total == rows, so nothing reports as truncated.
    assert "callers_total" not in t
    assert not t.get("callers_truncated")
