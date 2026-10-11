"""A staged plan over five stages renders as batches, each closed by its tests."""

from __future__ import annotations

import re

import pytest

from repowise.core.agent_prompts import FLAVORS, render_opportunity, render_plan
from repowise.core.agent_prompts.refactoring import render_plan_spec
from repowise.core.analysis.health.refactoring.recipe import build_recipe

_TOOL_NAMES = re.compile(r"\b(Read|Grep|Glob|Edit|Write|TodoWrite|Bash|str_replace)\b")


def _detail(stages: int) -> dict:
    return {
        "id": "rp_batched",
        "refactoring_type": "extract_method",
        "file_path": "pkg/mod.py",
        "target_symbol": "pkg/mod.py::run",
        "line_start": 1,
        "line_end": 200,
        "plan": {
            "stages": [
                {
                    "span": {"start": 20 * k, "end": 20 * k + 9},
                    "params": ["rows"],
                    "returns": [],
                    "suggested_name": f"_stage_{k}",
                    "anchors": {"first": f"    if rows_{k}:", "last": f"        done_{k}()"},
                }
                for k in range(1, stages + 1)
            ],
            "orchestrator": {"ccn_before": 90, "ccn_after": 20},
        },
        "validation": {
            "tests": ["tests/test_mod.py::test_run", "tests/test_cli.py::test_main"],
            "total": 2,
            "commands": ["pytest tests/test_cli.py::test_main tests/test_mod.py::test_run"],
            "basis": "inferred",
        },
    }


def test_the_recipe_carries_batches_only_over_five_stages():
    assert len(build_recipe(_detail(7))["batches"]) == 2
    assert "batches" not in build_recipe(_detail(5))


@pytest.mark.parametrize("flavor", FLAVORS)
def test_a_plan_prompt_groups_its_steps_by_batch(flavor):
    text = render_plan(_detail(7), flavor)
    assert "## Steps, in order, in batches" in text
    first, second = text.index("**Batch 1 of 2: stages 1-4**"), text.index("**Batch 2 of 2: stages 5-7**")
    # Every stage once, in order, with batch 1's check before batch 2 starts.
    starts = [text.index(f"Stage {k} of 7:") for k in range(1, 8)]
    assert starts == sorted(starts) and first < starts[0] and starts[3] < second < starts[4]
    check = text.index("do not start batch 2")
    assert starts[3] < check < second
    assert "Run: `pytest tests/test_cli.py::test_main tests/test_mod.py::test_run`" in text
    assert text.count("If any fails, stop here") == 2
    steps = text[text.index("## Steps"):text.index("## Verify")]
    assert not _TOOL_NAMES.search(steps)


def test_a_short_plan_keeps_one_step_list():
    text = render_plan(_detail(5))
    assert "## Steps, in order\n" in text and "Batch" not in text


def test_the_spec_and_an_opportunity_carry_the_batches():
    assert "do not start batch 2" in render_plan_spec(_detail(6))
    opportunity = {
        "opportunity_id": "ro_1",
        "file_path": "pkg/mod.py",
        "lead_refactoring_type": "extract_method",
        "steps": [
            {"plan_id": "rp_batched", "refactoring_type": "extract_method", "file_path": "pkg/mod.py",
             "line_start": 1, "line_end": 200, "target_symbol": "run", "applicability": {}}
        ],
        "plans": [_detail(6)],
    }
    text = render_opportunity(opportunity)
    assert "   **Batch 1 of 2: stages 1-3**" in text and "   **Batch 2 of 2: stages 4-6**" in text
