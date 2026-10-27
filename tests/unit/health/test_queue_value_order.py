"""Rank-order golden for the Fix first queue: value band, tier, then worth.

Modelled on a large agent repository where the band alone could not tell a
4,226-line conversation loop from a status-printing wizard: both are past
every size bar, so the old order fell back to per-finding health credit,
which saturates, and ranked the loop 25th. Each item below says why it sits
where it does.
"""

from __future__ import annotations

import math

import pytest

from repowise.core.analysis.health.fix_first import build_fix_first
from repowise.core.analysis.health.queue.value import CHURN_WEIGHT, churn_weight, history_weight
from tests.unit.health.fix_first_rows import AT, _perf

# Central and churning from 10 importers and 10 commits in 90 days.
_CUTS = (10.0, 10.0)


def _metric(path: str, dependents: int, commits: int) -> dict:
    return {"file_path": path, "score": 3.0, "nloc": 900, "is_test": False,
            "commit_count_90d": commits, "dependents": dependents,
            "analyzed_commit": "abc", "updated_at": AT}


def _finding(path: str, function: str, impact: float, **details) -> dict:
    return {"file_path": path, "biomarker_type": "complex_method", "severity": "high",
            "function_name": function, "line_start": 10, "line_end": 10 + details["nloc"],
            "health_impact": impact, "reason": f"{function} is complex",
            "public_id": f"finding_{function}", "dimension": "defect",
            "details": {**details, "deepest_block": {"start": 40, "end": 60}}}


METRICS = [
    _metric("agent/conversation_loop.py", dependents=32, commits=40),
    _metric("cli/status.py", dependents=7, commits=40),
    _metric("gateway/config.py", dependents=487, commits=40),
    _metric("gateway/sessions.py", dependents=3, commits=2),
    _metric("lib/parse.py", dependents=0, commits=0),
]

FINDINGS = [
    _finding("agent/conversation_loop.py", "run_conversation", 0.25,
             ccn=963, nloc=4226, max_nesting=9),
    # A wizard: long and branchy, but it prints; its health credit is the
    # highest of the large functions, which is what used to rank it first.
    _finding("cli/status.py", "show_status", 0.72, ccn=153, nloc=478, max_nesting=6),
    _finding("gateway/config.py", "load_gateway_config", 0.52, ccn=151, nloc=341, max_nesting=6),
    # Past the bars by less: CCN 45 over 120 lines, in a file nothing imports
    # and nobody changed. It leads only on churn or fix history, so it is out.
    _finding("lib/parse.py", "parse_block", 1.6, ccn=45, nloc=120, max_nesting=6),
]

PERFORMANCE = [
    # A request handler's growing database loop: the top band.
    _perf("perf_req", "gateway/db.py::fetch", file_path="gateway/sessions.py",
          intervention_symbol="gateway/sessions.py::list_sessions"),
    # The same loop with no role evidence: a step below a request's.
    _perf("perf_unknown", "gateway/db.py::save", file_path="gateway/sessions.py",
          intervention_symbol="gateway/sessions.py::sync_all", execution_role="unknown"),
]

#: (finding's function or cause's id, value band, why it sits here).
GOLDEN = [
    ("perf_req", "4", "request-run growing db loop: top band, and its plan is safe to start"),
    ("run_conversation", "4", "largest complexity removed (963) x reach of 32 importers"),
    ("load_gateway_config", "4", "CCN 151 x reach of 487 importers outweighs the wizard"),
    ("show_status", "4", "the wizard: band 4 on size, CCN 153 x reach of only 7 importers"),
    ("perf_unknown", "2", "the same loop with no role evidence: band 2, worth 103"),
]


def _queue():
    return build_fix_first(
        metrics=METRICS, findings=FINDINGS, performance=PERFORMANCE, hot_cuts=_CUTS,
        limit=None,
    )


def _key(item) -> str:
    return item.target.symbol if item.kind == "finding" else item.source.opportunity_id


