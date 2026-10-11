"""What Fix first leads with: print and prompt flows, moderate functions with no
history, and functions credited with a hub file's importers.

Each test builds the smallest rows that show the rule firing and the nearest
case where it must not.
"""

from __future__ import annotations

from repowise.core.analysis.health.complexity.terminal import is_terminal_call, terminal_share
from repowise.core.analysis.health.complexity.walker import walk_file
from repowise.core.analysis.health.fix_first import build_fix_first
from repowise.core.analysis.health.queue.value import HUB_SHARE, function_reach
from tests.unit.health.fix_first_rows import AT

# Neither churning nor central unless a test says so.
_CUTS = (10.0, 10.0)


def _metric(path: str, *, commits: int = 1, dependents: int = 0, nloc: int = 900) -> dict:
    return {"file_path": path, "score": 3.0, "nloc": nloc, "is_test": False,
            "commit_count_90d": commits, "dependents": dependents,
            "analyzed_commit": "abc", "updated_at": AT}


def _size(path: str, function: str, *, line: int = 10, **details) -> dict:
    return {"file_path": path, "biomarker_type": "complex_method", "severity": "high",
            "function_name": function, "line_start": line, "line_end": line + details["nloc"],
            "health_impact": 1.0, "reason": f"{function} is complex",
            "public_id": f"finding_{path}_{function}", "dimension": "defect",
            "details": {**details, "deepest_block": {"start": line + 5, "end": line + 9}}}


def _history(path: str, marker: str, function: str | None = None) -> dict:
    return {"file_path": path, "biomarker_type": marker, "severity": "medium",
            "function_name": function, "health_impact": 0.5, "reason": marker,
            "public_id": f"finding_{path}_{marker}_{function}", "dimension": "defect"}


def _queue(metrics: list[dict], findings: list[dict]):
    return build_fix_first(metrics=metrics, findings=findings, hot_cuts=_CUTS, limit=None)


def _symbols(queue) -> set[str]:
    return {i.target.symbol for i in queue.items}


# --- print and prompt flows ------------------------------------------------------


def test_terminal_calls_are_read_by_name_and_receiver() -> None:
    assert is_terminal_call("print", set())
    assert is_terminal_call("print_info", set())
    assert is_terminal_call("_prompt_checklist", set())
    assert is_terminal_call("log", {"console"})
    assert is_terminal_call("write", {"sys", "stdout"})
    assert not is_terminal_call("log", {"logger"})
    assert not is_terminal_call("write", {"handle"})
    assert not is_terminal_call("build_prompt", set())
    for name in ("printer_for", "printable", "confirmed_order", "promptly", "echo_server_handle"):
        assert not is_terminal_call(name, set()), name


def test_a_function_with_few_calls_has_no_terminal_share() -> None:
    assert terminal_share(2, 3) == 0.0
    assert terminal_share(2, 4) == 0.5


def test_the_walker_measures_a_status_command_and_not_a_resolver() -> None:
    status = (
        "def show_status(cfg):\n"
        + "".join(
            f"    if cfg.get('k{i}'):\n        print('k{i} on')\n    else:\n"
            f"        print('k{i} off')\n"
            for i in range(6)
        )
        + "    return load(cfg)\n"
    )
    resolver = "def resolve(cfg):\n    a = load(cfg)\n    b = merge(a, cfg)\n    return check(b)\n"
    fns = {f.name: f for f in walk_file("s.py", "python", (status + resolver).encode()).functions}
    # 12 prints of 19 calls (six `cfg.get` and one `load` are not terminal).
    assert fns["show_status"].terminal_share == 0.63
    assert fns["resolve"].terminal_share == 0.0


def test_a_print_flow_is_counted_out_and_a_logic_function_beside_it_stays() -> None:
    metrics = [_metric("cli/status.py", commits=40), _metric("core/run.py", commits=40)]
    findings = [
        _size("cli/status.py", "show_status", ccn=153, nloc=478, max_nesting=4,
              terminal_share=0.35),
        _size("core/run.py", "finalize", ccn=131, nloc=408, max_nesting=6, terminal_share=0.1),
    ]
    queue = _queue(metrics, findings)
    assert _symbols(queue) == {"finalize"}
    assert queue.totals.excluded["terminal_flow"] == 1


