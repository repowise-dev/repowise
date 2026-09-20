"""Symbol-level contract impact: classification and caller partitioning.

Fixtures are the two parse shapes the analyzer accepts -- ``parsed_files.json``
rows (dicts) and live ``ParsedFile``-shaped objects -- because the whole point
of :func:`symbol_index` is that it takes both.
"""

from __future__ import annotations

from types import SimpleNamespace

from repowise.core.analysis.change_contracts import (
    analyze_contract_impact,
    caller_index,
    symbol_index,
    unavailable_contract_impact,
)


def _sym(name: str, *, path: str, sig: str, start: int = 10, end: int = 20, kind: str = "function"):
    return {
        "id": f"{path}::{name}",
        "name": name,
        "kind": kind,
        "signature": sig,
        "start_line": start,
        "end_line": end,
    }


def _file(path: str, symbols: list[dict]) -> dict:
    return {"file_info": {"path": path}, "symbols": symbols}


def _live_file(path: str, symbols: list[dict]):
    """The object shape a live re-parse returns."""
    return SimpleNamespace(
        file_info=SimpleNamespace(path=path),
        symbols=[SimpleNamespace(**s) for s in symbols],
    )


def _graph(*calls: tuple[str, str]) -> dict:
    return {
        "nodes": [],
        "links": [{"source": src, "target": tgt, "edge_type": "calls"} for src, tgt in calls],
    }


def _compute(base, head, *, calls=(), changed=("app/a.py",), ranges=None, **kwargs):
    return analyze_contract_impact(
        graph=_graph(*calls),
        base_parsed=base,
        head_parsed=head,
        changed_set=set(changed),
        added_ranges_by_file=ranges or {},
        **kwargs,
    )


# ---------------------------------------------------------------------------
# symbol_index / caller_index
# ---------------------------------------------------------------------------


def test_symbol_index_accepts_both_artifact_and_live_shapes():
    rows = [_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x)")])]
    live = [_live_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x)")])]
    assert symbol_index(rows)["app/a.py"]["run"].signature == "run(x)"
    assert symbol_index(live)["app/a.py"]["run"].signature == "run(x)"


def test_symbol_index_drops_uninteresting_kinds():
    rows = [
        _file(
            "app/a.py",
            [
                _sym("run", path="app/a.py", sig="run()"),
                _sym("MAX", path="app/a.py", sig="MAX = 3", kind="constant"),
            ],
        )
    ]
    assert set(symbol_index(rows)["app/a.py"]) == {"run"}


def test_caller_index_reads_calls_edges_only():
    graph = {
        "links": [
            {"source": "app/b.py::caller", "target": "app/a.py::run", "edge_type": "calls"},
            {"source": "app/b.py", "target": "app/a.py", "edge_type": "imports"},
        ]
    }
    assert caller_index(graph) == {"app/a.py::run": ["app/b.py::caller"]}


def test_caller_index_tolerates_pre_edge_type_snapshots():
    # An indexer old enough to omit edge_type: symbol-to-symbol edges are still
    # read (degrade to fewer callers, never to none), file edges are not.
    graph = {
        "links": [
            {"source": "app/b.py::caller", "target": "app/a.py::run"},
            {"source": "app/b.py", "target": "app/a.py"},
        ]
    }
    assert caller_index(graph) == {"app/a.py::run": ["app/b.py::caller"]}


# ---------------------------------------------------------------------------
# analyze_contract_impact
# ---------------------------------------------------------------------------


def test_signature_change_with_outside_caller_is_breaking():
    base = [_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x)")])]
    head = [_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x, y)")])]
    impact = _compute(base, head, calls=[("app/b.py::main", "app/a.py::run")])

    assert impact.status == "available"
    assert len(impact.breaking) == 1
    change = impact.breaking[0]
    assert change.change == "signature"
    assert change.outside_callers == ["app/b.py::main"]
    assert change.inside_caller_count == 0
    assert change.callers_total == 1
    assert change.outside_callers_total == 1


def test_callers_inside_the_change_are_not_the_finding():
    # The caller is in a file this change also touches, so it is already under
    # review -- a signature change reaching only those is not a finding.
    base = [_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x)")])]
    head = [_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x, y)")])]
    impact = _compute(
        base,
        head,
        calls=[("app/b.py::main", "app/a.py::run")],
        changed=("app/a.py", "app/b.py"),
    )
    assert impact.breaking == []
    assert impact.changes[0].inside_caller_count == 1
    assert impact.changes[0].callers_total == 1


def test_removed_symbol_with_outside_caller_is_breaking():
    base = [_file("app/a.py", [_sym("gone", path="app/a.py", sig="gone()")])]
    head = [_file("app/a.py", [_sym("kept", path="app/a.py", sig="kept()")])]
    impact = _compute(base, head, calls=[("app/b.py::main", "app/a.py::gone")])
    kinds = {c.name: c.change for c in impact.changes}
    assert kinds["gone"] == "removed"
    assert kinds["kept"] == "added"
    assert [c.name for c in impact.breaking] == ["gone"]


