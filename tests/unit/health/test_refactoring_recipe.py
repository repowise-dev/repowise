"""A stored plan read as a recipe: preconditions, ordered steps, postconditions.

The prompt wording is pinned by ``tests/unit/agent_prompts/test_golden.py``;
these pin the structure an agent or the CLI's JSON reads.
"""

from __future__ import annotations

import json
import pathlib

from repowise.core.analysis.health.refactoring.recipe import (
    PRECONDITION_KINDS,
    RECIPE_ACTIONS,
    build_recipe,
)

_CASES = json.loads(
    (
        pathlib.Path(__file__).resolve().parents[2] / "fixtures/agent_prompts/refactoring.json"
    ).read_text(encoding="utf-8")
)
_PLANS = {case["name"]: case["detail"] for case in _CASES["plans"]}


def test_extract_method_carries_the_texts_to_copy() -> None:
    recipe = build_recipe(_PLANS["extract_method_typed"])
    (step,) = recipe["steps"]
    assert step["action"] == "extract"
    assert step["span"] == {"start": 61, "end": 78}
    assert step["new_symbol"]["signature_text"].startswith("async def _read_config(self")
    assert step["call_site"]["new_text"] == "config = await self._read_config(path, strict)"
    # A placeholder in the texts is named, so the agent fills it.
    assert any("<type>" in note for note in step["new_symbol"]["notes"])
    assert recipe["does_not"][1].startswith("touch anything outside lines 61-78")


def test_preconditions_list_tests_risks_and_each_governing_decision_once() -> None:
    pre = build_recipe(_PLANS["extract_method_typed"])["preconditions"]
    assert [p["kind"] for p in pre] == ["tests", "decision", "active_edit", "decision"]
    assert [p.get("ref") for p in pre if p["kind"] == "decision"] == ["dec_1", "dec_2"]
    assert {p["kind"] for p in pre} <= set(PRECONDITION_KINDS)


def test_an_untested_plan_asks_for_a_characterization_test_first() -> None:
    recipe = build_recipe(_PLANS["performance_fix_steps"])
    assert recipe["preconditions"][0]["kind"] == "characterization"
    verify = recipe["postconditions"][0]
    assert verify["kind"] == "verify" and verify["tests"] == []


def test_a_site_listed_twice_is_one_step_and_keeps_its_own_verify() -> None:
    steps = build_recipe(_PLANS["performance_fix_steps"])["steps"]
    assert [s["n"] for s in steps] == [1, 2]
    assert steps[1]["verify"]["commands"] == ["pytest tests/test_rows.py"]
    assert all(s["applicability"] == "judgment" for s in steps)


def test_a_label_target_is_not_reported_as_a_symbol() -> None:
    recipe = build_recipe(_PLANS["split_file_older_row"])
    assert recipe["target"] == {"file": "pkg/big.py", "symbol": None, "span": None}
    assert [s["action"] for s in recipe["steps"]] == ["move", "move", "keep", "reexport"]
    # An older row with no validation still ends on a check.
    assert recipe["postconditions"][0]["kind"] == "verify"


def test_every_plan_type_yields_steps_in_the_shared_vocabulary() -> None:
    plans = {
        "extract_helper": {
            "occurrences": [
                {"file": "a.py", "line_start": 1, "line_end": 9},
                {"file": "b.py", "line_start": 3, "line_end": 11},
            ],
            "suggested_site": {"directory": "pkg"},
        },
        "extract_class": {"groups": [{"name": None, "methods": ["get"], "fields": ["x"]}]},
        "move_method": {"method": "m", "from_class": "A", "to_class": "B", "to_file": "b.py"},
        "break_cycle": {"cycle": ["a.py", "b.py"], "cut_edges": [{"from": "a.py", "to": "b.py"}]},
        "unknown_type": {},
    }
    for kind, plan in plans.items():
        recipe = build_recipe({"refactoring_type": kind, "file_path": "a.py", "plan": plan})
        assert recipe["steps"], kind
        assert {s["action"] for s in recipe["steps"]} <= set(RECIPE_ACTIONS), kind
    helper = build_recipe(
        {"refactoring_type": "extract_helper", "file_path": "a.py", "plan": plans["extract_helper"]}
    )
    assert [s["action"] for s in helper["steps"]] == [
        "add_helper",
        "replace_with_call",
        "replace_with_call",
    ]
