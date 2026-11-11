"""A staged Extract Method plan over five stages, served as batches to test between."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from repowise.core.analysis.health.refactoring.batches import (
    BATCH_MAX_STAGES,
    stage_batches,
)
from repowise.core.analysis.health.refactoring.models import RefactoringSuggestion
from repowise.core.analysis.health.refactoring.recipe import build_recipe, recipe_steps
from repowise.core.analysis.health.refactoring.recommendations import build_validation_plan

from .test_extract_method_stages import _persist, _plans

#: Agent tool names a plan never says: harnesses name their tools differently.
_TOOL_NAMES = re.compile(
    r"\b(Read|Grep|Glob|Edit|Write|TodoWrite|Bash|str_replace)\b|@codebase"
)


def _staged_detail(
    stages: int, *, obj: bool = False, tests: list[str] | None = None, **validation
) -> dict:
    plan = {
        "stages": [
            {"span": {"start": 10 * k, "end": 10 * k + 5}, "params": [], "returns": []}
            for k in range(1, stages + 1)
        ],
        "orchestrator": {"ccn_before": 80, "ccn_after": 20},
    }
    if obj:
        plan["parameter_object"] = {"name": "_Ctx", "var": "ctx", "construct_before": 10}
    return {
        "refactoring_type": "extract_method",
        "file_path": "pkg/mod.py",
        "target_symbol": "pkg/mod.py::run",
        "plan": plan,
        "validation": {"tests": tests or [], "commands": [], **validation},
    }


def _batches(detail: dict) -> list[dict] | None:
    return stage_batches(detail, recipe_steps(detail))


@pytest.mark.parametrize(
    ("stages", "sizes"),
    [(6, [3, 3]), (10, [5, 5]), (11, [4, 4, 3]), (21, [5, 4, 4, 4, 4])],
)
def test_batches_hold_at_most_five_stages_in_order(stages, sizes):
    batches = _batches(_staged_detail(stages))
    got = [b["stages"]["last"] - b["stages"]["first"] + 1 for b in batches]
    assert got == sizes and max(got) <= BATCH_MAX_STAGES
    assert [b["n"] for b in batches] == list(range(1, len(sizes) + 1))
    # Every stage once, in source order.
    assert [n for b in batches for n in b["steps"]] == list(range(1, stages + 1))


def test_five_stages_or_fewer_and_other_kinds_are_not_batched():
    assert _batches(_staged_detail(5)) is None
    assert _batches({**_staged_detail(8), "refactoring_type": "split_file"}) is None
    assert _batches({"refactoring_type": "extract_method", "plan": {"span": {"start": 1, "end": 9}}}) is None


def test_the_parameter_object_is_built_in_the_first_batch():
    batches = _batches(_staged_detail(7, obj=True))
    # Step 1 adds the object, steps 2-8 are the stages.
    assert batches[0]["steps"] == [1, 2, 3, 4, 5]
    assert batches[1]["steps"] == [6, 7, 8]
    assert batches[1]["stages"] == {"first": 5, "last": 7}


def test_each_batch_names_its_tests_and_stops_the_plan_on_failure():
    tests = ["tests/test_a.py::test_one", "tests/test_b.py", "tests/test_c.py", "tests/test_d.py"]
    batches = _batches(_staged_detail(7, tests=tests))
    for batch in batches:
        assert batch["tests"] == tests[:3]
        # A capped list widens to its files, as the plan's own command does.
        assert batch["commands"] == ["pytest tests/test_a.py tests/test_b.py tests/test_c.py"]
        assert "If any fails, stop here" in batch["text"]
        assert not _TOOL_NAMES.search(batch["text"])
    assert "do not start batch 2" in batches[0]["text"]
    assert "the plan is not done" in batches[-1]["text"]


def test_an_uncapped_list_runs_exactly_its_tests():
    tests = ["tests/test_a.py::test_one", "tests/test_b.py::test_two"]
    batch = _batches(_staged_detail(6, tests=tests, total=2))[0]
    assert batch["commands"] == ["pytest tests/test_a.py::test_one tests/test_b.py::test_two"]


def _no_reach_detail() -> dict:
    """A staged plan no test reaches, with the validation the ranker builds for it."""
    detail = _staged_detail(6)
    suggestion = RefactoringSuggestion(
        refactoring_type="extract_method",
        file_path=detail["file_path"],
        target_symbol="run",
        line_start=1,
        line_end=80,
        plan=detail["plan"],
        evidence={},
        impact_delta=1.0,
        effort_bucket="M",
        blast_radius={},
        confidence="medium",
        source_biomarker="complex_method",
    )
    detail["validation"] = build_validation_plan(suggestion, {}, {}).as_dict()
    return detail


def test_a_batch_with_no_reaching_test_runs_the_characterization_test():
    detail = _no_reach_detail()
    recipe = build_recipe(detail)
    # The batch's wording leans on this precondition being in the recipe.
    assert [p for p in recipe["preconditions"] if p["kind"] == "characterization"]
    batch = stage_batches(detail, recipe["steps"])[0]
    assert batch["tests"] == [] and batch["commands"] == []
    assert "the test you added before the edit" in batch["text"]


def test_unlisted_reaching_tests_point_at_verify():
    batch = _batches(_staged_detail(6, total=12))[0]
    assert batch["tests"] == [] and "run the tests under Verify" in batch["text"]


def test_the_typescript_batch_shape_matches_the_engine():
    types = Path(__file__).resolve().parents[3] / "packages" / "types" / "src" / "refactoring.ts"
    match = re.search(r"interface RecipeBatch \{(.*?)\n\}", types.read_text(encoding="utf-8"), re.S)
    assert match, "RecipeBatch is not declared in refactoring.ts"
    fields = set(re.findall(r"^  (\w+)\??:", match.group(1), re.M))
    batch = _batches(_staged_detail(6))[0]
    assert fields == set(batch)
    assert set(batch["stages"]) == {"first", "last"}
    assert re.search(r"^  batches\?: RecipeBatch\[\];", types.read_text(encoding="utf-8"), re.M)


def test_a_shared_local_import_is_dropped_only_with_its_last_reader():
    """Removing it with stage 1 would break every later stage still in the
    function when the first batch's tests run."""
    src = _persist(sections=14).replace(
        "from store import other_step_1, step_1", "from store import other_step_1, step_1, timed"
    )
    plan = _plans(src)["persist"].plan
    stages = plan["stages"]
    assert len(stages) > BATCH_MAX_STAGES
    readers = [
        k for k, s in enumerate(stages) if any("timed" in n and n.startswith("Import ") for n in s["new_symbol"].get("notes", []))
    ]
    removers = [
        k for k, s in enumerate(stages) if any("timed" in n and n.startswith("Remove ") for n in s["new_symbol"].get("notes", []))
    ]
    assert len(readers) == len(stages) and removers == [readers[-1]]
    detail = {"refactoring_type": "extract_method", "plan": plan, "validation": {}}
    batches = _batches(detail)
    assert len(batches) == 2 and batches[-1]["stages"]["last"] == len(stages)
