"""``gated_off``: a function a constant-false flag in its own file switches off,
and ``unreachable``: work a sure dead-code finding covers.

Both keep a unit out of Fix first, the refactoring default scope (which reads
Fix first's reasons) and the performance default queue, and both are counted.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.engine import _mark_function_facts
from repowise.core.analysis.health.finding_identity import finding_public_id
from repowise.core.analysis.health.fix_first import build_fix_first
from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import build_performance_opportunities
from repowise.core.analysis.health.queue.eligibility import perf_queue_counts, perf_queue_verdict
from tests.unit.health.fix_first_rows import FINDINGS, METRICS, REFACTORING, _perf

# The shape of ``decisions/evolution.py``: a documented kill switch, then a
# function that builds its result and returns it at once behind the switch.
_EVOLUTION = """
SEMANTIC_SUPERSESSION_ENABLED = False
LIVE = False
FROM_ENV = os.environ.get("REPOWISE_FLAG") == "1"
LATER = False
LATER = True
NOTHING = None


def configure(on):
    global LIVE
    LIVE = on


def detect_supersessions_and_conflicts(session, touched_ids):
    \"\"\"Record supersedes/conflicts edges.\"\"\"
    summary = {"supersedes": 0, "conflicts": 0, "flipped": 0}
    if not SEMANTIC_SUPERSESSION_ENABLED:
        return summary
    for decision in touched_ids:
        session.get(decision)
    return summary


def wholly_inside():
    if SEMANTIC_SUPERSESSION_ENABLED:
        work()


def is_false_guard():
    if SEMANTIC_SUPERSESSION_ENABLED is False:
        return None
    work()


def live_global():
    if not LIVE:
        return
    work()


def live_env():
    if not FROM_ENV:
        return
    work()


def reassigned_at_module_level():
    if not LATER:
        return
    work()


def none_is_not_false():
    if NOTHING is False:
        return
    work()


def guard_with_else():
    if not SEMANTIC_SUPERSESSION_ENABLED:
        return
    else:
        work()


def logs_before_returning():
    if not SEMANTIC_SUPERSESSION_ENABLED:
        log()
        return
    work()


def guard_after_a_loop():
    for x in xs:
        work(x)
    if not SEMANTIC_SUPERSESSION_ENABLED:
        return
"""

_TS = """
export const ENABLED = false;
let MUTABLE = false;
const ON = true;

export function guarded(rows: string[]) {
  const out = {};
  if (!ENABLED) return out;
  return rows;
}

const wholly = () => {
  if (ENABLED) {
    work();
  }
};

class Store {
  strict() {
    if (ENABLED === false) {
      return 1;
    }
    work();
  }
  onFlag() {
    if (!ON) return;
    work();
  }
}

