"""The Do next sentence: worded once in core and carried in the actions view."""

from __future__ import annotations

from datetime import datetime

from repowise.core.analysis.actions import RepoFacts, compose_actions
from repowise.core.analysis.actions.summary import summarize

ANCHOR = datetime(2026, 9, 28, 12, 0)


def _h(**by_tier: int) -> dict:
    return {"actions": [], "total": sum(by_tier.values()), "hidden": 0, "by_tier": by_tier}


def test_counts_work_and_names_the_week_by_its_last_commit() -> None:
    out = summarize({"week": _h(act_now=1, plan=2, improve_signal=4), "quarter": _h()}, ANCHOR)
    assert out["week"] == "3 things worth doing in the week to Sep 28, the last indexed commit, 1 of them now."


def test_a_clear_week_points_at_the_quarter() -> None:
    out = summarize({"week": _h(improve_signal=1), "quarter": _h(plan=1)}, ANCHOR)
    assert out["week"] == (
        "Nothing needs you in the week to Sep 28, the last indexed commit. "
        "1 thing is worth planning this quarter."
    )
    assert out["quarter"] == "1 thing worth doing this quarter."


def test_a_clear_quarter_points_at_the_week_and_both_clear_says_so() -> None:
    out = summarize({"week": _h(plan=2), "quarter": _h()}, None)
    assert out["quarter"] == "Nothing needs you this quarter. 2 things came up this week."
    assert summarize({"week": _h(), "quarter": _h()}, None) == {
        "week": "Nothing stands out this week.",
        "quarter": "Nothing stands out this quarter.",
    }


def test_the_view_carries_one_sentence_per_horizon() -> None:
    view = compose_actions(RepoFacts(anchor=ANCHOR), now=ANCHOR)
    assert view["summary"] == summarize(view["horizons"], ANCHOR)
    assert set(view["summary"]) == set(view["horizons"])
