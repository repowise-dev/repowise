"""Test runners: which runner takes a test file, and the arguments or command it runs.

The one owner of runner knowledge. Two kinds of caller ask it:

- ``impacted-tests`` renders a :class:`~repowise.core.analysis.test_selection.Selection`
  as arguments for one named runner (:func:`resolve_runner`, :func:`runner_args`,
  :func:`left_out`), here.
- Refactoring plans and PR blast reports need the command that runs a test's
  package from the repository root (``go -C mod test ./pkg``, ``cargo test -p
  crate``), in :mod:`.packages`, which reads manifests and so is imported on its
  own: this module stays light enough for the selection path.
- Coverage setup needs each build unit's test command with coverage, its
  prerequisites and report paths, in :mod:`.stacks`.

The first two read their test-file suffixes from :data:`LANGUAGE_SUFFIXES`.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..analysis.test_selection import Selection

#: Printed alone instead of arguments when every test must run.
RUN_ALL = ":all"

#: The runners ``impacted-tests --runner`` takes; ``auto`` picks one.
RUNNERS = ("auto", "pytest", "go", "jest", "files")

PYTHON_SUFFIXES = (".py",)
JS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")

#: Test-file suffixes per language some runner here runs.
LANGUAGE_SUFFIXES: dict[str, tuple[str, ...]] = {
    "python": PYTHON_SUFFIXES,
    "javascript": JS_SUFFIXES,
    "go": (".go",),
    "rust": (".rs",),
    "jvm": (".java", ".kt"),
    "ruby": (".rb",),
    "dotnet": (".cs",),
}

#: Every extension a test runner loads, test or not.
TEST_CODE_SUFFIXES = frozenset(
    {".py", ".go", ".java", ".kt", ".kts", ".scala", ".groovy", ".rb", ".cs", ".fs"}
    | set(JS_SUFFIXES)
    | {".rs", ".php", ".swift", ".ex", ".exs", ".dart", ".c", ".cc", ".cpp", ".cls"}
    | {".t", ".pl", ".pm", ".test", ".tcl"}
)


def is_code_file(path: str) -> bool:
    """A file with an extension a test runner loads, test or not."""
    return PurePosixPath(path).suffix.lower() in TEST_CODE_SUFFIXES


# The test files each named runner takes; ``files`` takes every one.
_RUNNER_SUFFIXES = {
    "pytest": LANGUAGE_SUFFIXES["python"],
    "go": LANGUAGE_SUFFIXES["go"],
    "jest": LANGUAGE_SUFFIXES["javascript"],
}


def _takes(runner: str, test: str) -> bool:
    """Whether *test* (a file or node id) is in *runner*'s language; case-blind."""
    return test.split("::", 1)[0].lower().endswith(_RUNNER_SUFFIXES[runner])


def _all_python(files: tuple[str, ...], packages: tuple[str, ...]) -> bool:
    return bool(files) and all(_takes("pytest", f) for f in files)


def _all_go(files: tuple[str, ...], packages: tuple[str, ...]) -> bool:
    # A changed Go file's own package counts even with no selected test file.
    return bool(files or packages) and all(f.endswith("_test.go") for f in files)


def _all_js(files: tuple[str, ...], packages: tuple[str, ...]) -> bool:
    return bool(files) and all(_takes("jest", f) for f in files)


# ``auto`` picks the first runner every selected test file belongs to.
_AUTO_RUNNERS = (("pytest", _all_python), ("go", _all_go), ("jest", _all_js))


def resolve_runner(selection: Selection, runner: str) -> str:
    """*runner*, or for ``auto`` the one every selected test file belongs to."""
    if runner != "auto":
        return runner
    files, packages = selection.test_files, selection.packages
    return next((name for name, fits in _AUTO_RUNNERS if fits(files, packages)), "files")


def _pytest_args(selection: Selection) -> list[str]:
    return [t for t in selection.tests if _takes("pytest", t)]


def _go_args(selection: Selection) -> list[str]:
    go_tests = [f for f in selection.test_files if f.endswith("_test.go")]
    dirs = list(dict.fromkeys(str(PurePosixPath(f).parent) for f in go_tests))
    for package in selection.packages:
        if package not in dirs:
            dirs.append(package)
    return ["." if d == "." else f"./{d}" for d in dirs]


def _jest_args(selection: Selection) -> list[str]:
    return [f for f in selection.test_files if _takes("jest", f)]


def _file_args(selection: Selection) -> list[str]:
    return list(selection.test_files)


_RUNNER_ARGS = {"pytest": _pytest_args, "go": _go_args, "jest": _jest_args}


def runner_args(selection: Selection, runner: str) -> list[str]:
    """Arguments for *runner* (already resolved); ``[RUN_ALL]`` for a full run.

    Each runner gets only the tests it can run, so a job per language can share
    one selection; :func:`runner_notes` names the rest. ``tests.always_run``
    entries are appended as written, but one in another runner's language is
    left to that runner. jest and vitest get file paths, meant for
    ``--runTestsByPath``.
    """
    if selection.run_all:
        return [RUN_ALL]
    args = _RUNNER_ARGS.get(runner, _file_args)(selection)
    always = [t for t in selection.always_run if _runs(t, runner)]
    return list(dict.fromkeys([*args, *always]))


def _runs(test: str, runner: str) -> bool:
    """Whether *runner* can run *test*; a directory or glob it cannot tell is kept."""
    if runner not in _RUNNER_SUFFIXES or not is_code_file(test.split("::", 1)[0]):
        return True
    return _takes(runner, test)


def left_out(selection: Selection, runner: str) -> dict[str, list[str]]:
    """Selected tests *runner*'s arguments leave out, keyed by the runner that takes them.

    A test no named runner takes (Kotlin, Swift) is keyed ``files``. Empty on a
    full run, where every runner runs everything of its own.
    """
    if selection.run_all:
        return {}
    out: dict[str, list[str]] = {}
    for test in dict.fromkeys((*selection.test_files, *selection.always_run)):
        if not _runs(test, runner):
            owner = next((r for r in _RUNNER_SUFFIXES if _takes(r, test)), "files")
            out.setdefault(owner, []).append(test)
    return out


def runner_notes(selection: Selection, runner: str) -> list[str]:
    """One line naming what :func:`left_out` found, so nothing is dropped silently.

    A pipeline running one runner needs a job per runner for these.
    """
    left = left_out(selection, runner)
    if not left:
        return []
    counts = ", ".join(f"{len(tests)} for {owner}" for owner, tests in sorted(left.items()))
    first = next(iter(left.values()))[0]
    return [
        f"Selected tests for other runners are not in these {runner} arguments ({counts}; "
        f"e.g. {first}); run them in a job per runner."
    ]


def format_args(args: Iterable[str]) -> str:
    """One shell-quoted line."""
    return shlex.join(args)


__all__ = [
    "JS_SUFFIXES",
    "LANGUAGE_SUFFIXES",
    "PYTHON_SUFFIXES",
    "RUNNERS",
    "RUN_ALL",
    "TEST_CODE_SUFFIXES",
    "format_args",
    "is_code_file",
    "left_out",
    "resolve_runner",
    "runner_args",
    "runner_notes",
]
