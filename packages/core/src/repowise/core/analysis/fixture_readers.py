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

A changed data file in a fixtures directory is resolved the same way
(:func:`with_data_file_readers`): the test code naming the file, or the tree it
sits in, reads it. It keeps the full run when code outside the test trees names
the file or the tree, or when no test code does (a glob or a computed path).
"""

from __future__ import annotations

import io
import re
import tokenize
from collections.abc import Callable, Collection, Mapping
from pathlib import PurePosixPath

from ..test_paths import is_test_related_path

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


def with_data_file_readers(
    files: Collection[str],
    namers: Mapping[str, list[str]],
    test_code: Collection[str],
    other_code: Collection[str],
    read: Callable[[str], str | None],
    holding: Callable[[Collection[str]], Collection[str] | None] | None = None,
) -> dict[str, list[str]]:
    """``{changed fixture data file: the test code reading it}``.

    *namers* are the code files naming each of *files* by file name
    (:func:`~.test_selection.file_namers`). A file inside a fixture tree is also
    read by the test code naming the tree. A file is absent, and so keeps its
    full run, when code outside the test trees names it or its tree, or when no
    test code does: a fixture read through a glob or a computed path is the
    case that fallback protects.
    """
    trees = {f: r for f in files if (r := fixture_root(f))}
    texts = _texts(set(trees.values()), test_code, other_code, read, holding) if trees else {}
    out: dict[str, list[str]] = {}
    for f in files:
        readers = [n for n in namers.get(f, ()) if n != f]
        if not all(is_test_related_path(n) for n in readers):
            continue  # production code names the file: not only tests read it
        if root := trees.get(f):
            tree = _tree_readers(root, texts, test_code, other_code)
            if tree is None:
                continue
            readers += [r for r in tree if r not in readers]
        if readers:
            out[f] = readers
    return out


#: File stems naming an entry point rather than the file; its directory carries the name.
_ENTRY_STEMS = frozenset({"main", "index", "__init__", "__main__", "mod", "lib"})


def helper_readers(
    helpers: Collection[str],
    test_files: Collection[str],
    read: Callable[[str], str | None],
) -> dict[str, list[str]]:
    """``{helper: the test files that may start it by path}`` for each of *helpers*.

    A test helper no test imports is used, if at all, by a test file naming it:
    a subprocess, a fixture runner, a mock, a case list. Its readers are the test
    files (code or data) naming its file name or its name up to the first dot
    (a module path, a fixture runner adding ``.input.tsx``), comments aside; those
    writing its directory or the one above as a path (``"typing"``,
    ``./cmd/tool``), a listing; the runnable tests under the directory above it,
    which may list it unnamed; and code beside it that lists its directory. An
    empty list means no test file can reach it.

    A helper a runner or build config at or above its directory names (an
    alias, a setup file, a plugin), or one at the repository root, is left out,
    so its users stay unknown. Ceiling: a module outside the test trees that a
    test imports and that starts the helper by path is not read.
    """
    from .test_selection import is_runnable_test

    names = {
        h: _helper_names(h)
        for h in helpers
        if str(PurePosixPath(h).parent) != "." and not _configured(h, read)
    }
    wanted = {n.lower() for words, dirs in names.values() for n in (*words, *dirs)}
    texts: dict[str, str] = {}
    for p in sorted(test_files):
        text = (read(p) or "").lower()
        if any(map(text.__contains__, wanted)) or _LISTING.search(text):
            texts[p] = code_text(p, text)
    out: dict[str, list[str]] = {}
    for h, (words, dirs) in names.items():
        parent = PurePosixPath(h).parent
        home, near = f"{parent}/", f"{parent.parent}/" if str(parent.parent) != "." else f"{parent}/"
        out[h] = [
            p
            for p in sorted(test_files)
            if p != h
            and (
                (p.startswith(near) and is_runnable_test(p))
                or (p.startswith(home) and p in texts and _LISTING.search(texts[p]) is not None)
                or (
                    p in texts
                    and (
                        _names_either(texts[p], *words)
                        or any(_writes_dir(texts[p], d) for d in dirs)
                    )
                )
            )
        ]
    return out


#: Code listing a directory, which reaches a file it never names.
_LISTING = re.compile(r"\b(?:glob|rglob|listdir|scandir|iterdir|walk|readdir(?:sync)?)\b")


#: Configs that can hand a test a helper without an import: an alias, a setup
#: file, a plugin. Ceiling: probed by name in each directory above the helper,
#: so a config under another name (``tsconfig.e2e.json``) is not read; listing
#: the checkout's configs is the upgrade.
_CONFIG_NAMES = (
    "package.json",
    "tsconfig.json",
    "tsconfig.spec.json",
    "tsconfig.test.json",
    "pytest.ini",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "karma.conf.js",
    *(
        f"{tool}.{kind}.{ext}"
        for tool in ("vitest", "vite", "jest", "playwright")
        for kind in ("config", "workspace")
        for ext in ("ts", "js", "mts", "mjs", "cts", "cjs", "json")
    ),
)


def _configured(helper: str, read: Callable[[str], str | None]) -> bool:
    """Whether a config at or above *helper*'s directory names its file name or stem."""
    words, _ = _helper_names(helper)
    folder = PurePosixPath(helper).parent
    for d in (folder, *folder.parents):
        for name in _CONFIG_NAMES:
            text = read(name if str(d) == "." else f"{d}/{name}")
            if text and _names_either(text.lower(), *words):
                return True
    return False


