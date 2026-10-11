"""Tests read a fixture tree through its directory, not through its files.

A file under ``tests/fixtures/ts_sample/`` is data: a test points an indexer or
parser at the ``ts_sample`` directory and never imports or names the file. As
a namer of a changed file (fixture code mentioning ``package.json``) it is a
test helper no test imports, which runs everything.

Such a namer stands instead for the test code that may read its tree: every
file in a test tree (a test, a helper, a conftest) whose text names the tree
(``"ts_sample"``) or the fixtures directory itself (``FIXTURES / name``, a
listing of it) in its code, comments and docstrings aside, case-insensitively. Those readers then route to their tests as
any namer does: a helper through the tests importing it, a conftest through
the tests under it, and a helper no test imports runs everything. The file
stays the namer, and so keeps the full run, when no test code names its tree
or code outside test trees names the tree's path.
"""

from __future__ import annotations

import io
import re
import tokenize
from collections.abc import Callable, Collection, Mapping
from pathlib import PurePosixPath

# Directories holding test data, as named in ``test_paths``' support tokens.
FIXTURE_DIRS = frozenset({"fixtures", "__fixtures__", "testdata"})


def fixture_root(path: str) -> str | None:
    """The fixture tree *path* sits in (``tests/fixtures/ts_sample``), ``None`` outside one.

    The outermost fixtures directory decides; a file directly in it has no tree.
    """
    parts = PurePosixPath(path).parts
    for i, part in enumerate(parts[:-2]):
        if part.lower() in FIXTURE_DIRS:
            return "/".join(parts[: i + 2])
    return None


def with_fixture_readers(
    namers: Mapping[str, list[str]],
    test_code: Collection[str],
    other_code: Collection[str],
    read: Callable[[str], str | None],
    holding: Callable[[Collection[str]], Collection[str] | None] | None = None,
) -> dict[str, list[str]]:
    """*namers* with each fixture-tree namer replaced by the test code reading its tree.

    *test_code* are the checkout's code files in test trees, *other_code* the
    rest; *read* returns a file's text and *holding* (optional) narrows which
    files to read to those holding a name.
    """
    roots = {r for found in namers.values() for n in found if (r := fixture_root(n))}
    if not roots:
        return {f: list(n) for f, n in namers.items()}
    readers = _root_readers(roots, test_code, other_code, read, holding)
    out: dict[str, list[str]] = {}
    for f, found in namers.items():
        kept: dict[str, None] = {}
        for n in found:
            kept.update(dict.fromkeys(r for r in _stand_ins(n, readers) if r != f))
        out[f] = list(kept)
    return out


def _stand_ins(namer: str, readers: Mapping[str, list[str]]) -> list[str]:
    root = fixture_root(namer)
    return readers.get(root, [namer]) if root else [namer]


def _root_readers(
    roots: Collection[str],
    test_code: Collection[str],
    other_code: Collection[str],
    read: Callable[[str], str | None],
    holding: Callable[[Collection[str]], Collection[str] | None] | None,
) -> dict[str, list[str]]:
    """``{fixture root: test code reading it}``; a root that must keep its full run is absent."""
    names = {n for r in roots for n in _dir_names(r)}
    held = holding(names) if holding else None
    texts = {
        p: _code_text(p, read(p) or "").lower()
        for p in sorted({*test_code, *other_code})
        if held is None or p in held
    }
    out: dict[str, list[str]] = {}
    for root in roots:
        leaf, parent = _dir_names(root)
        if any(root.lower() in texts[p] for p in other_code if p in texts):
            continue  # code outside the test trees names the tree: not only tests read it
        found = [
            p
            for p in test_code
            if p in texts and fixture_root(p) is None and _names_either(texts[p], leaf, parent)
        ]
        if found:
            out[root] = found
    return out


def _dir_names(root: str) -> tuple[str, str]:
    """The tree's directory name and the fixtures directory's, as written."""
    path = PurePosixPath(root)
    return path.name, path.parent.name


def _names_either(text: str, *names: str) -> bool:
    return any(
        re.search(rf"(?<![\w\-]){re.escape(n.lower())}(?![\w\-])", text) is not None
        for n in names
    )


def _code_text(path: str, text: str) -> str:
    """*text* without comments, and for Python without docstrings: what can name a path.

    Python that does not tokenize is kept whole.
    """
    from ..ingestion.languages.js_config_values import strip_comments
    from ..ingestion.languages.python_strings import _is_statement

    if not path.endswith(".py"):
        # Ceiling: not string-aware, so a "/*" inside a string can hide code up to a "*/".
        return strip_comments(text.encode("utf-8")).decode("utf-8", errors="replace")
    try:
        tokens = [
            t
            for t in tokenize.tokenize(io.BytesIO(text.encode("utf-8")).readline)
            if t.type not in (tokenize.NL, tokenize.COMMENT)
        ]
    except (tokenize.TokenError, SyntaxError, ValueError):
        return text
    return " ".join(
        t.string
        for i, t in enumerate(tokens)
        if i and not (t.type == tokenize.STRING and _is_statement(tokens, i))
    )
