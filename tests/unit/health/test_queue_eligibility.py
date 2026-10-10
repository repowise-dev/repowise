"""The one queue's ladders and its count bookkeeping."""

from __future__ import annotations

from repowise.core.analysis.health.queue.eligibility import (
    ELIGIBLE,
    Tally,
    Verdict,
    perf_fix_verdict,
    refactor_verdict,
)


class _Facts:
    def unreachable(self, path, symbol, line):
        return False

    def shape(self, path, symbol):
        return {"ccn": 60, "nloc": 300}

    def cloned(self, path, shape):
        return False


def _cause(**over):
    row = {
        "execution_context": "production",
        "actionability_state": "plan_ready",
        "plan_state": "available",
        "fix_strategy": "batch_or_prefetch_io",
        "biomarker_type": "io_in_loop",
        "facets": {"loop_magnitude": "grows_with_data"},
    }
    return {**row, **over}


def test_the_tally_counts_once_and_names_dormant_functions_only_for_gated_off() -> None:
    tally = Tally(("test", "gated_off"))
    assert not tally.add(ELIGIBLE, ("a.py", "f"))
    tally.add(Verdict("gated_off"), ("a.py", "f"))
    tally.add(Verdict("unreachable"), ("b.py", "g"))
    tally.add(Verdict("unreachable"))
    assert tally.excluded == {"test": 0, "gated_off": 1, "unreachable": 2}
    assert tally.dormant == {("a.py", "f")}
    assert tally.total == 3
    assert tally.by_reason() == {"unreachable": 2, "gated_off": 1}


def test_by_reason_keeps_first_counted_order_on_ties() -> None:
    tally = Tally()
    for reason in ("small_function", "test", "test", "small_function", "low_value_kind"):
        tally.add(Verdict(reason))
    assert list(tally.by_reason()) == ["small_function", "test", "low_value_kind"]


def test_an_unaudited_kind_is_counted_as_such_after_the_worth_floor() -> None:
    def concrete(_step):
        return True

    for kind in ("move_method", "extract_class"):
        steps = [{"refactoring_type": kind, "target_symbol": "a.py::f"}]
        assert refactor_verdict(2.0, steps, _Facts(), "a.py", concrete).reason == "kind_unaudited"
        assert refactor_verdict(0.1, steps, _Facts(), "a.py", concrete).reason == "below_min_worth"
    steps = [{"refactoring_type": "large_method", "target_symbol": "a.py::f"}]
    assert refactor_verdict(2.0, steps, _Facts(), "a.py", concrete).reason == "low_value_kind"


def test_a_cause_group_needs_a_plan_and_is_dormant_only_when_every_row_is() -> None:
    contexts = frozenset({"production"})
    never = lambda: False  # noqa: E731
    unplanned = _cause(plan_state="none", fix_strategy=None)
    assert perf_fix_verdict([unplanned], contexts, never)[0].reason == "no_plan"
    gated = _cause(actionability_state="expected", actionability_reason="gated_off")
    assert perf_fix_verdict([gated], contexts, never)[0].reason == "gated_off"
    test_row = _cause(execution_context="test")
    assert perf_fix_verdict([gated, test_row], contexts, never)[0].reason == "test"
    verdict, worth = perf_fix_verdict([_cause()], contexts, never)
    assert verdict.eligible and len(worth) == 1


def test_a_cause_group_asks_about_dead_code_only_once_it_is_worth_an_item() -> None:
    asked: list[bool] = []

    def unreachable() -> bool:
        asked.append(True)
        return True

    bounded = _cause(biomarker_type="string_concat_in_loop", facets={"loop_magnitude": "bounded"})
    contexts = frozenset({"production"})
    assert perf_fix_verdict([bounded], contexts, unreachable)[0].reason == "below_min_worth"
    assert not asked
    assert perf_fix_verdict([_cause()], contexts, unreachable)[0].reason == "unreachable"
    assert asked == [True]