def _helper_names(helper: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """*helper*'s file name and stem (unless an entry-point stem), and the names of
    its directory and the one above."""
    path = PurePosixPath(helper)
    stem = path.name.split(".")[0]
    words = (path.name,) if stem.lower() in _ENTRY_STEMS else (path.name, stem)
    dirs = (path.parent.name, path.parent.parent.name)
    return tuple(dict.fromkeys(w for w in words if w)), tuple(d for d in dirs if d)


def _writes_dir(text: str, name: str) -> bool:
    """Whether *text* writes directory *name* as a path: next to a quote or a slash.

    ``from typing import`` names a module, ``"typing"`` or ``tests/typing/`` a directory.
    """
    if not name:
        return False
    n = re.escape(name.lower())
    return re.search(rf"[\"'/\\]{n}(?![\w\-])|(?<![\w\-]){n}[\"'/\\]", text) is not None


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
    texts = _texts(roots, test_code, other_code, read, holding)
    out: dict[str, list[str]] = {}
    for root in roots:
        if found := _tree_readers(root, texts, test_code, other_code):
            out[root] = found
    return out


def _texts(
    roots: Collection[str],
    test_code: Collection[str],
    other_code: Collection[str],
    read: Callable[[str], str | None],
    holding: Callable[[Collection[str]], Collection[str] | None] | None,
) -> dict[str, str]:
    """The lowercased code text of each file that may name one of *roots*' trees.

    Code outside the test trees counts only when it names a tree's whole path
    (:func:`_tree_readers`), so one whose raw text holds no such path skips the
    costly :func:`code_text` pass. That pass only drops text or spaces tokens
    apart, so it cannot make a path without whitespace appear.
    """
    names = {n for r in roots for n in _dir_names(r)}
    held = holding(names) if holding else None
    tests = set(test_code)
    paths = [r.lower() for r in roots]
    by_path = not any(c.isspace() for r in paths for c in r)
    out: dict[str, str] = {}
    for p in sorted({*tests, *other_code}):
        if held is not None and p not in held:
            continue
        text = read(p) or ""
        if by_path and p not in tests and not any(map(text.lower().__contains__, paths)):
            continue
        out[p] = code_text(p, text).lower()
    return out


def _tree_readers(
    root: str,
    texts: Mapping[str, str],
    test_code: Collection[str],
    other_code: Collection[str],
) -> list[str] | None:
    """The test code naming *root*'s tree, or ``None`` when code outside the test trees names it."""
    leaf, parent = _dir_names(root)
    if any(root.lower() in texts[p] for p in other_code if p in texts):
        return None  # code outside the test trees names the tree: not only tests read it
    return [
        p
        for p in test_code
        if p in texts and fixture_root(p) is None and _names_either(texts[p], leaf, parent)
    ]


def _dir_names(root: str) -> tuple[str, str]:
    """The tree's directory name and the fixtures directory's, as written."""
    path = PurePosixPath(root)
    return path.name, path.parent.name


def _names_either(text: str, *names: str) -> bool:
    return any(
        re.search(rf"(?<![\w\-]){re.escape(n.lower())}(?![\w\-])", text) is not None
        for n in names
    )


def code_text(path: str, text: str) -> str:
    """*text* without comments, and for Python without docstrings: what can name a path.

    Python that does not tokenize is kept whole.
    """
    from ..ingestion.languages.python_strings import _is_statement
    from .security_scan import mask_comments_and_strings, mask_dialect

    if not path.endswith(".py"):
        # ``#`` is kept: a kept mention selects more, a ``#include`` lost selects less.
        # Ceiling: a regex literal or a Rust lifetime holding a quote can desync the scan.
        return mask_comments_and_strings(
            text, strings=False, hash_comments=False, **mask_dialect(path)
        )
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
