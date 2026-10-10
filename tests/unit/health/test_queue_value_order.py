"""Rank-order golden for the Fix first queue: value band, tier, then worth.

Modelled on a large agent repository where the band alone could not tell a
4,226-line conversation loop from a status-printing wizard: both are past
every size bar, so the old order fell back to per-finding health credit,
which saturates, and ranked the loop 25th. Each item below says why it sits
where it does.
"""

from __future__ import annotations

from repowise.core.analysis.health.fix_first import build_fix_first
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
    # Past the bars by less: CCN 45 over 120 lines, in a file nothing imports.
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
    ("run_conversation", "4", "largest complexity removed (963) x reach of 32 importers"),
    ("load_gateway_config", "4", "CCN 151 x reach of 487 importers outweighs the wizard"),
    ("show_status", "4", "the wizard: band 4 on size, CCN 153 x reach of only 7 importers"),
    ("perf_req", "4", "request-run growing db loop: top band, worth a CCN 40 x 3 call sites"),
    ("perf_unknown", "2", "the same loop with no role evidence: band 2, worth 103"),
    ("parse_block", "2", "band 2 on size and gain; worth 45 (no importers) is below 103"),
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


def test_the_giant_outranks_the_wizard_on_complexity_removed_times_reach() -> None:
    by_symbol = {i.target.symbol: i for i in _queue().items}
    giant, wizard = by_symbol["run_conversation"], by_symbol["show_status"]
    ranked = {f.factor: f.value for f in giant.why_ranked}
    assert ranked["complexity removed"] == "963"
    assert ranked["files that import it"] == "32 (top fifth)"
    assert giant.rank < wizard.rank
    # The wizard still earns more health credit: worth, not credit, orders the band.
    assert giant.gain.value < wizard.gain.value


def test_history_orders_within_a_band_and_never_lifts_one() -> None:
    quiet = [{**m, "commit_count_90d": 0} for m in METRICS]
    base = {_key(i): _value(i) for i in _queue().items}
    calm = build_fix_first(metrics=quiet, findings=FINDINGS, performance=PERFORMANCE,
                           hot_cuts=_CUTS, limit=None)
    assert {_key(i): _value(i) for i in calm.items} == base
    churn = {f.factor: f.value for f in calm.items[0].why_ranked}["changes often"]
    assert churn == "no"