def test_body_change_needs_an_added_line_and_never_breaks():
    # Same signature. Without an overlapping added-line range the symbol is not
    # reported at all: the base side may be an out-of-date snapshot, and
    # everything that drifted since indexing would otherwise be blamed here.
    base = [_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x)")])]
    head = [_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x)")])]
    calls = [("app/b.py::main", "app/a.py::run")]

    assert _compute(base, head, calls=calls).changes == []

    touched = _compute(base, head, calls=calls, ranges={"app/a.py": [(12, 14)]})
    assert [c.change for c in touched.changes] == ["body"]
    # A body edit keeps the caller's contract, so it is never breaking.
    assert touched.breaking == []


def test_new_file_does_not_report_every_symbol_as_added():
    head = [_file("app/new.py", [_sym("run", path="app/new.py", sig="run()")])]
    impact = _compute([], head, changed=("app/new.py",))
    assert impact.changes == []


def test_breaking_changes_rank_first_and_by_reach():
    base = [
        _file(
            "app/a.py",
            [
                _sym("wide", path="app/a.py", sig="wide(x)"),
                _sym("narrow", path="app/a.py", sig="narrow(x)"),
            ],
        )
    ]
    head = [
        _file(
            "app/a.py",
            [
                _sym("wide", path="app/a.py", sig="wide(x, y)"),
                _sym("narrow", path="app/a.py", sig="narrow(x, y)"),
            ],
        )
    ]
    impact = _compute(
        base,
        head,
        calls=[
            ("app/b.py::one", "app/a.py::wide"),
            ("app/c.py::two", "app/a.py::wide"),
            ("app/d.py::three", "app/a.py::narrow"),
        ],
    )
    assert [c.name for c in impact.changes] == ["wide", "narrow"]


# ---------------------------------------------------------------------------
# Core owns the untruncated population; caps are surface policy
# ---------------------------------------------------------------------------


def test_core_returns_every_outside_caller_by_default():
    base = [_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x)")])]
    head = [_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x, y)")])]
    calls = [(f"app/c{i}.py::main", "app/a.py::run") for i in range(50)]
    change = _compute(base, head, calls=calls).changes[0]
    assert len(change.outside_callers) == 50
    assert change.outside_callers_total == 50
    assert change.callers_total == 50


def test_a_surface_cap_never_changes_the_reported_totals():
    base = [_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x)")])]
    head = [_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x, y)")])]
    calls = [(f"app/c{i}.py::main", "app/a.py::run") for i in range(50)]
    change = _compute(base, head, calls=calls, callers_per_symbol=40).changes[0]
    assert len(change.outside_callers) == 40
    assert change.outside_callers_total == 50
    assert change.callers_total == 50
    # A capped list is still the top of the same sorted population.
    assert change.outside_callers == sorted(f"app/c{i}.py::main" for i in range(50))[:40]


def test_ranking_uses_the_uncapped_reach_not_the_capped_list():
    # Both symbols cap to the same rendered length; only the totals separate
    # them, so ranking must read the total.
    base = [
        _file(
            "app/a.py",
            [
                _sym("wide", path="app/a.py", sig="wide(x)"),
                _sym("narrow", path="app/a.py", sig="narrow(x)"),
            ],
        )
    ]
    head = [
        _file(
            "app/a.py",
            [
                _sym("wide", path="app/a.py", sig="wide(x, y)"),
                _sym("narrow", path="app/a.py", sig="narrow(x, y)"),
            ],
        )
    ]
    calls = [(f"app/w{i}.py::c", "app/a.py::wide") for i in range(6)]
    calls += [(f"app/n{i}.py::c", "app/a.py::narrow") for i in range(3)]
    impact = _compute(base, head, calls=calls, callers_per_symbol=2)
    assert [c.name for c in impact.changes] == ["wide", "narrow"]


# ---------------------------------------------------------------------------
# Explicit evidence states
# ---------------------------------------------------------------------------


def test_known_empty_is_available_not_unavailable():
    base = [_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x)")])]
    impact = _compute(base, base)
    assert impact.is_empty()
    assert impact.status == "available"
    assert impact.reason is None


def test_unavailable_carries_its_reason():
    impact = unavailable_contract_impact("base_parse_not_supplied")
    assert impact.status == "unavailable"
    assert impact.reason == "base_parse_not_supplied"
    assert impact.is_empty()
    assert impact.breaking == []


def test_snapshot_base_is_carried_on_the_result():
    base = [_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x)")])]
    head = [_file("app/a.py", [_sym("run", path="app/a.py", sig="run(x, y)")])]
    assert _compute(base, head).base_is_snapshot is False
    assert _compute(base, head, base_is_snapshot=True).base_is_snapshot is True
