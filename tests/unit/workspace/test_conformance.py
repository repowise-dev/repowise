"""Tests for architecture conformance — matchers, rule checking, cycles, IO.

The negative cases are load-bearing: a clean graph must produce no violations,
a behavioral co-change edge must never count as a dependency, and an ``allow``
exception must suppress an otherwise-denied edge.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from repowise.core.workspace.config import (
    ConformanceConfig,
    ConformanceRule,
    RepoEntry,
    WorkspaceConfig,
)
from repowise.core.workspace.conformance import (
    CONFORMANCE_FILENAME,
    ConformanceReport,
    build_conformance_report,
    check_conformance,
    compile_matcher,
    load_conformance_report,
    run_conformance_check,
    save_conformance_report,
    tags_by_repo_from_config,
)
from repowise.core.workspace.cycles import detect_cycles
from repowise.core.workspace.system_graph import SystemEdge, SystemGraph, SystemNode

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _node(node_id, repo=None, name=None, service_path=None) -> SystemNode:
    return SystemNode(
        id=node_id,
        repo=repo or node_id.split("::", 1)[0],
        service_path=service_path,
        name=name or node_id,
    )


def _edge(source, target, kind="http", structural=True, match="exact") -> SystemEdge:
    return SystemEdge(
        id=f"{source}->{target}:{kind}",
        source=source,
        target=target,
        kind=kind,
        match_type=match,
        confidence=1.0,
        weight=1,
        structural=structural,
    )


def _graph(nodes, edges) -> SystemGraph:
    return SystemGraph(nodes=nodes, edges=edges)


# ---------------------------------------------------------------------------
# Matchers
# ---------------------------------------------------------------------------


def test_wildcard_matcher_matches_any():
    pred = compile_matcher("*")
    assert pred(_node("frontend"), {}) is True
    assert pred(_node("db"), {}) is True


def test_tag_matcher_matches_repo_tag():
    pred = compile_matcher("tag:edge")
    tags = {"frontend": ["edge", "ui"], "db": []}
    assert pred(_node("frontend"), tags) is True
    assert pred(_node("db"), tags) is False


def test_glob_matcher_matches_id_repo_and_name():
    # bare alias matches the repo
    assert compile_matcher("frontend")(_node("frontend"), {}) is True
    # glob over a service node id
    svc = _node("api::services/auth", repo="api", name="auth", service_path="services/auth")
    assert compile_matcher("api::*")(svc, {}) is True
    # name match
    assert compile_matcher("auth")(svc, {}) is True
    # non-match
    assert compile_matcher("payments")(_node("frontend"), {}) is False


# ---------------------------------------------------------------------------
# Rule checking — violation + clean
# ---------------------------------------------------------------------------


def test_deny_rule_flags_violating_dependency():
    g = _graph(
        [_node("frontend"), _node("db")],
        [_edge("frontend", "db")],
    )
    violations = check_conformance(g, [ConformanceRule(source="frontend", target="db")])
    assert len(violations) == 1
    v = violations[0]
    assert v.source == "frontend"
    assert v.target == "db"
    assert v.edge_kind == "http"
    assert v.rule_source == "frontend"
    assert v.rule_target == "db"


def test_clean_graph_has_no_violations():
    g = _graph(
        [_node("frontend"), _node("api"), _node("db")],
        [_edge("frontend", "api"), _edge("api", "db")],
    )
    # frontend must not talk to db directly; it doesn't (only api -> db).
    violations = check_conformance(g, [ConformanceRule(source="frontend", target="db")])
    assert violations == []


def test_wildcard_deny_rule():
    g = _graph(
        [_node("frontend"), _node("api"), _node("legacy")],
        [_edge("frontend", "legacy"), _edge("api", "legacy")],
    )
    violations = check_conformance(g, [ConformanceRule(source="*", target="legacy")])
    assert {v.source for v in violations} == {"frontend", "api"}


def test_allow_rule_whitelists_exception():
    g = _graph(
        [_node("frontend"), _node("migrations"), _node("db")],
        [_edge("frontend", "db"), _edge("migrations", "db")],
    )
    rules = [
        ConformanceRule(source="*", target="db"),  # deny all -> db
        ConformanceRule(source="migrations", target="db", allow=True),  # except migrations
    ]
    violations = check_conformance(g, rules)
    assert [v.source for v in violations] == ["frontend"]


def test_behavioral_edge_is_never_a_violation():
    g = _graph(
        [_node("frontend"), _node("db")],
        [_edge("frontend", "db", kind="co_change", structural=False)],
    )
    violations = check_conformance(g, [ConformanceRule(source="frontend", target="db")])
    assert violations == []


def test_tag_based_rule():
    g = _graph(
        [_node("frontend"), _node("admin"), _node("db")],
        [_edge("frontend", "db"), _edge("admin", "db")],
    )
    tags = {"frontend": ["ui"], "admin": ["ui"], "db": ["data"]}
    violations = check_conformance(g, [ConformanceRule(source="tag:ui", target="tag:data")], tags)
    assert {v.source for v in violations} == {"frontend", "admin"}


def test_no_deny_rules_yields_no_violations():
    g = _graph([_node("a"), _node("b")], [_edge("a", "b")])
    # only an allow rule present
    assert check_conformance(g, [ConformanceRule(source="a", target="b", allow=True)]) == []


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


def test_two_node_cycle():
    g = _graph(
        [_node("a"), _node("b")],
        [_edge("a", "b"), _edge("b", "a")],
    )
    cycles, total, _capped = detect_cycles(g)
    assert len(cycles) == 1
    assert total == 1
    assert set(cycles[0].nodes) == {"a", "b"}
    assert len(cycles[0].edge_ids) == 2


def test_longer_cycle():
    g = _graph(
        [_node("a"), _node("b"), _node("c")],
        [_edge("a", "b"), _edge("b", "c"), _edge("c", "a")],
    )
    cycles, total, _capped = detect_cycles(g)
    assert len(cycles) == 1
    assert total == 1
    assert set(cycles[0].nodes) == {"a", "b", "c"}
    assert cycles[0].length == 3


def test_cycles_start_at_smallest_node_id():
    # Regression test for #4507: nx.simple_cycles begins each cycle at whatever
    # node its iteration order lands on (varies with PYTHONHASHSEED). Every
    # rotation of the same loop must canonicalize to the same cycle, so the
    # loop printed by `repowise workspace check` and the `cycles` in
    # conformance.json never change between runs on the same graph.
    import networkx as nx

    g = _graph(
        [_node("b"), _node("c"), _node("a")],
        [_edge("a", "b"), _edge("b", "c"), _edge("c", "a")],
    )
    for rotation in (["a", "b", "c"], ["b", "c", "a"], ["c", "a", "b"]):
        with patch.object(nx, "simple_cycles", return_value=[rotation]):
            (cycle,), total, _capped = detect_cycles(g)
        assert cycle.nodes == ["a", "b", "c"]
        assert cycle.edge_ids == ["a->b:http", "b->c:http", "c->a:http"]
        assert cycle.leg_kinds == [["http"], ["http"], ["http"]]
        assert total == 1


def test_acyclic_graph_has_no_cycles():
    g = _graph(
        [_node("a"), _node("b"), _node("c")],
        [_edge("a", "b"), _edge("b", "c")],
    )
    assert detect_cycles(g) == ([], 0, False)


def test_a_cycle_closed_by_an_event_counts_and_names_each_legs_kinds():
    g = _graph(
        [_node("a"), _node("b")],
        [_edge("a", "b", kind="event"), _edge("b", "a"), _edge("b", "a", kind="db")],
    )
    [cycle], total, _capped = detect_cycles(g)
    assert total == 1
    legs = dict(zip(cycle.nodes, cycle.leg_kinds, strict=True))
    assert legs == {"a": ["event"], "b": ["db", "http"]}
    assert cycle.async_legs == 1
    assert cycle.to_dict()["async_legs"] == 1
    assert type(cycle).from_dict(cycle.to_dict()).leg_kinds == cycle.leg_kinds


def test_cycles_ignore_behavioral_edges():
    # a->b structural, b->a only via co-change → not a real cycle
    g = _graph(
        [_node("a"), _node("b")],
        [_edge("a", "b"), _edge("b", "a", kind="co_change", structural=False)],
    )
    assert detect_cycles(g) == ([], 0, False)


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def test_build_report_combines_violations_and_cycles():
    g = _graph(
        [_node("frontend"), _node("db")],
        [_edge("frontend", "db"), _edge("db", "frontend")],
    )
    report = build_conformance_report(g, [ConformanceRule(source="frontend", target="db")])
    assert report.to_dict()["violation_count"] == 1
    assert len(report.violations) == 1
    assert len(report.cycles) == 1
    assert report.rules_evaluated == 1
    assert report.has_findings
    assert set(report.violating_repos) == {"frontend", "db"}


def test_report_to_dict_round_trip():
    g = _graph([_node("a"), _node("b")], [_edge("a", "b"), _edge("b", "a")])
    report = build_conformance_report(g, [ConformanceRule(source="a", target="b")])
    restored = ConformanceReport.from_dict(report.to_dict())
    assert len(restored.violations) == len(report.violations)
    assert len(restored.cycles) == len(report.cycles)
    assert restored.rules_evaluated == report.rules_evaluated


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def test_config_round_trips_rules_and_tags():
    cfg = WorkspaceConfig(
        repos=[
            RepoEntry(path="fe", alias="frontend", tags=["ui", "edge"]),
            RepoEntry(path="db", alias="db"),
        ],
        conformance=ConformanceConfig(
            rules=[
                ConformanceRule(source="frontend", target="db", description="no direct db"),
                ConformanceRule(source="migrations", target="db", allow=True),
            ]
        ),
    )
    restored = WorkspaceConfig.from_dict(cfg.to_dict())
    assert restored.repos[0].tags == ["ui", "edge"]
    assert len(restored.conformance.rules) == 2
    r0 = restored.conformance.rules[0]
    assert r0.source == "frontend" and r0.target == "db" and r0.allow is False
    assert r0.description == "no direct db"
    assert restored.conformance.rules[1].allow is True


def test_config_omits_empty_conformance():
    cfg = WorkspaceConfig(repos=[RepoEntry(path="a", alias="a")])
    assert "conformance" not in cfg.to_dict()


def test_tags_by_repo_from_config():
    cfg = WorkspaceConfig(
        repos=[
            RepoEntry(path="fe", alias="frontend", tags=["ui"]),
            RepoEntry(path="db", alias="db"),
        ]
    )
    assert tags_by_repo_from_config(cfg) == {"frontend": ["ui"], "db": []}


# ---------------------------------------------------------------------------
# Persistence + orchestrator
# ---------------------------------------------------------------------------


def test_save_and_load_report(tmp_path: Path):
    g = _graph([_node("a"), _node("b")], [_edge("a", "b"), _edge("b", "a")])
    report = build_conformance_report(g, [ConformanceRule(source="a", target="b")])
    out = save_conformance_report(report, tmp_path)
    assert out.name == CONFORMANCE_FILENAME
    loaded = load_conformance_report(tmp_path)
    assert loaded is not None
    assert len(loaded.violations) == 1
    assert len(loaded.cycles) == 1


def test_load_missing_report_returns_none(tmp_path: Path):
    assert load_conformance_report(tmp_path) is None


def test_run_conformance_check_persists(tmp_path: Path):
    cfg = WorkspaceConfig(
        repos=[RepoEntry(path="fe", alias="frontend"), RepoEntry(path="db", alias="db")],
        conformance=ConformanceConfig(rules=[ConformanceRule(source="frontend", target="db")]),
    )
    g = _graph([_node("frontend"), _node("db")], [_edge("frontend", "db")])
    report = run_conformance_check(cfg, tmp_path, g)
    assert len(report.violations) == 1
    # persisted
    loaded = load_conformance_report(tmp_path)
    assert loaded is not None and len(loaded.violations) == 1


# ---------------------------------------------------------------------------
# Never-ran vs found-nothing
# ---------------------------------------------------------------------------


def test_building_a_report_stamps_it():
    """Producing a report is running the check. Before this, run_conformance_check
    left generated_at empty on every run, so a real all-clear was
    indistinguishable from a check that never happened."""
    g = _graph([_node("a"), _node("b")], [_edge("a", "b")])
    report = build_conformance_report(g, [])
    assert report.ran
    assert report.generated_at
    assert report.to_dict()["generated_at"]


def test_an_unbuilt_report_is_not_a_clean_one():
    report = ConformanceReport()
    assert not report.ran
    assert report.generated_at is None
    assert not report.has_findings  # zero findings...
    # ...but the zero is meaningless, and the artifact says so.
    assert report.to_dict()["generated_at"] is None


def test_legacy_empty_string_stamp_reads_as_never_ran():
    """Artifacts written before the check was clocked carry "". They cannot be
    told apart from never-ran, so they must not read as a pass."""
    restored = ConformanceReport.from_dict({"generated_at": "", "rules_evaluated": 3})
    assert restored.generated_at is None
    assert not restored.ran


def test_report_carries_the_true_cycle_count_past_the_cap():
    import repowise.core.workspace.cycles as cy

    nodes = [_node(n) for n in "abcd"]
    # Every ordered pair, so there are far more elementary cycles than the cap.
    edges = [_edge(s, t) for s in "abcd" for t in "abcd" if s != t]
    g = _graph(nodes, edges)
    with patch.object(cy, "MAX_CYCLES", 2):
        report = build_conformance_report(g, [])
    assert len(report.cycles) == 2
    assert report.total_cycles > 2
    assert report.to_dict()["total_cycles"] == report.total_cycles
    assert report.total_capped is False


def _complete_graph(n: int):
    names = [f"s{i:02d}" for i in range(n)]
    return _graph(
        [_node(x) for x in names], [_edge(s, t) for s in names for t in names if s != t]
    )


def test_a_dense_tangle_stops_at_the_budget_and_lists_the_shortest_cycles():
    import repowise.core.workspace.cycles as cy

    # K6 has 409 elementary cycles; a budget of 100 stops the count.
    g = _complete_graph(6)
    with patch.object(cy, "MAX_ENUMERATED_CYCLES", 100):
        cycles, total, capped = detect_cycles(g)
        report = build_conformance_report(g, [])
    assert capped is True
    assert total == 100
    # The bounded pass lists the 15 two-service loops and then the triangles.
    assert [c.length for c in cycles] == sorted(c.length for c in cycles)
    assert cycles[0].length == 2
    assert sum(1 for c in cycles if c.length == 2) == 15
    assert len(cycles) == cy.MAX_CYCLES
    assert report.total_capped is True
    restored = type(report).from_dict(report.to_dict())
    assert restored.total_capped is True
    assert restored.total_cycles == 100


def test_cycles_under_the_budget_keep_the_exact_count():
    import repowise.core.workspace.cycles as cy

    g = _complete_graph(6)
    with patch.object(cy, "MAX_ENUMERATED_CYCLES", 409):
        _, total, capped = detect_cycles(g)
    assert (total, capped) == (409, False)


def test_cycle_total_says_at_least_when_capped():
    from repowise.core.workspace.cycles import cycle_total

    assert cycle_total(7, False) == "7"
    assert cycle_total(20000, True) == "at least 20000"


def test_a_capped_count_with_only_long_cycles_still_lists_one():
    import repowise.core.workspace.cycles as cy

    # Two 7-service rings through "a": no cycle within MAX_CYCLE_LENGTH.
    rings = [list("abcdefg"), ["a", *"hijklm"]]
    names = sorted({n for ring in rings for n in ring})
    edges = [_edge(r[i], r[(i + 1) % len(r)]) for r in rings for i in range(len(r))]
    g = _graph([_node(n) for n in names], edges)
    with patch.object(cy, "MAX_ENUMERATED_CYCLES", 1):
        cycles, total, capped = detect_cycles(g)
    assert (total, capped) == (1, True)
    assert [c.length for c in cycles] == [7]
