"""A staged plan over five stages renders as batches, each closed by its tests."""

from __future__ import annotations

import re

import pytest

from repowise.core.agent_prompts import FLAVORS, render_opportunity, render_plan
from repowise.core.agent_prompts.preamble import preamble
from repowise.core.agent_prompts.refactoring import render_plan_spec
from repowise.core.analysis.health.refactoring.recipe import build_recipe

#: Agent tool names no prompt says (harnesses name their tools differently).
#: Read / Edit / Write are also English verbs the shared preamble uses, so the
#: whole-text scan takes the unambiguous names and the batch lines take all.
_TOOL_NAMES = re.compile(r"\b(Grep|Glob|TodoWrite|Bash|str_replace)\b|@codebase")
_VERB_TOOLS = re.compile(r"\b(Read|Edit|Write)\b")


def _no_tool_names(text: str) -> None:
    # The flavour opening is shared by every refactoring prompt and owns its own wording.
    for flavor in FLAVORS:
        for producer in ("refactoring_plan", "refactoring_opportunity"):
            text = text.replace(preamble(flavor, producer), "")
    assert not _TOOL_NAMES.search(text)
    batch_lines = [ln for ln in text.splitlines() if "Batch " in ln or "stages in" in ln]
    assert batch_lines and not any(_VERB_TOOLS.search(ln) for ln in batch_lines)


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


def _stage_order(text: str, stages: int) -> list[int]:
    starts = [text.index(f"Stage {k} of {stages}:") for k in range(1, stages + 1)]
    assert starts == sorted(starts)
    return starts


@pytest.mark.parametrize("flavor", FLAVORS)
def test_a_plan_prompt_groups_its_steps_by_batch(flavor):
    text = render_plan(_detail(7), flavor)
    assert "## Steps, in order, in batches" in text
    first, second = text.index("**Batch 1 of 2: stages 1-4**"), text.index("**Batch 2 of 2: stages 5-7**")
    starts = _stage_order(text, 7)
    assert first < starts[0] and starts[3] < second < starts[4]
    # Batch 1's check closes it, before batch 2 starts.
    assert starts[3] < text.index("do not start batch 2") < second
    assert "Run: `pytest tests/test_cli.py::test_main tests/test_mod.py::test_run`" in text
    assert text.count("If any fails, stop here") == 2
    # The header names the batch and its stages once; the check does not repeat them.
    assert text.count("Batch 1 of 2") == 1 and "stages 1-4" not in text[first + 30:second]
    _no_tool_names(text)


def test_a_short_plan_keeps_one_step_list():
    text = render_plan(_detail(5))
    assert "## Steps, in order\n" in text and "Batch" not in text


def test_a_spec_keeps_the_batches_but_runs_nothing():
    text = render_plan_spec(_detail(6))
    assert "**Batch 1 of 2: stages 1-3**" in text and "**Batch 2 of 2: stages 4-6**" in text
    _stage_order(text, 6)
    assert "Run: " not in text and "Tests: " not in text and "If any fails" not in text
    _no_tool_names(text)


def test_an_opportunity_keeps_the_batches_inside_its_step():
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
    first, second = text.index("   - **Batch 1 of 2: stages 1-3**"), text.index("   - **Batch 2 of 2: stages 4-6**")
    starts = _stage_order(text, 6)
    assert first < starts[0] and starts[2] < text.index("do not start batch 2") < second < starts[3]
    assert "   - With these stages in, run the tests named here." in text
    assert text.count("If any fails, stop here") == 2
    # No blank line between the step's head and its last batch line: the batches stay in the item.
    step = text[text.index("1. **Extract Method**"):text.index("the plan is not done")]
    assert "\n\n" not in step
    _no_tool_names(text)