def _value(item) -> str:
    return next(f.value for f in item.why_ranked if f.factor in ("value", "value within later"))


def test_rank_order_golden() -> None:
    items = _queue().items
    got = [(_key(i), _value(i)) for i in items]
    assert got == [(symbol, band) for symbol, band, _why in GOLDEN]


def test_a_moderate_function_with_no_history_is_counted_out() -> None:
    queue = _queue()
    assert "parse_block" not in {i.target.symbol for i in queue.items}
    assert queue.totals.excluded["moderate_quiet"] == 1
    churned = [{**m, "commit_count_90d": 40} if m["file_path"] == "lib/parse.py" else m
               for m in METRICS]
    again = build_fix_first(metrics=churned, findings=FINDINGS, performance=PERFORMANCE,
                            hot_cuts=_CUTS, limit=None)
    assert "parse_block" in {i.target.symbol for i in again.items}


def test_the_giant_outranks_the_wizard_on_complexity_removed_times_reach() -> None:
    by_symbol = {i.target.symbol: i for i in _queue().items}
    giant, wizard = by_symbol["run_conversation"], by_symbol["show_status"]
    ranked = {f.factor: f.value for f in giant.why_ranked}
    assert ranked["complexity removed"] == "963"
    assert ranked["files that import it"] == "32 (top fifth)"
    assert giant.rank < wizard.rank
    # The giant earns no more health credit: worth, not credit, orders the band.
    assert giant.gain.value <= wizard.gain.value


def test_history_orders_within_a_band_and_never_lifts_one() -> None:
    quiet = [{**m, "commit_count_90d": 0} for m in METRICS]
    base = {_key(i): _value(i) for i in _queue().items}
    calm = build_fix_first(metrics=quiet, findings=FINDINGS, performance=PERFORMANCE,
                           hot_cuts=_CUTS, limit=None)
    assert {_key(i): _value(i) for i in calm.items} == base
    giant = next(i for i in calm.items if i.target.symbol == "run_conversation")
    churn = {f.factor: f.value for f in giant.why_ranked}["changes often"]
    assert churn == "no"


# --- the function's own history -------------------------------------------------


def test_churn_weight_is_one_plus_log_of_the_changes() -> None:
    assert churn_weight(None) == churn_weight(0) == churn_weight(-3) == 1.0
    assert churn_weight(14) == pytest.approx(1 + math.log1p(14))
    assert churn_weight(60) > churn_weight(14) > churn_weight(1) > 1.0


def test_no_function_history_falls_back_to_the_file_weight() -> None:
    assert history_weight(None, churning=True) == CHURN_WEIGHT
    assert history_weight(None, churning=False) == 1.0
    # With function history the file's churn is not read.
    assert history_weight(0, churning=True) == 1.0
    assert history_weight(14, churning=False) == churn_weight(14)


def _twins(changes: dict[str, int] | None):
    """Two equal functions (same band, CCN, size and importers) in two files."""
    metrics = [_metric("a/one.py", 3, 40), _metric("b/two.py", 3, 40)]
    findings = [_finding(p, fn, 0.5, ccn=60, nloc=200, max_nesting=5)
                for p, fn in (("a/one.py", "first"), ("b/two.py", "second"))]
    return build_fix_first(metrics=metrics, findings=findings, hot_cuts=_CUTS, limit=None,
                           function_changes=changes).items


def test_a_churned_function_outranks_an_equal_quiet_one_in_its_band() -> None:
    for churned, quiet in (("first", "second"), ("second", "first")):
        path = "a/one.py" if churned == "first" else "b/two.py"
        items = _twins({f"{path}::{churned}": 14})
        assert [i.target.symbol for i in items] == [churned, quiet]
        assert items[0].value == items[1].value
        lead, other = ({f.factor: f.value for f in i.why_ranked} for i in items)
        assert lead["function changed in"] == "14 commits"
        assert other["function changed in"] == "0 commits"
        assert float(lead["worth in band"]) > float(other["worth in band"])
        assert "; function changed in 14 commits;" in items[0].why
        assert "function changed" not in items[1].why


