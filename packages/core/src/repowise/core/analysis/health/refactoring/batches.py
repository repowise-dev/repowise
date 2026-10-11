"""A long staged Extract Method plan, cut into batches an agent tests between.

An agent applying a dozen stages in one go has nothing to tell it which stage
broke the code, and long runs invite rewriting the file wholesale. A staged
plan over :data:`BATCH_MAX_STAGES` stages is therefore served as ordered
batches of at most that many: each names the tests to run before the next,
and a batch whose tests fail stops the plan there.

Every batch end leaves the file runnable by construction. A stage replaces its
lines with one call binding its outputs in the function, so a later stage
reads them as it read the original lines; the stage planner refuses a plan in
which some read would see another value (``dataflow.stages``, Composition).
The parameter object is built before stage 1, from values the stages never
rebind, so it goes in batch 1. A function-local import is dropped only with
the last stage that reads it (``extract_method``), never while a stage in a
later batch still needs it.

Derived from the recipe's steps at serve time: nothing is stored, and plan
ids do not change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .recommendations import _commands

#: The most stages one batch holds.
BATCH_MAX_STAGES = 5
#: Tests a batch names: the most direct of the plan's.
_BATCH_TESTS = 3


def batch_tests(detail: Mapping[str, Any]) -> list[str]:
    """The tests to run after a batch, most direct first.

    Every stage lifts lines out of the one target function, so the tests that
    reach that function guard each batch. Read from the plan's own guarding
    tests (file-level today); a function-level test map replaces this body.
    """
    validation = detail.get("validation")
    tests = validation.get("tests") if isinstance(validation, Mapping) else None
    return [str(t) for t in tests or [] if t][:_BATCH_TESTS]


def _sizes(count: int) -> list[int]:
    """*count* stages in as few batches as the cap allows, sizes within one."""
    batches = -(-count // BATCH_MAX_STAGES)
    small, extra = divmod(count, batches)
    return [small + (1 if k < extra else 0) for k in range(batches)]


def _text(n: int, total: int, first: int, last: int, tests: list[str]) -> str:
    stages = f"stage {first}" if first == last else f"stages {first}-{last}"
    check = (
        "run the tests named here"
        if tests
        else "run the test you added before the edit (no existing test reaches this function)"
    )
    stop = (
        f"do not start batch {n + 1}"
        if n < total
        else "the plan is not done"
    )
    return (
        f"Batch {n} of {total}: apply {stages}, then {check}. "
        f"If any fails, stop here and report it; {stop}."
    )


def stage_batches(
    detail: Mapping[str, Any], steps: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]] | None:
    """*steps* (a staged plan's numbered recipe steps) as ordered batches, or
    None when the plan is not staged or has at most :data:`BATCH_MAX_STAGES`
    stages. Each batch is ``{n, steps, stages: {first, last}, tests, commands,
    text}``; ``steps`` are recipe step numbers, the steps before stage 1 (the
    parameter object) in batch 1."""
    if detail.get("refactoring_type") != "extract_method":
        return None
    numbers = [s["n"] for s in steps if s.get("action") == "extract"]
    if len(numbers) <= BATCH_MAX_STAGES:
        return None
    lead = [s["n"] for s in steps if s["n"] < numbers[0]]
    tests = batch_tests(detail)
    commands = _commands(tests)
    sizes = _sizes(len(numbers))
    out: list[dict[str, Any]] = []
    first = 1
    for n, size in enumerate(sizes, 1):
        last = first + size - 1
        out.append(
            {
                "n": n,
                "steps": (lead if n == 1 else []) + numbers[first - 1 : last],
                "stages": {"first": first, "last": last},
                "tests": tests,
                "commands": commands,
                "text": _text(n, len(sizes), first, last, tests),
            }
        )
        first = last + 1
    return out


__all__ = ["BATCH_MAX_STAGES", "batch_tests", "stage_batches"]
