"""The quoted estimate and the live counter must price a model the same way.

The pre-run estimate ("$1.19 - $1.98, median $1.59") comes from
``cost_estimator/pricing.py``, in USD per **1K** tokens. The live counter that
ticks up during the run comes from ``generation/cost_tracker.py``, in USD per
**1M** tokens. Two hand-maintained tables in different units, feeding two
numbers a user compares directly, is a 1000x error waiting for someone to add a
row to one of them.

The tables agree today. This is the guard that says so, because the failure
mode is silent: an estimate that is wrong by three orders of magnitude still
renders as a plausible dollar figure, and it is shown *before* the user commits
to spending.

Only models present in both tables are compared. Each deliberately carries rows
the other does not — the estimator prices what an index plan can select, the
tracker prices what a session transcript can report — and requiring identical
membership would be a different, unwanted constraint.
"""

from __future__ import annotations

import pytest

from repowise.core.cost_estimator.pricing import _COST_TABLE_EXACT, _lookup_cost
from repowise.core.generation.cost_tracker import _PRICING, get_model_pricing

_SHARED_MODELS = sorted(set(_COST_TABLE_EXACT) & set(_PRICING))

# Models where the two tables already disagree, found by this test on the run
# that introduced it. Both are Gemini "preview" rows that ``cost_tracker``
# still prices at the gemini-1.5-flash rate ($0.075/$0.30 per MTok) it sits
# directly beneath in that file, while the estimator carries per-row rates with
# explicit per-MTok comments. ``gemini-3.5-flash-lite`` agrees in both tables,
# which is what makes these two look like copied placeholders rather than a
# deliberate split.
#
# Not fixed here: the wrong half is ``core/generation/cost_tracker.py``, and
# this branch does not own that file. ``strict=True`` on purpose — whoever
# corrects the rate gets a failing test telling them to delete this entry,
# rather than a silently-passing xfail that outlives the bug.
_KNOWN_DRIFT = {
    "gemini-3-flash-preview": "cost_tracker prices it at the 1.5-flash rate",
    "gemini-3.1-flash-lite-preview": "cost_tracker prices it at the 1.5-flash rate",
}


def _param(model: str):
    if model in _KNOWN_DRIFT:
        return pytest.param(model, marks=pytest.mark.xfail(strict=True, reason=_KNOWN_DRIFT[model]))
    return pytest.param(model)


def test_the_two_tables_actually_overlap() -> None:
    """Otherwise the parametrised test below would vacuously pass on zero cases."""
    assert len(_SHARED_MODELS) >= 5, _SHARED_MODELS


@pytest.mark.parametrize("model", [_param(m) for m in _SHARED_MODELS])
def test_estimate_and_live_counter_price_a_model_identically(model: str) -> None:
    """Per-1K x 1000 must equal per-MTok, for both directions of the bill."""
    est_input, est_output = _lookup_cost(model)
    live = get_model_pricing(model)

    assert est_input * 1000 == pytest.approx(live["input"]), (
        f"{model}: estimate says ${est_input * 1000:.4f}/MTok input, "
        f"live counter says ${live['input']:.4f}/MTok"
    )
    assert est_output * 1000 == pytest.approx(live["output"]), (
        f"{model}: estimate says ${est_output * 1000:.4f}/MTok output, "
        f"live counter says ${live['output']:.4f}/MTok"
    )


def test_the_default_model_is_priced_by_both() -> None:
    """The one model almost every run actually uses.

    A default that only one table knows is the case where the mismatch reaches
    the most users, so it is asserted by name rather than left to the overlap.
    """
    import inspect

    from repowise.core.providers.llm.openai import OpenAIProvider

    # Read from the constructor signature rather than repeating the name, so
    # bumping the default (as #1594 did) fails here instead of silently
    # pricing the new default off the fallback tier.
    default = inspect.signature(OpenAIProvider.__init__).parameters["model"].default
    assert isinstance(default, str) and default

    assert default in _COST_TABLE_EXACT, f"{default} missing from the estimator table"
    assert default in _PRICING, f"{default} missing from the live cost table"
