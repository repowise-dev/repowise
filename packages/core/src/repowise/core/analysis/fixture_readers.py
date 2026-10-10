"""Tests read a fixture tree through its directory, not through its files.

A file under ``tests/fixtures/ts_sample/`` is data: a test points an indexer or
parser at the ``ts_sample`` directory and never imports or names the file. As
a namer of a changed file (fixture code mentioning ``package.json``) it is a
test helper no test imports, which runs everything.

Such a namer stands instead for the tests that read its fixture root: every
test whose text names the root directory (``"ts_sample"``, ``fixtures /
"ts_sample"``), and every test that names the fixtures directory and walks a
directory, since it may read every root below it. A fixture root no test
reaches that way keeps the file as the namer, so the full run stays.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping
from pathlib import PurePosixPath

# Directories holding test data, as named in ``test_paths``' support tokens.
FIXTURE_DIRS = frozenset({"fixtures", "__fixtures__", "testdata"})
# A directory listing in Python or JavaScript (as ``always_run`` reads one).
_WALKS = re.compile(
    r"\.rglob\(|\bos\.walk\(|\.glob\(|\bglob\.i?glob\(|\bos\.listdir\(|\bos\.scandir\("
    r"|\.iterdir\(|\breaddir(?:Sync)?\s*\(|\bglob(?:Sync)?\s*\(|\bfg(?:\.sync)?\s*\("
)


def fixture_root(path: str) -> str | None:
    """The fixture tree *path* sits in (``tests/fixtures/ts_sample``), ``None`` outside one.

    A file directly in the fixtures directory has no tree of its own.
    """
    parts = PurePosixPath(path).parts
    for i, part in enumerate(parts[:-2]):
        if part in FIXTURE_DIRS:
            return "/".join(parts[: i + 2])
    return None


def with_fixture_readers(
    namers: Mapping[str, list[str]],
    tests: Collection[str],
    read: Callable[[str], str | None],
    holding: Callable[[Collection[str]], Collection[str] | None] | None = None,
) -> dict[str, list[str]]:
    """*namers* with each fixture-tree namer replaced by the tests reading its root.

    *tests* are the checkout's runnable tests, *read* returns a file's text and
    *holding* (optional) narrows which files to read to those holding a name.
    """
    roots = {r for found in namers.values() for n in found if (r := fixture_root(n))}
    if not roots:
        return {f: list(n) for f, n in namers.items()}
    readers = _root_readers(roots, tests, read, holding)
    out: dict[str, list[str]] = {}
    for f, found in namers.items():
        kept: dict[str, None] = {}
        for n in found:
            root = fixture_root(n)
            for reader in readers.get(root, [n]) if root else [n]:
                if reader != f:
                    kept[reader] = None
        out[f] = list(kept)
    return out


def _root_readers(
    roots: Collection[str],
    tests: Collection[str],
    read: Callable[[str], str | None],
    holding: Callable[[Collection[str]], Collection[str] | None] | None,
) -> dict[str, list[str]]:
    """``{fixture root: tests reading it}``; a root with none is absent."""
    names = {PurePosixPath(r).name for r in roots} | {
        PurePosixPath(r).parent.name for r in roots
    }
    held = holding(names) if holding else None
    candidates = sorted(t for t in tests if held is None or t in held)
    out: dict[str, list[str]] = {}
    for test in candidates:
        text = read(test) or ""
        walks = bool(_WALKS.search(text))
        for root in roots:
            leaf, parent = PurePosixPath(root).name, PurePosixPath(root).parent.name
            if _names_dir(text, leaf) or (walks and _names_dir(text, parent)):
                out.setdefault(root, []).append(test)
    return out


def _names_dir(text: str, name: str) -> bool:
    return re.search(rf"(?<![\w\-]){re.escape(name)}(?![\w\-])", text) is not None