function mutable() {
  if (!MUTABLE) {
    return;
  }
  work();
}
"""


def _gated(language: str, path: str, source: str) -> set[str]:
    fcx = walk_file(path, language, source.encode())
    if not fcx.functions:
        pytest.skip(f"{language} grammar unavailable")
    return {fc.name for fc in fcx.functions if fc.gated_off}


def test_a_constant_false_guard_in_the_same_file_gates_the_function() -> None:
    assert _gated("python", "/tmp/evolution.py", _EVOLUTION) == {
        "detect_supersessions_and_conflicts",
        "wholly_inside",
        "is_false_guard",
    }


def test_a_live_flag_is_never_gated() -> None:
    gated = _gated("python", "/tmp/evolution.py", _EVOLUTION)
    for name in ("live_global", "live_env", "reassigned_at_module_level", "configure"):
        assert name not in gated


def test_typescript_const_false_gates_and_let_or_true_does_not() -> None:
    assert _gated("typescript", "/tmp/store.ts", _TS) == {"guarded", "wholly", "strict"}


def test_the_dogfood_function_is_gated() -> None:
    import pathlib

    import repowise.core.analysis.decisions.evolution as evolution

    source = pathlib.Path(evolution.__file__).read_text(encoding="utf-8")
    assert "detect_supersessions_and_conflicts" in _gated("python", evolution.__file__, source)


def _finding(name: str = "run", line: int = 10, **details) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="complex_method",
        severity=Severity.HIGH,
        file_path="src/a.py",
        function_name=name,
        line_start=line,
        line_end=line + 20,
        details={"ccn": 20, **details},
        health_impact=1.0,
    )


def test_findings_in_a_gated_function_carry_the_fact_and_keep_their_id() -> None:
    fcx = walk_file("/tmp/evolution.py", "python", _EVOLUTION.encode())
    if not fcx.functions:
        pytest.skip("python grammar unavailable")
    gated = next(fc for fc in fcx.functions if fc.name == "detect_supersessions_and_conflicts")
    inside, outside = _finding(gated.name, gated.start_line + 1), _finding("live_env", 1)
    before = finding_public_id(inside)
    _mark_function_facts([inside, outside], fcx.functions)
    assert inside.details["gated_off"] is True and "gated_off" not in outside.details
    assert finding_public_id(inside) == before


# --- Fix first ---------------------------------------------------------------------


def _fix_finding(**details) -> dict:
    complex_run = FINDINGS[1]
    return {**complex_run, "details": {**complex_run["details"], **details}}


def test_a_gated_function_is_no_item_and_counts_as_dormant() -> None:
    queue = build_fix_first(metrics=METRICS[:1], findings=[_fix_finding(gated_off=True)])
    assert queue.items == ()
    assert queue.totals.excluded["gated_off"] == 1 and queue.totals.dormant == 1
    assert len(build_fix_first(metrics=METRICS[:1], findings=[_fix_finding()]).items) == 1


def test_a_plan_on_a_gated_function_leaves_the_refactoring_default_scope() -> None:
    queue = build_fix_first(
        metrics=METRICS[:1], findings=[_fix_finding(gated_off=True)], refactoring=REFACTORING[:1]
    )
    assert queue.refactoring_reasons["refop2_core"] == "gated_off"
    # The plan and the finding it leaves behind sit in one function.
    assert queue.totals.excluded["gated_off"] == 2 and queue.totals.dormant == 1


def _perf_finding(line: int, **details) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="io_in_loop",
        severity=Severity.MEDIUM,
        file_path="src/repo.py",
        function_name="load_all",
        line_start=line,
        line_end=line,
        details={"boundary_kind": "db", "cross_function": False, "path": [], **details},
        health_impact=0.0,
        dimension="performance",
    )


def test_a_cause_in_a_gated_function_leaves_the_performance_default_queue() -> None:
    (gated,) = build_performance_opportunities(
        [_perf_finding(10, gated_off=True), _perf_finding(12, gated_off=True)]
    )
    (live,) = build_performance_opportunities([_perf_finding(10)])
    assert (gated.actionability_state, gated.actionability_reason, gated.fix) == (
        "expected",
        "gated_off",
        None,
    )
    assert live.actionability_state != "expected"
    assert perf_queue_verdict(gated).reason == "gated_off"
    assert perf_queue_counts([gated, live])["excluded"]["gated_off"] == 1


def test_one_live_call_site_keeps_the_cause() -> None:
    (mixed,) = build_performance_opportunities(
        [_perf_finding(10, gated_off=True), _perf_finding(12)]
    )
    assert mixed.actionability_reason != "gated_off"


def test_fix_first_counts_a_gated_cause_by_its_reason() -> None:
    row = _perf(
        "perf3_gated", "x", actionability_state="expected", plan_state="no_safe_plan",
        fix_strategy=None,
    )
    row["details"] = {"actionability_reason": "gated_off"}
    expected = {**row, "opportunity_id": "perf3_exp", "intervention_symbol": "src/repo.py::other",
                "details": {"actionability_reason": "inherent_to_boundary"}}
    queue = build_fix_first(metrics=[METRICS[3]], performance=[row, expected])
    assert queue.items == ()
    assert queue.totals.excluded["gated_off"] == 1 and queue.totals.excluded["expected"] == 1
    assert queue.totals.dormant == 1


# --- dead code -----------------------------------------------------------------------


def _dead(**over) -> dict:
    return {
        "kind": "unused_internal", "file_path": "src/core.py", "symbol_name": "run",
        "start_line": 10, "end_line": 60, "confidence": 0.9, "safe_to_delete": False,
        "status": "open", **over,
    }


@pytest.mark.parametrize(
    "dead",
    [
        _dead(),
        _dead(kind="unreachable_file", symbol_name=None, start_line=None, end_line=None),
        _dead(confidence=0.5, safe_to_delete=True),
        _dead(start_line=None, end_line=None),
    ],
)
def test_sure_dead_code_makes_its_target_unreachable(dead: dict) -> None:
    queue = build_fix_first(
        metrics=METRICS[:1], findings=[_fix_finding()], refactoring=REFACTORING[:1],
        dead_code=[dead],
    )
    assert queue.items == ()
    assert queue.refactoring_reasons["refop2_core"] == "unreachable"
    assert queue.totals.excluded["unreachable"] == 2 and queue.totals.dormant == 0


@pytest.mark.parametrize(
    "dead",
    [
        _dead(confidence=0.5),
        _dead(status="false_positive"),
        _dead(start_line=100, end_line=120),
        _dead(file_path="src/other.py"),
    ],
)
def test_unsure_or_elsewhere_dead_code_leaves_the_target(dead: dict) -> None:
    queue = build_fix_first(metrics=METRICS[:1], findings=[_fix_finding()], dead_code=[dead])
    assert len(queue.items) == 1 and queue.totals.excluded["unreachable"] == 0


def test_a_cause_in_dead_code_is_unreachable() -> None:
    dead = _dead(file_path="src/repo.py", symbol_name="load_all", start_line=None, end_line=None)
    queue = build_fix_first(metrics=[METRICS[3]], performance=[_perf("perf3_a", "s")],
                            dead_code=[dead])
    assert queue.items == () and queue.totals.excluded["unreachable"] == 1


def test_only_a_plain_assignment_may_come_before_the_guard() -> None:
    source = _EVOLUTION + """

