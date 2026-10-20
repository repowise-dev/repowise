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


def _method(cls: str, name: str, *, path: str, sig: str, start: int = 10, end: int = 20):
    """A method, whose id carries its class: ``<path>::<Class>::<method>``."""
    return {
        "id": f"{path}::{cls}::{name}",
        "name": name,
        "kind": "method",
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
    assert symbol_index(rows)["app/a.py"]["app/a.py::run"].signature == "run(x)"
    assert symbol_index(live)["app/a.py"]["app/a.py::run"].signature == "run(x)"


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
    assert set(symbol_index(rows)["app/a.py"]) == {"app/a.py::run"}


def test_symbol_index_keeps_every_same_named_method():
    # Keying by bare name would keep only the last `login`, so a change to the
    # first one could never be seen.
    rows = [
        _file(
            "service.py",
            [
                _method("AuthService", "login", path="service.py", sig="def login(user, pw)"),
                _method("SSOService", "login", path="service.py", sig="def login(token)"),
            ],
        )
    ]
    indexed = symbol_index(rows)["service.py"]
    assert set(indexed) == {"service.py::AuthService::login", "service.py::SSOService::login"}
    assert indexed["service.py::AuthService::login"].signature == "def login(user, pw)"
    assert indexed["service.py::SSOService::login"].signature == "def login(token)"


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


def test_signature_change_reports_the_same_named_method_it_belongs_to():
    # Two classes, both with `login`. Only AuthService's changes, so only that
    # id may be reported -- and against its own callers.
    auth = _method("AuthService", "login", path="service.py", sig="def login(user, pw)")
    sso = _method(
        "SSOService", "login", path="service.py", sig="def login(token)", start=30, end=40
    )
    edited = _method("AuthService", "login", path="service.py", sig="def login(user, pw, mfa)")
    base = [_file("service.py", [auth, sso])]
    head = [_file("service.py", [edited, sso])]

    impact = _compute(
        base,
        head,
        changed=("service.py",),
        calls=[("app/b.py::main", "service.py::AuthService::login")],
    )

    assert [c.change for c in impact.changes] == ["signature"]
    change = impact.changes[0]
    assert change.symbol_id == "service.py::AuthService::login"
    assert change.name == "login"
    assert change.outside_callers == ["app/b.py::main"]
    assert [c.symbol_id for c in impact.breaking] == ["service.py::AuthService::login"]


def test_removing_one_same_named_method_leaves_the_other_alone():
    # Deleting SSOService.login must not diff AuthService.login against it: the
    # surviving method is unchanged, so nothing at all is reported for it.
    auth = _method("AuthService", "login", path="service.py", sig="def login(user, pw)")
    sso = _method(
        "SSOService", "login", path="service.py", sig="def login(token)", start=30, end=40
    )
    base = [_file("service.py", [auth, sso])]
    head = [_file("service.py", [auth])]

    impact = _compute(
        base,
        head,
        changed=("service.py",),
        calls=[("app/b.py::main", "service.py::SSOService::login")],
    )

    assert [(c.symbol_id, c.change) for c in impact.changes] == [
        ("service.py::SSOService::login", "removed")
    ]
    assert impact.changes[0].name == "login"
    assert [c.symbol_id for c in impact.breaking] == ["service.py::SSOService::login"]


def test_a_method_moved_between_classes_reads_removed_and_added():
    # Its callers point at the old id, so a move is a removal plus an addition
    # rather than an edit to whichever method now answers to the bare name.
    base = [
        _file(
            "service.py",
            [_method("AuthService", "login", path="service.py", sig="def login(user, pw)")],
        )
    ]
    head = [
        _file(
            "service.py",
            [_method("SSOService", "login", path="service.py", sig="def login(user, pw)")],
        )
    ]

    impact = _compute(base, head, changed=("service.py",))

    assert [(c.symbol_id, c.change) for c in impact.changes] == [
        ("service.py::AuthService::login", "removed"),
        ("service.py::SSOService::login", "added"),
    ]


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
# What a signature change does, and who witnesses it
# ---------------------------------------------------------------------------


def _signature_change(before: str, after: str, calls=(("app/b.py::main", "app/a.py::run"),)):
    base = [_file("app/a.py", [_sym("run", path="app/a.py", sig=before)])]
    head = [_file("app/a.py", [_sym("run", path="app/a.py", sig=after)])]
    return _compute(base, head, calls=list(calls)).changes[0]


def test_a_reflowed_signature_is_not_a_signature_change():
    # Same contract, different text: it falls through to the body check, so
    # it is reported only when the change edited inside the symbol.
    base = [_file("app/a.py", [_sym("run", path="app/a.py", sig="def run(x, y)")])]
    head = [_file("app/a.py", [_sym("run", path="app/a.py", sig="def run(\n  x,\n  y,\n)")])]
    calls = [("app/b.py::main", "app/a.py::run")]
    assert _compute(base, head, calls=calls).changes == []
    touched = _compute(base, head, calls=calls, ranges={"app/a.py": [(12, 14)]})
    assert [c.change for c in touched.changes] == ["body"]
    assert touched.changes[0].signature_effect is None


def test_the_file_language_decides_whether_a_retype_breaks():
    base = [_file("web/a.ts", [_sym("run", path="web/a.ts", sig="function run(x: string)")])]
    head = [_file("web/a.ts", [_sym("run", path="web/a.ts", sig="function run(x: number)")])]
    calls = [("web/b.ts::main", "web/a.ts::run")]
    change = _compute(base, head, calls=calls, changed=("web/a.ts",)).changes[0]
    assert change.signature_effect == "breaking"
    assert change.is_breaking
    # The same edit in Python is an annotation no caller is checked against.
    assert not _signature_change("def run(x: str)", "def run(x: int)").is_breaking


def test_an_appended_optional_argument_is_compatible_and_never_breaks():
    change = _signature_change("def run(x)", "def run(x, y=None)")
    assert change.signature_effect == "compatible"
    assert change.signature_reason == "added optional `y`"
    assert not change.is_breaking


def test_a_removed_parameter_is_breaking_and_says_which():
    change = _signature_change("def run(x, y)", "def run(x)")
    assert change.signature_effect == "breaking"
    assert change.signature_reason == "removed the required `y`"
    assert change.is_breaking


def test_an_unparseable_signature_is_unknown_and_stays_breaking():
    change = _signature_change("def run(x)", "def run(x, y: Dict[str, int)")
    assert change.signature_effect == "unknown"
    assert change.is_breaking


def test_only_signature_changes_carry_an_effect():
    base = [_file("app/a.py", [_sym("gone", path="app/a.py", sig="gone()")])]
    head = [_file("app/a.py", [_sym("kept", path="app/a.py", sig="kept()")])]
    impact = _compute(base, head, calls=[("app/b.py::main", "app/a.py::gone")])
    assert all(c.signature_effect is None and c.signature_reason is None for c in impact.changes)


def test_a_break_witnessed_only_by_tests_is_not_breaking():
    calls = [("tests/test_a.py::test_run", "app/a.py::run")]
    change = _signature_change("def run(x, y)", "def run(x)", calls=calls)
    assert change.signature_effect == "breaking"
    assert change.outside_test_callers == ["tests/test_a.py::test_run"]
    assert change.outside_production_callers == []
    assert not change.is_breaking


def test_a_removal_witnessed_only_by_tests_is_not_breaking():
    base = [_file("app/a.py", [_sym("gone", path="app/a.py", sig="gone()")])]
    calls = [("tests/test_a.py::t", "app/a.py::gone")]
    impact = _compute(base, [_file("app/a.py", [])], calls=calls)
    assert impact.changes[0].change == "removed"
    assert impact.breaking == []


def test_a_production_module_named_like_a_test_is_still_production():
    # ``src/pkg/test_impact.py`` is named for what it does, not a test of it.
    caller = "packages/core/src/repowise/core/analysis/test_impact.py::build"
    change = _signature_change("def run(x, y)", "def run(x)", calls=[(caller, "app/a.py::run")])
    assert change.outside_production_callers == [caller]
    assert change.is_breaking


def test_ranking_reads_production_reach_not_test_reach():
    def syms(sig_suffix: str):
        return [
            _file(
                "app/a.py",
                [
                    _sym("tested", path="app/a.py", sig=f"def tested({sig_suffix})"),
                    _sym("used", path="app/a.py", sig=f"def used({sig_suffix})"),
                ],
            )
        ]

    calls = [(f"tests/test_{i}.py::t", "app/a.py::tested") for i in range(5)]
    calls += [("app/b.py::main", "app/a.py::tested")]
    calls += [(f"app/c{i}.py::main", "app/a.py::used") for i in range(2)]
    impact = _compute(syms("x"), syms(""), calls=calls)
    assert [c.name for c in impact.changes] == ["used", "tested"]


def test_production_callers_come_first_so_a_cap_keeps_them():
    base = [_file("app/a.py", [_sym("run", path="app/a.py", sig="def run(x, y)")])]
    head = [_file("app/a.py", [_sym("run", path="app/a.py", sig="def run(x)")])]
    calls = [(f"tests/test_{i}.py::t", "app/a.py::run") for i in range(3)]
    calls.append(("app/z.py::main", "app/a.py::run"))
    change = _compute(base, head, calls=calls, callers_per_symbol=2).changes[0]
    assert change.outside_callers == ["app/z.py::main", "tests/test_0.py::t"]
    assert change.outside_production_callers == ["app/z.py::main"]
    assert change.outside_callers_total == 4
    assert change.is_breaking


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
