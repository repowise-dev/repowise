"""The "Fix and verify" prompt and the timing lines the Verify blocks share."""

from __future__ import annotations

import re

import pytest

from repowise.core.agent_prompts import FLAVORS, render_fix_and_verify, render_verify_lines
from repowise.core.agent_prompts.fix_and_verify import measure_lines
from repowise.core.agent_prompts.preamble import ROLE
from repowise.core.agent_prompts.refactoring import _verify_lines
from repowise.core.perf_measure import verify_measure

_BENCH = verify_measure(
    {"workload": "pytest bench", "class": "benchmark", "command": "pytest --benchmark-only", "cwd": ""},
    "load_rows",
)
_NESTED = verify_measure(
    {"workload": "api: go bench ./work", "class": "benchmark",
     "command": "go test '-run=^$' -bench . -benchtime=10x -benchmem -count=1 ./work", "cwd": "api"},
    "load_rows",
)
_CHECK = {
    "kind": "verify",
    "text": "The guarding tests pass after the edit, unchanged.",
    "tests": ["tests/test_rows.py"],
    "tests_total": 1,
    "commands": ["pytest tests/test_rows.py"],
    "measure": _BENCH,
}


@pytest.mark.parametrize("flavor", FLAVORS)
def test_the_prompt_measures_changes_tests_and_measures_again_in_order(flavor: str) -> None:
    prompt = render_fix_and_verify(_CHECK, flavor)

    assert prompt.startswith(ROLE[flavor] + "\n\n")
    order = [_BENCH["before"], "Make the change", "pytest tests/test_rows.py", _BENCH["after"], "verdict"]
    positions = [prompt.index(text) for text in order]
    assert positions == sorted(positions)


def test_the_prompt_never_lets_an_inconclusive_run_read_as_faster() -> None:
    prompt = render_fix_and_verify(_CHECK)

    assert "improved, regressed or inconclusive" in prompt
    assert "interval" in prompt
    assert "faster only when the verdict is improved" in prompt
    assert "5%" in prompt
    assert not re.search(r"\bhot\b", prompt, re.I)


def test_without_plan_tests_the_prompt_still_asks_for_a_test_run() -> None:
    prompt = render_fix_and_verify({**_CHECK, "commands": []})
    assert "Run the tests that exercise the change" in prompt


def test_a_nested_unit_is_measured_from_its_directory() -> None:
    assert "from `api`" in render_fix_and_verify({**_CHECK, "measure": _NESTED})
    assert "from the repository root" in render_fix_and_verify(_CHECK)


def test_the_plan_verify_block_adds_the_timing_after_the_tests() -> None:
    lines = _verify_lines(_CHECK)
    plain = _verify_lines({k: v for k, v in _CHECK.items() if k != "measure"})

    assert lines == [*plain, "", *measure_lines(_BENCH)]
    # A reader that cannot run anything gets no commands, timing included.
    assert _verify_lines(_CHECK, runnable=False) == _verify_lines(
        {k: v for k, v in _CHECK.items() if k != "measure"}, runnable=False
    )


def test_a_plan_without_tests_still_gets_its_timing() -> None:
    check = {"kind": "verify", "text": "No guarding test was found.", "tests": [], "measure": _BENCH}
    assert _verify_lines(check) == ["No guarding test was found.", "", *measure_lines(_BENCH)]


def test_the_fix_first_verify_block_adds_the_timing() -> None:
    verify = {"tests": [], "tests_total": 0, "command": None, "basis": "unknown", "prerequisite": "Pin it."}

    assert render_verify_lines({"verify": verify}) == "Pin it."
    assert render_verify_lines({"verify": {**verify, "measure": _BENCH}}) == "\n".join(
        ["Pin it.", "", *measure_lines(_BENCH)]
    )
