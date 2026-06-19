"""Tests for architecture conformance — matchers, rule checking, cycles, IO.

The negative cases are load-bearing: a clean graph must produce no violations,
a behavioral co-change edge must never count as a dependency, and an ``allow``
exception must suppress an otherwise-denied edge.
"""

from __future__ import annotations

from pathlib import Path

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
    cycles = detect_cycles(g)
    assert len(cycles) == 1
    assert set(cycles[0].nodes) == {"a", "b"}
    assert len(cycles[0].edge_ids) == 2


def test_longer_cycle():
    g = _graph(
        [_node("a"), _node("b"), _node("c")],
        [_edge("a", "b"), _edge("b", "c"), _edge("c", "a")],
    )
    cycles = detect_cycles(g)
    assert len(cycles) == 1
    assert set(cycles[0].nodes) == {"a", "b", "c"}
    assert cycles[0].length == 3


def test_acyclic_graph_has_no_cycles():
    g = _graph(
        [_node("a"), _node("b"), _node("c")],
        [_edge("a", "b"), _edge("b", "c")],
    )
    assert detect_cycles(g) == []


def test_cycles_ignore_behavioral_edges():
    # a->b structural, b->a only via co-change → not a real cycle
    g = _graph(
        [_node("a"), _node("b")],
        [_edge("a", "b"), _edge("b", "a", kind="co_change", structural=False)],
    )
    assert detect_cycles(g) == []


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
