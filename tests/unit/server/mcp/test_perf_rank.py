"""The performance dimension's ranking key.

Every performance finding carries ``health_impact: 0`` by construction — the
dimension is deliberately never blended into the score — so the ranked list came
back in whatever order the impact tie broke to, which is file order. "Which of
these 697 matters" was unanswerable from the payload, and the *cap* was
arbitrary with it: ``include=['performance']`` returned 20 of 697 chosen by
nothing.

``_perf_rank`` is an ordering key, not a score. Nothing here is blended into
``score`` / ``performance_score`` and nothing here was fitted against the defect
corpus; the frozen weights stay frozen.
"""

from __future__ import annotations

import uuid

import pytest

from repowise.core.analysis.health.biomarkers import registered_biomarkers
from repowise.core.analysis.health.perf.opportunity_rank import (
    MULTIPLIER_POINTS,
    UNKNOWN_MULTIPLIER_POINTS,
)
from repowise.server.mcp_server.tool_health import _perf_rank, _rank_emitted

# ---------------------------------------------------------------------------
# The key itself
# ---------------------------------------------------------------------------


def test_every_performance_biomarker_carries_a_weight() -> None:
    """One weight table, and it must not silently acquire a default.

    The observation key and the opportunity rank read the same table. They
    used to keep one each and had already drifted apart on markers both named,
    so a finding and the opportunity built from it disagreed about the same
    evidence.

    A detector added without a weight ranks at the floor, which is the safe
    direction but also an invisible one — a new high-cost marker would sort
    below ``string_concat_in_loop`` and nobody would see it. This is the
    mechanism that makes adding one loud.
    """
    registered = {
        b.name for b in registered_biomarkers() if getattr(b, "category", "") == "performance"
    }
    assert registered, "no performance detectors registered — the check is vacuous"
    assert registered <= set(MULTIPLIER_POINTS), (
        f"unweighted performance biomarkers: {sorted(registered - set(MULTIPLIER_POINTS))}"
    )
    # And no stale entries pointing at detectors that no longer exist.
    assert set(MULTIPLIER_POINTS) <= registered, (
        f"weights for unregistered markers: {sorted(set(MULTIPLIER_POINTS) - registered)}"
    )


def test_a_cross_function_subprocess_n_plus_one_outranks_a_filesystem_one() -> None:
    """Boundary kind separates two findings of identical shape.

    "A subprocess spawn in a loop is not a filesystem stat in a loop" is the
    whole reason the key reads ``boundary_kind``.
    """
    spawn = _perf_rank("io_in_loop", {"boundary_kind": "subprocess", "cross_function": True})
    stat = _perf_rank("io_in_loop", {"boundary_kind": "filesystem", "cross_function": True})
    assert spawn > stat


def test_a_cross_function_hit_outranks_the_same_hit_inside_one_function() -> None:
    """The minimum the item asked for: cross-function N+1s above intra-function.

    An intra-function loop is often visibly bounded at the call site; a
    cross-function one is the one nobody sees by reading the loop.
    """
    same = {"boundary_kind": "db", "cross_function": False}
    across = {"boundary_kind": "db", "cross_function": True}
    assert _perf_rank("io_in_loop", across) > _perf_rank("io_in_loop", same)


def test_the_gated_markers_carry_their_hotness_proof() -> None:
    """``hot_path_sync_io`` is only ever emitted on a hot, reachable function.

    ``perf.gated.collect_centrality_gated`` will not produce it otherwise, so
    its presence *is* the request-reachability signal and needs no new column.
    It therefore outranks the cheap in-loop CPU markers at the same boundary,
    while sitting level with a plain N+1 marker — it proves hotness and no
    multiplier, the loop proves a multiplier and no hotness.
    """
    hot = _perf_rank("hot_path_sync_io", {"boundary_kind": "db"})
    cheap = _perf_rank("string_concat_in_loop", {})
    assert hot > cheap
    assert hot == _perf_rank("io_in_loop", {"boundary_kind": "db"})
    # Superlinear beats both.
    assert _perf_rank("nested_loop_quadratic", {}) > _perf_rank("io_in_loop", {})


def test_an_unknown_marker_sinks_rather_than_floats() -> None:
    assert _perf_rank("some_future_marker", {}) == UNKNOWN_MULTIPLIER_POINTS
    assert min(MULTIPLIER_POINTS.values()) > UNKNOWN_MULTIPLIER_POINTS


def test_malformed_details_never_raise() -> None:
    """Details come from a JSON column; the reader has to survive any shape."""
    for details in (None, [], "not a dict", 7, {}):
        assert _perf_rank("io_in_loop", details) >= 1


