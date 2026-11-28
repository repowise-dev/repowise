"""The command that runs a plan's tests, shared by plan validation and staged-plan batches.

Where one test's package runs is the test runners' answer
(:mod:`repowise.core.test_runners.packages`); this module stores those runs on
a plan and joins them into commands.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ....test_runners import PYTHON_SUFFIXES
from ....test_runners.packages import (
    TestRuns,
    file_of_test,
    is_js_test,
    js_root_runs,
    npm_run,
    npm_test_run,
    quote_arg,
)

# Plans and their tests import the runs from here too.
from ....test_runners.packages import runs_for_tests as runs_for_tests


def keeps_runs(tests: Iterable[str], runs: TestRuns) -> bool:
    """Whether a plan stores its *runs* map: when it holds any, or is empty
    beside a JS/TS test, so a later read does not take the plan for one stored
    before runs existed and guess a root ``npm test`` (:func:`stored_runs`)."""
    return bool(runs) or any(is_js_test(test) for test in tests)


def stored_runs(validation: Mapping[str, Any]) -> dict[str, list[str | None]]:
    """The ``test_runs`` a stored *validation* holds, plus the run of each JS/TS
    test a plan stored before JS joined them keeps in ``js_packages``. A plan
    stored with neither ran its JS/TS tests from the root, and still does."""
    runs = validation.get("test_runs")
    if runs is None and validation.get("js_packages") is None:
        tests = validation.get("tests")
        return js_root_runs(t for t in tests if isinstance(t, str)) if isinstance(tests, list) else {}
    out = {k: list(v) for k, v in runs.items()} if isinstance(runs, Mapping) else {}
    packages = validation.get("js_packages")
    for path, owner in packages.items() if isinstance(packages, Mapping) else ():
        if isinstance(owner, str):
            out.setdefault(path, list(npm_test_run(path, owner)))
    return out


def _run_key(runs: TestRuns, test: str) -> str | None:
    """The key *runs* holds for *test*: the test itself, else its file."""
    return test if test in runs else file_of_test(test) if file_of_test(test) in runs else None


def plan_runs(tests: Iterable[str], runs: TestRuns | None) -> dict[str, list[str | None]]:
    """The part of *runs* a plan's *tests* use, stored on the plan so a later
    read (batches, a served plan) needs no checkout."""
    if runs is None:
        return {}
    keys = {key for test in tests if (key := _run_key(runs, test)) is not None}
    return {key: list(runs[key]) for key in keys}


def _merged(args: Iterable[str]) -> list[str]:
    """*args* with every ``--flag=value`` sharing a flag merged into one
    ``--flag "a|b"``; any other arg is kept as is."""
    plain: set[str] = set()
    valued: dict[str, set[str]] = {}
    for arg in args:
        flag, equals, value = arg.partition("=")
        if equals and flag.startswith("-") and " " not in flag:
            valued.setdefault(flag, set()).add(value)
        else:
            plain.add(arg)
    merged = [f'{flag} "{"|".join(sorted(values))}"' for flag, values in sorted(valued.items())]
    return sorted(plain) + merged


def _package_commands(tests: list[str], runs: TestRuns) -> list[str]:
    """One command per run naming every target its tests need; a test that
    needs its whole package widens the run to the whole package."""
    groups: dict[str, set[str] | None] = {}
    for key in {key for test in tests if (key := _run_key(runs, test)) is not None}:
        run, arg = runs[key]
        if arg is None:
            groups[run] = None
        elif (args := groups.setdefault(run, set())) is not None:
            args.add(arg)
    return [" ".join([run, *_merged(groups[run] or ())]) for run in sorted(groups, key=_order)]


def _order(run: str) -> tuple[bool, bool, str]:
    # JS runs first and the root package's first among them, as before JS joined
    # the runs: with no Python test, Fix first quotes this first command.
    return not run.startswith("npm "), run != npm_run(""), run


def commands_for_tests(
    tests: list[str],
    *,
    total: int | None = None,
    runs: TestRuns | None = None,
) -> list[str]:
    """A command that runs at least everything the plan says validates it.

    When the displayed list is capped, enumerating only the shown tests would
    read as a complete validation run while silently skipping the rest, so the
    selection widens to the files those tests live in: bounded by file count
    rather than test count, and never narrower than the evidence.

    An empty list means the plan has no command to suggest. Every command names
    its tests: with none found, a bare ``pytest`` or ``npm test`` would run a
    whole suite as if it guarded the change. A JS/TS, Go, Rust, JVM, Ruby or C#
    test runs its package, or the narrower target *runs* names for it. Any other
    test, or one *runs* does not hold, gets none in place of a guess that would
    fail when run.
    """
    if total is not None and total > len(tests):
        tests = sorted({file_of_test(test) for test in tests})
    python_tests = sorted(test for test in tests if file_of_test(test).endswith(PYTHON_SUFFIXES))
    commands: list[str] = []
    if python_tests:
        commands.append("pytest " + " ".join(map(quote_arg, python_tests)))
    if runs:
        commands.extend(_package_commands(tests, runs))
    return commands


__all__ = [
    "TestRuns",
    "commands_for_tests",
    "js_root_runs",
    "keeps_runs",
    "plan_runs",
    "runs_for_tests",
    "stored_runs",
]
