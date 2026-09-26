"""Edge paths of the ``get_context`` include-block resolvers in ``tool_context/enrichment.py``.

Called directly so a regression names the helper; ``test_context*.py`` covers
the end-to-end shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import (
    CoverageFile,
    GraphEdge,
    GraphNode,
    HealthFileMetric,
    Repository,
)
from repowise.server.mcp_server import _basis
from repowise.server.mcp_server.tool_context import enrichment

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _fresh_basis_cache():
    # Every seed shares one repo id and updated_at, so a basis grouping cached
    # by an earlier test would otherwise answer for this one's graph.
    _basis.reset_cache()
    yield
    _basis.reset_cache()


async def _repo(session, repo_id: str) -> Repository:
    return await session.get(Repository, repo_id)


def _symbol(repo_id: str, node_id: str, *, name: str | None = None) -> GraphNode:
    file_path, _, qual = node_id.partition("::")
    return GraphNode(
        id=f"t_{node_id}",
        repository_id=repo_id,
        node_id=node_id,
        node_type="symbol",
        name=name or qual.split(".")[-1],
        file_path=file_path,
        kind="function",
        language="python",
        start_line=3,
        end_line=9,
        created_at=_NOW,
    )


def _call(repo_id: str, source: str, target: str, **extra) -> GraphEdge:
    return GraphEdge(
        id=f"t_{source}->{target}",
        repository_id=repo_id,
        source_node_id=source,
        target_node_id=target,
        edge_type="calls",
        confidence=extra.pop("confidence", 0.9),
        created_at=_NOW,
        **extra,
    )


# ---------------------------------------------------------------------------
# _unique_by_symbol
# ---------------------------------------------------------------------------


def test_unique_by_symbol_keeps_the_first_entry_per_symbol_in_order():
    """Callers sort by confidence first, so "first" must mean "highest confidence"."""
    rows = [
        {"symbol_id": "a", "confidence": 0.9},
        {"symbol_id": "b", "confidence": 0.8},
        {"symbol_id": "a", "confidence": 0.7},
    ]
    assert enrichment._unique_by_symbol(rows) == [
        {"symbol_id": "a", "confidence": 0.9},
        {"symbol_id": "b", "confidence": 0.8},
    ]


# ---------------------------------------------------------------------------
# _resolve_call_graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_graph_unknown_target_answers_empty_lists_without_a_note(
    session, populated_db
):
    """A target with no graph node gets empty lists, not a misleading type note."""
    out: dict = {}
    await enrichment._resolve_call_graph(
        session,
        await _repo(session, populated_db),
        "nowhere.py",
        "file",
        out,
        want_callers=True,
        want_callees=True,
    )
    assert out == {"callers": [], "callees": []}


@pytest.mark.asyncio
async def test_call_graph_file_target_asked_only_for_callees_explains_the_symbol_rule(
    session, populated_db
):
    """Callees have no file-level rollup, so the empty list must say why it is empty."""
    out: dict = {}
    await enrichment._resolve_call_graph(
        session,
        await _repo(session, populated_db),
        "src/auth/service.py",
        "file",
        out,
        want_callees=True,
    )
    assert out["callees"] == []
    assert "callers" not in out
    assert "require a symbol target" in out["_call_graph_note"]
    assert "is a file" in out["_call_graph_note"]


@pytest.mark.asyncio
async def test_call_graph_file_target_with_both_directions_rolls_up_callers_and_bases_callees(
    session, populated_db
):
    """A file nothing imports: both lists empty, and each empty list carries its basis."""
    out: dict = {}
    await enrichment._resolve_call_graph(
        session,
        await _repo(session, populated_db),
        "tests/test_service.py",
        "file",
        out,
        want_callers=True,
        want_callees=True,
    )
    assert out["callers"] == []
    assert out["callees"] == []
    assert "callers_basis" in out
    assert "callees_basis" in out
    assert out["_call_graph_note"].startswith("File-level rollup")


@pytest.mark.asyncio
async def test_call_graph_fuzzy_fallback_prefers_the_node_in_the_hinted_file(
    session, populated_db
):
    """``b.py::helper`` misses the exact id ``b.py::Cls.helper`` and falls back by name.

    Two symbols share the bare name; the one in the file the caller named must
    win, or the agent is handed a different function's callers.
    """
    rid = populated_db
    session.add_all(
        [
            _symbol(rid, "a.py::Cls.helper"),
            _symbol(rid, "b.py::Cls.helper"),
            _symbol(rid, "c.py::main"),
            _symbol(rid, "d.py::other"),
            _call(rid, "c.py::main", "b.py::Cls.helper"),
            _call(rid, "d.py::other", "a.py::Cls.helper"),
        ]
    )
    await session.flush()

    out: dict = {}
    await enrichment._resolve_call_graph(
        session,
        await _repo(session, rid),
        "b.py::helper",
        "symbol",
        out,
        want_callers=True,
    )
    assert [c["symbol_id"] for c in out["callers"]] == ["c.py::main"]


@pytest.mark.asyncio
async def test_call_graph_serves_callees_and_places_a_recursive_call_on_both_sides(
    session, populated_db
):
    """A self-call is one edge that matches both directions.

    It must appear once under callers and once under callees; the resolution
    origin rides along as ``via`` when the index stamped one.
    """
    rid = populated_db
    session.add_all(
        [
            _symbol(rid, "m.py::walk"),
            _symbol(rid, "m.py::visit"),
            _call(rid, "m.py::walk", "m.py::walk"),
            _call(rid, "m.py::walk", "m.py::visit", resolution_origin="import_resolved"),
        ]
    )
    await session.flush()

    out: dict = {}
    await enrichment._resolve_call_graph(
        session,
        await _repo(session, rid),
        "m.py::walk",
        "symbol",
        out,
        want_callers=True,
        want_callees=True,
    )
    assert [c["symbol_id"] for c in out["callers"]] == ["m.py::walk"]
    callees = {c["symbol_id"]: c for c in out["callees"]}
    assert set(callees) == {"m.py::walk", "m.py::visit"}
    assert callees["m.py::visit"]["via"] == "import_resolved"
    assert callees["m.py::visit"]["edge_type"] == "calls"
    assert callees["m.py::visit"]["line"] == 3
    # Populated lists are their own evidence and earn no basis.
    assert "callers_basis" not in out
    assert "callees_basis" not in out


# ---------------------------------------------------------------------------
# _resolve_metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_for_an_unknown_target_is_null(session, populated_db):
    out: dict = {}
    await enrichment._resolve_metrics(
        session, await _repo(session, populated_db), "nowhere.py", out
    )
    assert out == {"metrics": None}


@pytest.mark.asyncio
async def test_metrics_report_measured_betweenness_with_its_percentile(session, populated_db):
    """Once centrality has run, betweenness is a number with a peer percentile.

    models.py ranks above two of the four seeded file nodes, so both percentiles are 50.
    """
    node = (
        await session.execute(select(GraphNode).where(GraphNode.node_id == "src/db/models.py"))
    ).scalar_one()
    node.betweenness_commit = "abc1234"
    node.community_meta_json = '{"label": "Persistence"}'
    await session.flush()

    out: dict = {}
    await enrichment._resolve_metrics(
        session, await _repo(session, populated_db), "src/db/models.py", out
    )
    assert out["metrics"] == {
        "pagerank": 0.6,
        "pagerank_percentile": 50,
        "in_degree": 1,
        "out_degree": 0,
        "community_id": 2,
        "community_label": "Persistence",
        "betweenness": 0.3,
        "betweenness_percentile": 50,
    }


@pytest.mark.asyncio
async def test_metrics_survive_unparseable_community_meta(session, populated_db):
    """A corrupt meta blob costs the label only; the block still renders."""
    node = (
        await session.execute(select(GraphNode).where(GraphNode.node_id == "src/auth/service.py"))
    ).scalar_one()
    node.community_meta_json = "{not json"
    await session.flush()

    out: dict = {}
    await enrichment._resolve_metrics(
        session, await _repo(session, populated_db), "src/auth/service.py", out
    )
    assert out["metrics"]["community_label"] is None
    assert out["metrics"]["betweenness"] is None
    assert "not scored yet" in out["metrics"]["betweenness_note"]


# ---------------------------------------------------------------------------
# _resolve_community
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_community_is_null_for_an_unknown_target(session, populated_db):
    out: dict = {}
    await enrichment._resolve_community(
        session, await _repo(session, populated_db), "nowhere.py", out
    )
    assert out == {"community": None}


@pytest.mark.asyncio
async def test_community_names_members_by_pagerank_and_neighbours_by_crossing_edges(
    session, populated_db
):
    """service.py sits in community 1 and imports models.py in community 2.

    Members come back in PageRank order; the neighbour's label falls back to
    ``cluster_<id>`` when its own meta blob does not parse.
    """
    rows = {
        n.node_id: n
        for n in (await session.execute(select(GraphNode))).scalars().all()
    }
    rows["src/auth/service.py"].community_meta_json = '{"label": "Auth", "cohesion": 0.4567}'
    rows["src/db/models.py"].community_meta_json = "{not json"
    await session.flush()

    out: dict = {}
    await enrichment._resolve_community(
        session, await _repo(session, populated_db), "src/auth/service.py", out
    )
    assert out["community"] == {
        "id": 1,
        "label": "Auth",
        "cohesion": 0.457,
        "top_members": [
            "src/auth/service.py",
            "src/auth/middleware.py",
            "tests/test_service.py",
        ],
        "neighbors": [{"id": 2, "label": "cluster_2", "cross_edges": 1}],
    }


@pytest.mark.asyncio
async def test_community_label_falls_back_to_cluster_id_when_unparseable(session, populated_db):
    node = (
        await session.execute(select(GraphNode).where(GraphNode.node_id == "src/db/models.py"))
    ).scalar_one()
    node.community_meta_json = "{not json"
    await session.flush()

    out: dict = {}
    await enrichment._resolve_community(
        session, await _repo(session, populated_db), "src/db/models.py", out
    )
    assert out["community"]["label"] == "cluster_2"
    assert out["community"]["cohesion"] == 0.0
    # models.py only receives an edge; crossings are counted from this side.
    assert out["community"]["neighbors"] == []


# ---------------------------------------------------------------------------
# _resolve_health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_is_null_for_a_non_file_target_without_a_path(session, health_data):
    out: dict = {}
    await enrichment._resolve_health(
        session, await _repo(session, health_data), "src/auth", "module", out
    )
    assert out == {"health": None}


@pytest.mark.asyncio
async def test_health_is_null_for_a_file_with_no_metric_row(session, health_data):
    out: dict = {}
    await enrichment._resolve_health(
        session, await _repo(session, health_data), "src/auth/middleware.py", "file", out
    )
    assert out == {"health": None}


@pytest.mark.asyncio
async def test_health_for_a_symbol_target_reads_its_file_with_stored_coverage_and_signals(
    session, health_data
):
    """``file::Symbol`` resolves to the file's metric row, with stored coverage,
    the top two findings by impact, and process signals.
    """
    rid = health_data
    session.add(
        CoverageFile(
            repository_id=rid,
            file_path="src/auth/service.py",
            source_format="lcov",
            line_coverage_pct=61.5,
            branch_coverage_pct=40.0,
            total_coverable_lines=200,
        )
    )
    await session.flush()

    out: dict = {}
    await enrichment._resolve_health(
        session,
        await _repo(session, rid),
        "src/auth/service.py::AuthService",
        "symbol",
        out,
    )
    health = out["health"]
    assert health["score"] == 4.5
    assert health["max_ccn"] == 15
    assert health["has_test_file"] is False
    assert health["module"] == "auth"
    assert health["coverage"] == {
        "source_format": "lcov",
        "line_coverage_pct": 61.5,
        "branch_coverage_pct": 40.0,
        "total_coverable_lines": 200,
    }
    assert len(health["top_biomarkers"]) == 2
    top = health["top_biomarkers"][0]
    assert top["biomarker_type"] == "complex_method"
    assert top["function_name"] == "authenticate"
    assert top["impact"] == 1.2
    assert isinstance(top["suggestion"], str) and top["suggestion"]
    signals = health["signals"]
    assert signals["commit_count_90d"] == 8
    assert signals["primary_owner_name"] == "Alice"
    # middleware.py and the test file import service.py; it imports models.py.
    assert signals["in_degree"] == 2
    assert signals["out_degree"] == 1


@pytest.mark.asyncio
async def test_health_falls_back_to_metric_coverage_and_omits_null_function_name(
    session, health_data
):
    """No ingested coverage row: the metric's own percentages stand in.

    A file-level finding carries no ``function_name`` key at all rather than a
    null, matching get_risk's cards.
    """
    from repowise.core.persistence.crud import save_health_findings

    rid = health_data
    metric = (
        await session.execute(
            select(HealthFileMetric).where(HealthFileMetric.file_path == "src/db/models.py")
        )
    ).scalar_one()
    metric.line_coverage_pct = 88.0
    await save_health_findings(
        session,
        rid,
        [
            {
                "file_path": "src/db/models.py",
                "biomarker_type": "low_cohesion",
                "severity": "medium",
                "function_name": None,
                "line_start": None,
                "line_end": None,
                "details": {},
                "health_impact": 0.5,
                "reason": "User has low cohesion",
                "dimension": "maintainability",
            }
        ],
    )
    await session.flush()

    out: dict = {}
    await enrichment._resolve_health(
        session, await _repo(session, rid), "src/db/models.py", "file", out
    )
    health = out["health"]
    assert health["coverage"] == {"line_coverage_pct": 88.0, "branch_coverage_pct": None}
    assert [b["biomarker_type"] for b in health["top_biomarkers"]] == ["low_cohesion"]
    assert "function_name" not in health["top_biomarkers"][0]


@pytest.mark.asyncio
async def test_health_omits_signals_for_a_file_with_no_history_and_no_graph_node(
    session, health_data
):
    """No git row and no graph node means no signal is known; the key is absent, not empty."""
    from repowise.core.persistence.crud import save_health_metrics

    rid = health_data
    await save_health_metrics(
        session,
        rid,
        [
            {
                "file_path": "src/brand_new.py",
                "score": 9.0,
                "max_ccn": 1,
                "max_nesting": 1,
                "nloc": 5,
                "has_test_file": False,
                "module": "src",
            }
        ],
    )
    await session.flush()

    out: dict = {}
    await enrichment._resolve_health(
        session, await _repo(session, rid), "src/brand_new.py", "file", out
    )
    health = out["health"]
    assert health["top_biomarkers"] == []
    assert "coverage" not in health
    assert "signals" not in health


# ---------------------------------------------------------------------------
# attach_doc_references / _resolve_skeleton
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_doc_references_for_a_module_target_are_null_and_the_marker_is_popped(
    session, populated_db
):
    """A directory cannot be named by a reference row, so its block is null.

    The private path marker must never reach the response.
    """
    cards = {"src/auth": {"title": "Auth", enrichment._DOC_DRIFT_PATH: None}}
    await enrichment.attach_doc_references(session, await _repo(session, populated_db), cards)
    assert cards == {"src/auth": {"title": "Auth", "doc_drift": None}}


@pytest.mark.asyncio
async def test_doc_references_do_nothing_when_no_card_asked(session, populated_db):
    cards = {"src/auth/service.py": {"title": "Auth Service"}}
    await enrichment.attach_doc_references(session, await _repo(session, populated_db), cards)
    assert cards == {"src/auth/service.py": {"title": "Auth Service"}}


@pytest.mark.asyncio
async def test_skeleton_without_a_repo_path_is_an_error_not_a_crash(session, populated_db):
    out: dict = {}
    await enrichment._resolve_skeleton(
        session, await _repo(session, populated_db), "src/auth/service.py", "file", out
    )
    assert out == {"skeleton": {"error": "MCP server has no repo path configured."}}


@pytest.mark.asyncio
async def test_skeleton_of_a_file_with_no_symbol_bounds_is_raw_with_a_note(
    session, populated_db, tmp_path
):
    """No indexed symbols for the file: the source is served as-is and says so."""
    (tmp_path / "notes.py").write_text("X = 1\nY = 2\n", encoding="utf-8")

    out: dict = {}
    await enrichment._resolve_skeleton(
        session,
        await _repo(session, populated_db),
        "notes.py",
        "file",
        out,
        repo_root=tmp_path,
    )
    skeleton = out["skeleton"]
    assert skeleton["mode"] == "raw"
    assert skeleton["verified"] is True
    assert "X = 1" in skeleton["text"]
    assert skeleton["note"].startswith("No usable symbol bounds")
