"""The command that runs a plan's tests, shared by plan validation and staged-plan batches."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from ....ingestion.package_roots import owning_package
from ...test_selection import JS_SUFFIXES, PYTHON_SUFFIXES

#: Each JS/TS test file's package: the directory (``""`` is the repository
#: root) whose package.json declares a ``test`` script, or None when no
#: package.json above the file does. A file the map does not hold is None too.
JsPackages = Mapping[str, str | None]


def _file(test: str) -> str:
    return test.split("::", 1)[0]


def _is_js(test: str) -> bool:
    return _file(test).lower().endswith(JS_SUFFIXES)


def _arg(path: str) -> str:
    # Double quotes hold a space in sh, PowerShell and cmd alike. Ceiling: a
    # path with ``"``, ``$`` or a backtick is left as is; upgrade by quoting
    # per shell once a plan names its shell.
    return f'"{path}"' if re.search(r"\s", path) and not re.search(r'["$`]', path) else path


def _declares_test(manifest: Path) -> bool:
    try:
        scripts = json.loads(manifest.read_text(encoding="utf-8")).get("scripts")
    except (OSError, ValueError, AttributeError):
        return False
    script = scripts.get("test") if isinstance(scripts, dict) else None
    # npm init's placeholder exits 1 without running anything.
    return isinstance(script, str) and bool(script) and "no test specified" not in script


def js_test_packages(
    root: Path, manifests: Mapping[str, frozenset[str]], tests: Iterable[str]
) -> dict[str, str | None]:
    """The package that runs each JS/TS test in *tests*: the nearest package
    root (*manifests*, from :func:`scan_package_manifests`) whose package.json
    declares a test script. Blocking file reads: an async caller uses a thread.
    """
    declares: dict[str, bool] = {}

    def accepts(directory: str) -> bool:
        if directory not in declares:
            declares[directory] = _declares_test(root / directory / "package.json")
        return declares[directory]

    return {
        path: owning_package(path, manifests, "package.json", accepts)
        for path in {_file(test) for test in tests if _is_js(test)}
    }


def plan_packages(tests: Iterable[str], packages: JsPackages | None) -> dict[str, str | None]:
    """The part of *packages* a plan's *tests* use, stored on the plan so a
    later read (batches, a served plan) needs no checkout. Empty without a map."""
    if packages is None:
        return {}
    return {path: packages.get(path) for path in {_file(test) for test in tests if _is_js(test)}}


def _js_commands(tests: list[str], packages: JsPackages | None) -> list[str]:
    """``npm test`` from the package each test belongs to; a test no package
    can run gets no command. Without *packages* every test runs from the root."""
    groups: dict[str, list[str]] = {}
    for test in tests:
        owner = packages.get(_file(test)) if packages is not None else ""
        if owner is not None:
            groups.setdefault(owner, []).append(test[len(owner) + 1 :] if owner else test)
    return [
        ("npm test -- " if not owner else f"npm --prefix {_arg(owner)} test -- ")
        + " ".join(map(_arg, paths))
        for owner, paths in sorted(groups.items())
    ]


def commands_for_tests(
    tests: list[str], *, total: int | None = None, packages: JsPackages | None = None
) -> list[str]:
    """A command that runs at least everything the plan says validates it.

    When the displayed list is capped, enumerating only the shown tests would
    read as a complete validation run while silently skipping the rest, so the
    selection widens to the files those tests live in: bounded by file count
    rather than test count, and never narrower than the evidence.

    An empty list means the plan has no command to suggest. Every command names
    its tests: with none found, a bare ``pytest`` or ``npm test`` would run a
    whole suite as if it guarded the change. A JS/TS test runs from the package
    whose ``test`` script owns it (*packages*), with its path relative to that
    package; a test no package.json can run gets no command. A language other
    than Python or JS/TS gets none in place of a guess that would fail when run.
    """
    if total is not None and total > len(tests):
        tests = sorted({_file(test) for test in tests})
    python_tests = sorted(test for test in tests if _file(test).endswith(PYTHON_SUFFIXES))
    js_tests = sorted(test for test in tests if _is_js(test))
    commands: list[str] = []
    if python_tests:
        commands.append("pytest " + " ".join(map(_arg, python_tests)))
    commands.extend(_js_commands(js_tests, packages))
    return commands


__all__ = ["JsPackages", "commands_for_tests", "js_test_packages", "plan_packages"]