def test_function_history_never_lifts_a_band() -> None:
    base = _queue().items
    # The wizard and the moderate parser changed far more than anything else.
    churned = [{**m, "commit_count_90d": 40} if m["file_path"] == "lib/parse.py" else m
               for m in METRICS]
    changes = {"cli/status.py::show_status": 500, "lib/parse.py::parse_block": 500}
    items = build_fix_first(metrics=churned, findings=FINDINGS, performance=PERFORMANCE,
                            hot_cuts=_CUTS, limit=None, function_changes=changes).items
    values = {_key(i): _value(i) for i in items}
    assert {k: values[k] for k in (_key(i) for i in base)} == {_key(i): _value(i) for i in base}
    ranks = [int(_value(i)) for i in items if i.tier != "later"]
    assert ranks == sorted(ranks, reverse=True)
    # Inside band 4 the churned wizard passes the quiet config loader, not the giant.
    findings = [_key(i) for i in items if i.kind == "finding" and _value(i) == "4"]
    assert findings == ["run_conversation", "show_status", "load_gateway_config"]


def test_without_function_history_the_file_weight_still_orders() -> None:
    # No blame rows at all (the essential git tier): the churning file's x1.25 stands.
    items = _twins(None)
    quiet = build_fix_first(
        metrics=[_metric("a/one.py", 3, 0), _metric("b/two.py", 3, 40)],
        findings=[_finding(p, fn, 0.5, ccn=60, nloc=200, max_nesting=5)
                  for p, fn in (("a/one.py", "first"), ("b/two.py", "second"))],
        hot_cuts=_CUTS, limit=None,
    ).items
    assert [i.target.symbol for i in quiet] == ["second", "first"]
    lead, other = ({f.factor: f.value for f in i.why_ranked} for i in quiet)
    assert (lead["changes often"], other["changes often"]) == ("yes", "no")
    assert float(lead["worth in band"]) == pytest.approx(
        float(other["worth in band"]) * CHURN_WEIGHT, abs=1)
    assert all("function changed" not in i.why for i in items)


def test_a_unit_led_by_no_function_keeps_the_file_weight() -> None:
    # A class-level finding in a repository with function history: no function
    # leads it, so its file's churn still orders it.
    finding = {**_finding("a/one.py", "Store", 0.5, ccn=60, nloc=200, max_nesting=5),
               "biomarker_type": "god_class"}
    items = build_fix_first(metrics=[_metric("a/one.py", 3, 40)], findings=[finding],
                            hot_cuts=_CUTS, limit=None,
                            function_changes={"a/one.py::Store": 30}).items
    facts = [{f.factor: f.value for f in i.why_ranked} for i in items]
    assert all(f.get("changes often") == "yes" and "function changed in" not in f for f in facts)


def test_a_file_blame_skipped_for_size_keeps_the_file_weight() -> None:
    # Function history elsewhere in the repository; "a/one.py" was too large to
    # blame, so its churning file weighs 1.25 and leads the equal quiet twin.
    metrics = [_metric("a/one.py", 3, 40), _metric("b/two.py", 3, 0)]
    findings = [_finding(p, fn, 0.5, ccn=60, nloc=200, max_nesting=5)
                for p, fn in (("a/one.py", "first"), ("b/two.py", "second"))]
    items = build_fix_first(metrics=metrics, findings=findings, hot_cuts=_CUTS, limit=None,
                            function_changes={"c/three.py::other": 9},
                            blame_skipped={"a/one.py"}).items
    assert [i.target.symbol for i in items] == ["first", "second"]
    lead, other = ({f.factor: f.value for f in i.why_ranked} for i in items)
    assert lead["changes often"] == "yes"
    # A missing row in a file that was blamed counts as unchanged.
    assert other["function changed in"] == "0 commits"
    assert float(lead["worth in band"]) == pytest.approx(
        float(other["worth in band"]) * CHURN_WEIGHT, abs=1)
