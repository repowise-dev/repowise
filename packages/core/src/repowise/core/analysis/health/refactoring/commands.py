"""The command that runs a plan's tests, shared by plan validation and staged-plan batches."""

from __future__ import annotations

import json
import posixpath
from collections.abc import Iterable, Mapping
from pathlib import Path

_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")

#: Each JS/TS test file's package: the directory (``""`` is the repository
#: root) whose package.json declares a ``test`` script, or None when no
#: package.json above the file does.
JsPackages = Mapping[str, str | None]


def _is_js(test: str) -> bool:
    return any(ext in test for ext in _JS_EXTENSIONS)


def _declares_test(manifest: Path) -> bool:
    try:
        scripts = json.loads(manifest.read_text(encoding="utf-8")).get("scripts")
    except (OSError, ValueError, AttributeError):
        return False
    script = scripts.get("test") if isinstance(scripts, dict) else None
    # npm init's placeholder exits 1 without running anything.
    return isinstance(script, str) and bool(script) and "no test specified" not in script


def _owner(root: Path, directory: str, seen: dict[str, str | None]) -> str | None:
    if directory not in seen:
        if _declares_test(root / directory / "package.json"):
            seen[directory] = directory
        else:
            seen[directory] = (
                _owner(root, posixpath.dirname(directory), seen) if directory else None
            )
    return seen[directory]


def js_test_packages(root: Path, tests: Iterable[str]) -> dict[str, str | None]:
    """The package that runs each JS/TS test in *tests*, read from *root*.

    Walks up from each file only, one manifest read per directory, so the cost
    follows the test directories and never the tree.
    """
    seen: dict[str, str | None] = {}
    return {
        path: _owner(root, posixpath.dirname(path), seen)
        for path in {test.split("::", 1)[0] for test in tests if _is_js(test)}
    }


def js_packages_in(commands: Iterable[str], tests: Iterable[str]) -> dict[str, str | None]:
    """The package each JS/TS test in *tests* runs from in *commands*, as
    :func:`commands_for_tests` wrote them: a later reader of a stored plan has
    no checkout to read package.json from. A test no command names gets None."""
    named: dict[str, str] = {}
    for command in commands:
        owner, _, args = command.partition(" test -- ")
        if not args or not owner.startswith("npm"):
            continue
        owner = owner.removeprefix("npm").removeprefix(" --prefix ")
        for arg in args.split():
            named[posixpath.join(owner, arg.split("::", 1)[0])] = owner
    return {
        path: named.get(path) for path in {test.split("::", 1)[0] for test in tests if _is_js(test)}
    }


def _js_commands(tests: list[str], packages: JsPackages | None) -> list[str]:
    """``npm test`` from the package each test belongs to; a test no package
    can run gets no command. Without *packages* every test runs from the root."""
    groups: dict[str, list[str]] = {}
    for test in tests:
        owner = packages.get(test.split("::", 1)[0], "") if packages is not None else ""
        if owner is not None:
            groups.setdefault(owner, []).append(test[len(owner) + 1 :] if owner else test)
    return [
        ("npm test -- " if not owner else f"npm --prefix {owner} test -- ") + " ".join(paths)
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
        tests = sorted({test.split("::", 1)[0] for test in tests})
    python_tests = sorted(test for test in tests if ".py" in test)
    js_tests = sorted(test for test in tests if _is_js(test))
    commands: list[str] = []
    if python_tests:
        commands.append("pytest " + " ".join(python_tests))
    commands.extend(_js_commands(js_tests, packages))
    return commands


__all__ = ["JsPackages", "commands_for_tests", "js_packages_in", "js_test_packages"]