def works_before_the_guard(session):
    session.execute("delete from t")
    if not SEMANTIC_SUPERSESSION_ENABLED:
        return


def calls_in_an_assignment(session):
    rows = session.execute("select 1")
    if not SEMANTIC_SUPERSESSION_ENABLED:
        return rows
"""
    gated = _gated("python", "/tmp/evolution.py", source)
    assert not gated & {"works_before_the_guard", "calls_in_an_assignment"}
    assert "detect_supersessions_and_conflicts" in gated


def test_a_minified_file_is_read_as_written() -> None:
    source = "const ENABLED = false;\nfunction f() { if (!ENABLED) return; work(); }\n"
    assert _gated("javascript", "/tmp/a.js", source) == {"f"}
    assert _gated("javascript", "/tmp/a.js", source + "var x = 1;" * 300 + "\n") == set()


def test_dead_code_on_one_method_leaves_its_same_named_sibling() -> None:
    # ``Old.run`` (lines 10-20) is dead; ``New.run`` (line 40) is live.
    dead = [_dead(symbol_name="run", start_line=10, end_line=20)]
    live = {**_fix_finding(), "function_name": "New.run", "line_start": 40, "line_end": 70}
    queue = build_fix_first(metrics=METRICS[:1], findings=[live], dead_code=dead)
    assert len(queue.items) == 1 and queue.totals.excluded["unreachable"] == 0
    named = [_dead(symbol_name="Old.run", start_line=None, end_line=None)]
    queue = build_fix_first(metrics=METRICS[:1], findings=[live], dead_code=named)
    assert len(queue.items) == 1


def test_a_perf_cause_is_placed_by_its_function_line() -> None:
    dead = [_dead(file_path="src/repo.py", symbol_name="run", start_line=10, end_line=20)]
    rows = [_perf("perf3_new", "s", intervention_symbol="src/repo.py::New.run")]
    lines = {"src/repo.py::New.run": 40}
    queue = build_fix_first(metrics=[METRICS[3]], performance=rows, dead_code=dead,
                            symbol_lines=lines)
    assert len(queue.items) == 1
    queue = build_fix_first(metrics=[METRICS[3]], performance=rows, dead_code=dead,
                            symbol_lines={"src/repo.py::New.run": 12})
    assert queue.items == () and queue.totals.excluded["unreachable"] == 1


def test_one_live_row_keeps_a_perf_group_out_of_the_dormant_count() -> None:
    gated = _perf("perf3_a", "x", actionability_state="expected", plan_state="no_safe_plan",
                  fix_strategy=None, details={"actionability_reason": "gated_off"})
    other = _perf("perf3_b", "y", actionability_state="investigate", plan_state="no_safe_plan",
                  fix_strategy=None, rank_position=1)
    queue = build_fix_first(metrics=[METRICS[3]], performance=[gated, other])
    assert queue.totals.excluded["no_strategy"] == 1 and queue.totals.dormant == 0