# ---------------------------------------------------------------------------
# The sort
# ---------------------------------------------------------------------------


class _Row:
    def __init__(self, impact, dimension, biomarker_type, details_json, file_path):
        self.health_impact = impact
        self.dimension = dimension
        self.biomarker_type = biomarker_type
        self.details_json = details_json
        self.file_path = file_path

    def __repr__(self) -> str:  # pragma: no cover - failure output only
        return f"{self.file_path}:{self.biomarker_type}"


def test_the_sort_reorders_the_perf_tier_and_leaves_the_defect_order_alone() -> None:
    rows = [
        _Row(2.0, "defect", "complex_method", None, "b.py"),
        _Row(0.0, "performance", "string_concat_in_loop", "{}", "a.py"),
        _Row(3.0, "defect", "god_class", None, "c.py"),
        _Row(
            0.0,
            "performance",
            "io_in_loop",
            '{"boundary_kind": "subprocess", "cross_function": true}',
            "z.py",
        ),
    ]
    ranked = _rank_emitted(rows)
    # Defect rows keep their impact order and stay ahead of the zero-impact tier.
    assert [r.file_path for r in ranked[:2]] == ["c.py", "b.py"]
    # The perf tier is reordered by cost, not by file name.
    assert [r.file_path for r in ranked[2:]] == ["z.py", "a.py"]


def test_ties_break_on_path_so_the_order_is_total() -> None:
    """Two findings that rank the same used to swap places between calls."""
    mk = lambda p: _Row(0.0, "performance", "io_in_loop", '{"boundary_kind": "db"}', p)  # noqa: E731
    assert [r.file_path for r in _rank_emitted([mk("b.py"), mk("a.py")])] == ["a.py", "b.py"]
    assert [r.file_path for r in _rank_emitted([mk("a.py"), mk("b.py")])] == ["a.py", "b.py"]


def test_rows_without_a_details_column_still_sort() -> None:
    """The narrow dashboard read carries no ``details_json`` unless the caller
    filtered to ``performance`` — those rank on the marker alone."""

    class _Lite:
        def __init__(self, bt, p):
            self.health_impact = 0.0
            self.dimension = "performance"
            self.biomarker_type = bt
            self.file_path = p

    ranked = _rank_emitted([_Lite("string_concat_in_loop", "a.py"), _Lite("io_in_loop", "z.py")])
    assert [r.file_path for r in ranked] == ["z.py", "a.py"]


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


@pytest.fixture
async def perf_findings(session, health_data: str) -> str:
    """Nine performance findings spanning the whole rank range.

    File names are deliberately in the *opposite* order to cost, so a response
    that still sorts by path is unmistakable.
    """
    import json

    from repowise.core.persistence.models import HealthFinding

    rows = [
        ("a_cheap.py", "string_concat_in_loop", {}),
        ("b_member.py", "membership_test_against_list_in_loop", {}),
        ("c_fs.py", "io_in_loop", {"boundary_kind": "filesystem", "cross_function": False}),
        ("d_db.py", "io_in_loop", {"boundary_kind": "db", "cross_function": False}),
        ("e_hot_fs.py", "hot_path_sync_io", {"boundary_kind": "filesystem"}),
        ("f_db_xfn.py", "io_in_loop", {"boundary_kind": "db", "cross_function": True}),
        ("g_hot_spawn.py", "hot_path_sync_io", {"boundary_kind": "subprocess"}),
        ("h_nested_db.py", "nested_loop_with_io", {"boundary_kind": "db"}),
        ("i_spawn_xfn.py", "io_in_loop", {"boundary_kind": "subprocess", "cross_function": True}),
    ]
    for path, biomarker, details in rows:
        session.add(
            HealthFinding(
                id=str(uuid.uuid4()),
                repository_id=health_data,
                file_path=path,
                biomarker_type=biomarker,
                severity="medium",
                function_name="f",
                line_start=1,
                line_end=1,
                details_json=json.dumps(details),
                health_impact=0.0,
                reason=f"{biomarker} in {path}",
                dimension="performance",
                status="open",
            )
        )
    await session.flush()
    return health_data


