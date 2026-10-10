"""Where a bare ``pytest`` run collects tests from, read from its config files.

A Python module named like a test (``core/test_paths.py``, ``cli/approvals_test.py``)
is production code when pytest's own configuration keeps it out of collection:
``testpaths`` names the directories a bare run searches, and ``python_files`` the
file names it collects. :mod:`.test_paths` asks :meth:`PytestRoots.collects`
before trusting a test-shaped Python name outside any test directory.

Pure: callers hand in config text (or the already-parsed ``pyproject.toml``
table), so ingestion reads each config once in the walk it already makes and a
server can call this with data it holds.

Each config governs its own directory, nearest first, the way running ``pytest``
inside a package picks up that package's config. Within one directory pytest's
precedence applies: ``pytest.ini`` (even with no section), then
``pyproject.toml``, ``tox.ini``, ``setup.cfg``. A nested config's ``testpaths``
are read against its own directory and against the repository root, because a
CI job may pass it with ``-c`` from the root (``pytest -c skills/pyproject.toml
skills``); a file either reading collects counts as collected, so a doubt keeps
a test a test. Ceiling: ``python_files``
patterns are matched against the file name only, so a pattern with a directory
part never matches; none seen so far.
"""

from __future__ import annotations

import configparser
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Any

# The files pytest reads options from, in its own precedence order, with the
# section that holds them (``None``: the ``[tool.pytest.ini_options]`` table).
PYTEST_CONFIG_SECTIONS: tuple[tuple[str, str | None], ...] = (
    ("pytest.ini", "pytest"),
    ("pyproject.toml", None),
    ("tox.ini", "pytest"),
    ("setup.cfg", "tool:pytest"),
)
PYTEST_CONFIG_NAMES: frozenset[str] = frozenset(name for name, _ in PYTEST_CONFIG_SECTIONS)
_PRECEDENCE = {name: rank for rank, (name, _) in enumerate(PYTEST_CONFIG_SECTIONS)}


@dataclass(frozen=True, slots=True)
class _Collection:
    testpaths: tuple[str, ...]
    python_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PytestRoots:
    """``testpaths`` and ``python_files`` for each directory holding a pytest config."""

    by_dir: Mapping[str, _Collection] = field(default_factory=dict)

    def collects(self, path: str) -> bool | None:
        """Whether a bare ``pytest`` run collects *path*; ``None`` when no config governs it."""
        p = PurePosixPath(path)
        for parent in p.parents:
            key = "" if str(parent) == "." else str(parent)
            if (rules := self.by_dir.get(key)) is not None:
                return _collected(p, p.relative_to(parent), rules)
        return None


def _collected(path: PurePosixPath, rel: PurePosixPath, rules: _Collection) -> bool:
    if rules.python_files and not any(fnmatchcase(path.name, pat) for pat in rules.python_files):
        return False
    if not rules.testpaths or "." in rules.testpaths:
        return True
    candidates = {str(c) for p in (rel, path) for c in (p, *p.parents) if str(c) != "."}
    return any(fnmatchcase(c, root) for root in rules.testpaths for c in candidates)


def pytest_options(
    name: str, text: str = "", *, toml: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    """The pytest options of one config file, or ``None`` when it configures no pytest.

    *toml* is ``pyproject.toml`` already parsed, so a caller that read it for
    another table does not parse it twice. A file that does not parse yields
    ``None``, as it would leave pytest to its defaults here.
    """
    section = dict(PYTEST_CONFIG_SECTIONS).get(name, "")
    if section == "":
        return None
    try:
        if section is None:
            data = toml if toml is not None else tomllib.loads(text)
            block = data.get("tool", {}).get("pytest", {}).get("ini_options")
            return block if isinstance(block, dict) else None
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(text)
    except (tomllib.TOMLDecodeError, configparser.Error, AttributeError):
        return None
    if not parser.has_section(section):
        return {} if name == "pytest.ini" else None
    return dict(parser.items(section))


def pytest_roots(configs: Iterable[tuple[str, Mapping[str, Any]]]) -> PytestRoots:
    """Build from ``(config path, options)`` pairs (:func:`pytest_options`).

    The highest-precedence config in each directory wins.
    """
    chosen: dict[str, tuple[int, Mapping[str, Any]]] = {}
    for path, options in configs:
        p = PurePosixPath(path)
        key = "" if str(p.parent) == "." else str(p.parent)
        rank = _PRECEDENCE.get(p.name, len(_PRECEDENCE))
        if key not in chosen or rank < chosen[key][0]:
            chosen[key] = (rank, options)
    return PytestRoots(
        {
            key: _Collection(
                tuple(v.strip("/").removeprefix("./") or "." for v in _words(o.get("testpaths"))),
                _words(o.get("python_files")),
            )
            for key, (_, o) in chosen.items()
        }
    )


def read_pytest_roots(texts: Iterable[tuple[str, str]]) -> PytestRoots:
    """:func:`pytest_roots` from ``(config path, text)`` pairs."""
    return pytest_roots(
        (path, options)
        for path, text in texts
        if (options := pytest_options(PurePosixPath(path).name, text)) is not None
    )


def _words(value: Any) -> tuple[str, ...]:
    """An ini value is whitespace-separated; a TOML one is a list (or a string)."""
    if not value:
        return ()
    items = value.split() if isinstance(value, str) else value
    return tuple(str(v) for v in items if str(v).strip())
