"""The command that runs a plan's tests, shared by plan validation and staged-plan batches."""

from __future__ import annotations


def commands_for_tests(tests: list[str], *, total: int | None = None) -> list[str]:
    """A command that runs at least everything the plan says validates it.

    When the displayed list is capped, enumerating only the shown tests would
    read as a complete validation run while silently skipping the rest, so the
    selection widens to the files those tests live in: bounded by file count
    rather than test count, and never narrower than the evidence.

    An empty list means the plan has no command to suggest. Every command names
    its tests: with none found, a bare ``pytest`` or ``npm test`` would run a
    whole suite as if it guarded the change. Nothing here looks at the
    repository's tooling, so a language other than Python or JS/TS gets none in
    place of a guess that would fail when run.
    """
    if total is not None and total > len(tests):
        tests = sorted({test.split("::", 1)[0] for test in tests})
    python_tests = sorted(test for test in tests if ".py" in test)
    js_tests = sorted(
        test for test in tests if any(ext in test for ext in (".ts", ".tsx", ".js", ".jsx"))
    )
    commands: list[str] = []
    if python_tests:
        commands.append("pytest " + " ".join(python_tests))
    if js_tests:
        commands.append("npm test -- " + " ".join(js_tests))
    return commands


__all__ = ["commands_for_tests"]