@pytest.mark.asyncio
async def test_the_perf_head_is_the_costliest_findings_not_the_first_alphabetically(
    setup_mcp, perf_findings
):
    """The defect this closes is about *selection*, not display.

    With ``include=['performance']`` the whole list is perf, every row ties at
    impact 0, and the cap keeps whichever ones the tie broke to. Before the key
    that was file order, so a small ``limit`` returned the cheapest findings in
    the repo and called them the top ones.

    The base fixture's perf finding carries a non-zero impact, so it still
    leads: the key breaks ties *within* an impact tier and never reorders
    across one.
    """
    from repowise.server.mcp_server import get_health

    result = await get_health(include=["biomarkers", "performance"], limit=4)
    assert [f["file_path"] for f in result["findings"]] == [
        "src/auth/service.py",
        "h_nested_db.py",
        "i_spawn_xfn.py",
        "f_db_xfn.py",
    ]
    ranks = [f["perf_rank"] for f in result["findings"]]
    # A tie at the top, broken by ``file_path`` so the order is total.
    assert ranks[1] == ranks[2] > ranks[3]
    # The total still describes the whole filtered set, so the cap stays visible.
    assert result["findings_total"] == 10


@pytest.mark.asyncio
async def test_perf_rank_is_absent_from_every_other_dimension(setup_mcp, perf_findings):
    """Not zero — a defect finding ranks on ``weighted_deficit``, and a 0 here
    would read as "measured, and it is nothing"."""
    from repowise.server.mcp_server import get_health

    # Naming the dimension, because the impact-ranked list leaves it out by
    # default: every performance finding scores zero impact, so a ranking by
    # impact is not where it belongs.
    result = await get_health(
        include=["biomarkers", "defect", "maintainability", "performance"], limit=50
    )
    by_dim = {}
    for f in result["findings"]:
        by_dim.setdefault(f["dimension"], []).append(f)
    assert by_dim.get("defect"), "fixture should carry defect findings"
    assert all("perf_rank" not in f for f in by_dim["defect"])
    assert all("perf_rank" not in f for f in by_dim.get("maintainability", []))
    assert all("perf_rank" in f for f in by_dim["performance"])


@pytest.mark.asyncio
async def test_narrow_projection_leads_with_one_shared_causal_opportunity(
    setup_mcp, perf_findings, session
):
    """One shared helper, five callers, one cause, and a recoverable tail."""
    import json

    from repowise.core.persistence.crud import finalize_performance_opportunities
    from repowise.core.persistence.models import HealthFinding
    from repowise.server.mcp_server import get_health

    for index, caller in enumerate(
        ("src/a.py", "src/b.py", "src/c.py", "src/d.py", "src/e.py"), start=10
    ):
        session.add(
            HealthFinding(
                id=str(uuid.uuid4()),
                repository_id=perf_findings,
                file_path=caller,
                biomarker_type="io_in_loop",
                severity="medium",
                function_name="run",
                line_start=index,
                line_end=index,
                details_json=json.dumps(
                    {
                        "boundary_kind": "db",
                        "cross_function": True,
                        "path": [f"{caller}::run", "src/shared.py::load", "src/db.py::fetch"],
                        "resolution_basis": "call-site",
                    }
                ),
                health_impact=0.0,
                reason="shared N+1",
                dimension="performance",
                status="open",
            )
        )
    await session.flush()
    await finalize_performance_opportunities(session, perf_findings)
    await session.commit()

    result = await get_health(
        include=["performance"], only=["performance_opportunities"], limit=50
    )
    shared = [
        item
        for item in result["performance_opportunities"]
        if item["terminal_sink"] == "src/db.py::fetch"
    ]
    assert len(shared) == 1
    assert shared[0]["intervention_symbol"] == "src/shared.py::load"
    assert shared[0]["affected_call_sites_total"] == 5
    assert shared[0]["observations_total"] == 5
    assert {item["line_start"] for item in shared[0]["evidence"]} == {10, 11, 12}
    assert {item["function_name"] for item in shared[0]["evidence"]} == {"run"}
    assert shared[0]["evidence_total"] == 5
    assert shared[0]["evidence_emitted"] == 3
    assert shared[0]["evidence_reduced_reason"] == "evidence_page"

    # The tail is recovered by paging it, not by unpacking an omission blob.
    # A blob would have cost the full read of every observation to build.
    tail = await get_health(
        opportunity_id=shared[0]["opportunity_id"],
        only=["performance_evidence"],
        cursor=shared[0]["evidence_next_cursor"],
        limit=50,
    )
    assert [item["line_start"] for item in tail["evidence"]] == [13, 14]
    assert tail["evidence_total"] == 5
    assert "evidence_next_cursor" not in tail

    # And every reference round-trips through the finding selector.
    detail = await get_health(finding_id=tail["evidence"][0]["finding_id"])
    assert detail["resolved"] is True
    assert detail["finding"]["file_path"] == "src/d.py"
