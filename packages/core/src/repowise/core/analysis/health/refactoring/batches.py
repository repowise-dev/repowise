"""A staged Extract Method plan over five stages, as ordered batches to test between.

Each batch names the tests to run before the next, and a failing batch stops the
plan. Every batch end runs: a stage's call binds its outputs, the stage planner
refuses plans whose reads would change (``dataflow.stages``), the parameter object
is built in batch 1, and a local import leaves with its last reader. Derived at
serve time: nothing is stored and plan ids do not change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .commands import commands_for_tests

#: The most stages one batch holds.
BATCH_MAX_STAGES = 5
#: Tests a batch lists, a size choice; the command widens to their files when more reach.
_BATCH_TESTS = 3


def _validation(detail: Mapping[str, Any]) -> Mapping[str, Any]:
    validation = detail.get("validation")
    return validation if isinstance(validation, Mapping) else {}


def _batch_tests(detail: Mapping[str, Any]) -> tuple[list[str], int]:
    """The tests to run after a batch, most direct first, and how many reach.

    Every stage lifts lines out of the one target function, so the tests that
    reach it guard each batch. Read from the plan's own guarding tests
    (file-level today); a function-level test map replaces this body.
    """
    validation = _validation(detail)
    tests = [str(t) for t in validation.get("tests") or [] if t]
    total = validation.get("total")
    return tests[:_BATCH_TESTS], max(total if isinstance(total, int) else 0, len(tests))


def _sizes(count: int) -> list[int]:
    """*count* stages in as few batches as the cap allows, sizes within one."""
    batches = -(-count // BATCH_MAX_STAGES)
    small, extra = divmod(count, batches)
    return [small + (1 if k < extra else 0) for k in range(batches)]


def _check(tests: list[str], detail: Mapping[str, Any]) -> str:
    if tests:
        return "run the tests named here"
    if _validation(detail).get("prerequisite"):
        return "run the test you added before the edit (no existing test reaches this function)"
    return "run the tests under Verify"


def _text(n: int, total: int, check: str) -> str:
    """The batch's closing rule; its number and stages are fields of their own."""
    stop = f"do not start batch {n + 1}" if n < total else "the plan is not done"
    return f"With these stages in, {check}. If any fails, stop here and report it; {stop}."


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
    tests, total = _batch_tests(detail)
    # Each JS/TS test runs from the package the plan stored for it.
    packages = _validation(detail).get("js_packages")
    commands = commands_for_tests(
        tests, total=total, packages=packages if isinstance(packages, Mapping) else None
    )
    check = _check(tests, detail)
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
                "text": _text(n, len(sizes), check),
            }
        )
        first = last + 1
    return out


__all__ = ["BATCH_MAX_STAGES", "stage_batches"]
