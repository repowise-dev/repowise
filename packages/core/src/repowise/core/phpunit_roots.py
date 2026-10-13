"""Where PHPUnit collects tests from, read from its config files.

A PHP file whose class name ends in ``Test`` (``src/Testing/Attributes/UnitTest.php``)
is production code when PHPUnit's own configuration keeps it out of collection:
``<testsuites>`` defines the suites, directories, files and exclusions that a
PHPUnit test run executes. :mod:`.test_paths` asks :meth:`PhpunitRoots.collects`
before trusting a test-shaped PHP name outside any test directory.

Pure: callers hand in config text, so ingestion reads each config once in the
walk it already makes and a server can call this with data it holds.

Each config governs its own directory, nearest first. Precedence within a
directory: ``phpunit.xml``, ``phpunit.dist.xml``, ``phpunit.xml.dist``.
A nested config's suites are resolved against its own directory and against the
repository root, and a file any config above it collects counts as collected.

Fail-closed: only a definite ``False`` demotes. No config, an unreadable config,
or a config with no ``<testsuites>`` keeps today's answer, and any config that
collects the file keeps it a test.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import PurePosixPath

PHPUNIT_CONFIG_FILES: tuple[str, ...] = (
    "phpunit.xml",
    "phpunit.dist.xml",
    "phpunit.xml.dist",
)
PHPUNIT_CONFIG_NAMES: frozenset[str] = frozenset(PHPUNIT_CONFIG_FILES)
_PRECEDENCE = {name: rank for rank, name in enumerate(PHPUNIT_CONFIG_FILES)}


class PhpunitConfigUnreadableError(ValueError):
    """A PHPUnit config file that does not parse."""


@dataclass(frozen=True, slots=True)
class _SuiteDirectory:
    path: str
    suffix: str = "Test.php"
    prefix: str = ""
    excludes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _TestSuite:
    name: str
    directories: tuple[_SuiteDirectory, ...] = ()
    files: tuple[str, ...] = ()
    excludes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _PhpunitCollection:
    suites: tuple[_TestSuite, ...]


@dataclass(frozen=True, slots=True)
class PhpunitRoots:
    """Test suite collections for each directory holding a PHPUnit config."""

    by_dir: Mapping[str, _PhpunitCollection] = field(default_factory=dict)
    unreadable: frozenset[str] = frozenset()

    def collects(self, path: str) -> bool | None:
        """Whether PHPUnit collects *path*; ``None`` when no config governs it.

        Collected when any governing config at or above it collects it;
        only a unanimous "no" demotes a test. If an ancestor directory has an
        unreadable config, returns ``None`` (fail-closed).
        """
        p = PurePosixPath(path)
        dirs = ["" if str(parent) == "." else str(parent) for parent in p.parents]
        if any(d in self.unreadable for d in dirs):
            return None
        verdicts = [
            _collected(p, p.relative_to(parent), collection)
            for parent in p.parents
            if (collection := self.by_dir.get("" if str(parent) == "." else str(parent))) is not None
        ]
        return any(verdicts) if verdicts else None


def _matches_pattern_or_parent(
    candidate: PurePosixPath, pattern_str: str, parents: tuple[PurePosixPath, ...]
) -> bool:
    if pattern_str in (".", ""):
        return True
    pat_path = PurePosixPath(pattern_str)
    if candidate == pat_path or pat_path in parents:
        return True
    all_str_candidates = (str(candidate), *(str(c) for c in parents if str(c) != "."))
    return any(fnmatchcase(c, pattern_str) for c in all_str_candidates)


def _suite_collects(path: PurePosixPath, rel: PurePosixPath, suite: _TestSuite) -> bool:
    path_parents = tuple(path.parents)
    rel_parents = tuple(rel.parents)

    # Suite-level exclusions
    for ex in suite.excludes:
        if _matches_pattern_or_parent(rel, ex, rel_parents) or _matches_pattern_or_parent(
            path, ex, path_parents
        ):
            return False

    # Explicit files
    for f in suite.files:
        f_path = PurePosixPath(f)
        if rel == f_path or path == f_path or fnmatchcase(str(rel), f) or fnmatchcase(str(path), f):
            return True

    # Directories
    filename = path.name
    for d in suite.directories:
        # Check directory-level exclusions
        if any(
            _matches_pattern_or_parent(rel, ex, rel_parents)
            or _matches_pattern_or_parent(path, ex, path_parents)
            for ex in d.excludes
        ):
            continue

        # Check if the path falls within the directory
        if not (
            _matches_pattern_or_parent(rel, d.path, rel_parents)
            or _matches_pattern_or_parent(path, d.path, path_parents)
        ):
            continue

        # Check filename suffix and prefix
        if d.prefix and not filename.startswith(d.prefix):
            continue

        if d.suffix:
            if d.suffix.startswith("*"):
                if not fnmatchcase(filename, d.suffix):
                    continue
            elif not filename.endswith(d.suffix):
                continue

        return True

    return False


def _collected(path: PurePosixPath, rel: PurePosixPath, collection: _PhpunitCollection) -> bool:
    return any(_suite_collects(path, rel, suite) for suite in collection.suites)


def phpunit_options(text: str) -> _PhpunitCollection | None:
    """The PHPUnit suite collection of one config, or ``None`` if absent or unreadable."""
    try:
        return parse_phpunit_options(text)
    except PhpunitConfigUnreadableError:
        return None


def parse_phpunit_options(text: str) -> _PhpunitCollection | None:
    """:func:`phpunit_options`, raising :class:`PhpunitConfigUnreadableError` on malformed XML."""
    if not text.strip():
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise PhpunitConfigUnreadableError(f"Malformed XML: {exc}") from exc

    # A PHPUnit configuration root tag is <phpunit>
    if root.tag.lower() != "phpunit":
        return None

    suite_elements = list(root.iter("testsuite"))
    if not suite_elements:
        return None

    suites: list[_TestSuite] = []
    for suite_elem in suite_elements:
        name = suite_elem.get("name", "")
        suite_excludes = tuple(
            _clean_path(ex.text) for ex in suite_elem.findall("exclude") if _clean_path(ex.text)
        )
        suite_files = tuple(
            _clean_path(f.text) for f in suite_elem.findall("file") if _clean_path(f.text)
        )

        directories: list[_SuiteDirectory] = []
        for dir_elem in suite_elem.findall("directory"):
            dir_path = _clean_path(dir_elem.text)
            if not dir_path:
                continue
            suffix = dir_elem.attrib.get("suffix", "Test.php")
            prefix = dir_elem.attrib.get("prefix", "")
            dir_excludes = tuple(
                _clean_path(ex.text) for ex in dir_elem.findall("exclude") if _clean_path(ex.text)
            )
            directories.append(
                _SuiteDirectory(
                    path=dir_path,
                    suffix=suffix,
                    prefix=prefix,
                    excludes=dir_excludes,
                )
            )

        suites.append(
            _TestSuite(
                name=name,
                directories=tuple(directories),
                files=suite_files,
                excludes=suite_excludes,
            )
        )

    return _PhpunitCollection(suites=tuple(suites))


def _clean_path(val: str | None) -> str:
    if not val:
        return ""
    cleaned = val.strip().replace("\\", "/").strip("/")
    if cleaned.startswith("./"):
        cleaned = cleaned[2:].strip("/")
    return cleaned


def _dir_key(path: PurePosixPath) -> str:
    return "" if str(path.parent) == "." else str(path.parent)


def phpunit_roots(
    configs: Iterable[tuple[str, _PhpunitCollection]], unreadable: Iterable[str] = ()
) -> PhpunitRoots:
    """Build from ``(config path, collection)`` pairs.

    The highest-precedence config in each directory wins. If an unreadable or
    unknown config has higher or equal precedence than any parsed config in the
    same directory, that directory fails closed (marked unreadable).
    """
    chosen: dict[str, tuple[int, _PhpunitCollection]] = {}
    for path, collection in configs:
        p = PurePosixPath(path)
        key = _dir_key(p)
        rank = _PRECEDENCE.get(p.name, len(_PRECEDENCE))
        if key not in chosen or rank < chosen[key][0]:
            chosen[key] = (rank, collection)

    unreadable_ranks: dict[str, int] = {}
    for path in unreadable:
        p = PurePosixPath(path)
        key = _dir_key(p)
        rank = _PRECEDENCE.get(p.name, len(_PRECEDENCE))
        if key not in unreadable_ranks or rank < unreadable_ranks[key]:
            unreadable_ranks[key] = rank

    unreadable_dirs: set[str] = set()
    for key, unreadable_rank in unreadable_ranks.items():
        if key not in chosen or unreadable_rank <= chosen[key][0]:
            unreadable_dirs.add(key)
            chosen.pop(key, None)

    return PhpunitRoots(
        by_dir={key: col for key, (_, col) in chosen.items()},
        unreadable=frozenset(unreadable_dirs),
    )


def read_phpunit_roots(texts: Iterable[tuple[str, str]]) -> PhpunitRoots:
    """:func:`phpunit_roots` from ``(config path, text)`` pairs."""
    configs: list[tuple[str, _PhpunitCollection]] = []
    unreadable: list[str] = []
    for path, text in texts:
        try:
            options = parse_phpunit_options(text)
            if options is not None:
                configs.append((path, options))
            else:
                unreadable.append(path)
        except PhpunitConfigUnreadableError:
            unreadable.append(path)
    return phpunit_roots(configs, unreadable)