# --- moderate functions need history ----------------------------------------------


_MODERATE = {"ccn": 45, "nloc": 120, "max_nesting": 6}


def test_a_moderate_function_in_a_known_quiet_file_is_counted_out() -> None:
    queue = _queue([_metric("lib/parse.py")], [_size("lib/parse.py", "parse", **_MODERATE)])
    assert queue.items == ()
    assert queue.totals.excluded["moderate_quiet"] == 1


def test_a_moderate_function_with_unknown_history_still_leads() -> None:
    finding = _size("lib/parse.py", "parse", **_MODERATE)
    # No commit count: no git, or a file git has not seen.
    no_git = {**_metric("lib/parse.py"), "commit_count_90d": None}
    assert _symbols(_queue([no_git], [finding])) == {"parse"}
    # A shallow clone: every file changed once, so history tells files apart not at all.
    metrics = [_metric("lib/parse.py", commits=1), _metric("lib/other.py", commits=1)]
    shallow = build_fix_first(metrics=metrics, findings=[finding], limit=None)
    assert _symbols(shallow) == {"parse"}
    assert shallow.totals.excluded["moderate_quiet"] == 0


def test_churn_bug_fixes_or_a_hotspot_on_the_function_let_it_lead() -> None:
    finding = _size("lib/parse.py", "parse", **_MODERATE)
    churning = _queue([_metric("lib/parse.py", commits=40)], [finding])
    fixed = _queue([_metric("lib/parse.py")], [finding, _history("lib/parse.py", "prior_defect")])
    hot = _queue([_metric("lib/parse.py")],
                 [finding, _history("lib/parse.py", "function_hotspot", "parse")])
    assert all(_symbols(q) == {"parse"} for q in (churning, fixed, hot))


def test_a_hotspot_on_another_function_is_no_evidence() -> None:
    queue = _queue([_metric("lib/parse.py")],
                   [_size("lib/parse.py", "parse", **_MODERATE),
                    _history("lib/parse.py", "function_hotspot", "other")])
    assert queue.totals.excluded["moderate_quiet"] == 1


def test_a_large_function_leads_without_history() -> None:
    long_ = _size("lib/a.py", "long_one", ccn=45, nloc=230, max_nesting=6)
    branchy = _size("lib/b.py", "branchy", ccn=85, nloc=150, max_nesting=6)
    queue = _queue([_metric("lib/a.py"), _metric("lib/b.py")], [long_, branchy])
    assert _symbols(queue) == {"long_one", "branchy"}


# --- a hub file's importers -------------------------------------------------------


def test_function_reach_credits_a_small_share_of_a_hub_file() -> None:
    assert function_reach(287, 265, 21080) == 4
    assert function_reach(18, 423, 1405) == 18
    assert function_reach(None, 265, 21080) is None
    assert function_reach(80, 0, 9000) == 80
    assert function_reach(100, int(HUB_SHARE * 1000), 1000) == 100


def test_a_function_lost_in_a_hub_file_is_not_central() -> None:
    finding = _size("gateway/run.py", "send_progress", ccn=75, nloc=265, max_nesting=5)
    hub = _queue([_metric("gateway/run.py", commits=40, dependents=287, nloc=21080)], [finding])
    focused = _queue([_metric("gateway/run.py", commits=40, dependents=287, nloc=1000)], [finding])
    (lost,), (kept,) = hub.items, focused.items
    assert (lost.value, kept.value) == (2, 3)
    facts = {f.factor: f.value for f in lost.why_ranked}
    assert facts["files that import it"] == "287, 4 counted for its share of the file"
    assert {f.factor: f.value for f in kept.why_ranked}["files that import it"] == "287 (top fifth)"
    # What the file is, the item still says.
    assert "287" in lost.why


def test_size_markers_store_a_terminal_share_only_when_there_is_one() -> None:
    from repowise.core.analysis.health.biomarkers.base import shape_details

    src = b"def a():\n    print(1)\n    run()\n    go()\n    print(2)\n\ndef b():\n    run()\n"
    fns = {f.name: f for f in walk_file("x.py", "python", src).functions}
    assert shape_details(fns["a"]) == {"dispatch_share": 0.0, "terminal_share": 0.5}
    assert shape_details(fns["b"]) == {"dispatch_share": 0.0}
