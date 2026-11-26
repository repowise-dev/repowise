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
    (a module path, a fixture runner adding ``.input.tsx``), comments aside, as
    :func:`_names_helper` reads a name; those
    writing its directory or the one above as a path (``"typing"``,
    ``./cmd/tool``), a listing; the runnable tests under the directory above it,
    which may list it unnamed; and code beside it that lists its directory. An
    empty list means no test file can reach it.

    A helper a runner or build config at or above its directory names (an
    alias, a setup file, a plugin), or one at the repository root, is left out,
    so its users stay unknown. Ceiling: a module outside the test trees that a
    test imports and that starts the helper by path is not read. So is every
    helper when a test file cannot be read: it may be the one naming it.
    """
    names = {
        h: _helper_names(h)
        for h in helpers
        if str(PurePosixPath(h).parent) != "." and not _configured(h, read)
    }
    wanted = {n.lower() for words, dirs in names.values() for n in (*words, *dirs)}
    texts = _naming_texts(test_files, wanted, read)
    if texts is None:
        return {}
    files = sorted(test_files)
    return {
        h: [p for p in files if p != h and _reads(p, h, found, texts)] for h, found in names.items()
    }


def _naming_texts(
    test_files: Collection[str], wanted: Collection[str], read: Callable[[str], str | None]
) -> dict[str, str] | None:
    """``{test file: its code text}`` for each holding one of *wanted* or a listing;
    ``None`` when one cannot be read."""
    texts: dict[str, str] = {}
    for p in sorted(test_files):
        raw = read(p)
        if raw is None:
            return None
        text = raw.lower()
        if any(map(text.__contains__, wanted)) or _LISTING.search(text):
            texts[p] = code_text(p, text)
    return texts


def _reads(
    p: str, helper: str, found: tuple[tuple[str, ...], tuple[str, ...]], texts: Mapping[str, str]
) -> bool:
    """Whether test file *p* may reach *helper* (:func:`helper_readers`)."""
    from .test_selection import is_runnable_test

    parent = PurePosixPath(helper).parent
    near = parent.parent if str(parent.parent) != "." else parent
    if p.startswith(f"{near}/") and is_runnable_test(p):
        return True
    text = texts.get(p)
    if text is None:
        return False
    if p.startswith(f"{parent}/") and _LISTING.search(text):
        return True
    _, dirs = found
    return _names_helper(text, helper, reader=p) or any(_writes_dir(text, d) for d in dirs)


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
    """Whether a config at or above *helper*'s directory spells its file or its directory.

    A bare word does not count: ``client`` in an ``aiohttp.client`` warning filter
    or a project description names something else.
    """
    folder = PurePosixPath(helper).parent
    for d in (folder, *folder.parents):
        for name in _CONFIG_NAMES:
            text = read(name if str(d) == "." else f"{d}/{name}")
            if text and (
                _names_helper(text := text.lower(), helper, reader=None)
                or _spells_dir(text, folder)
            ):
                return True
    return False


#: Characters a written path is made of, up to the name it ends in.
_PATH_TAIL = re.compile(r"[\w.\-/\\@~#$]*$")
#: Characters a dotted module path is made of, up to the name it ends in.
_MODULE_TAIL = re.compile(r"[\w.]*$")
#: A written path holding at least one slash.
_PATH_TOKEN = re.compile(r"(?<![\w.\-@~#$])[\w.\-@~#$]*(?:[/\\][\w.\-@~#$]*)+")


def _names_helper(text: str, helper: str, reader: str | None) -> bool:
    """Whether lowercased *text* names *helper* by its file name or stem.

    A name counts when written as a path whose directories line up with the
    helper's (``autobahn/client/client.py``, ``../client``), or as a module path
    resolving to it (``tests.autobahn.client.client``, ``.client`` beside it). A
    different dotted name (``aiohttp.client``, ``self.client``) does not, unless
    its first name is an alias the text binds with ``as``. A bare stem may still be
    an import of this exact file (``from . import client``), so it counts for a
    test file *reader*; in a config (*reader* ``None``) only a bare file name does.
    """
    if reader and reader.endswith(".py"):  # tokenized by :func:`code_text`
        text = _join_dots(text)
    path = PurePosixPath(helper.lower())
    stem = path.name.split(".")[0]
    module = (*path.parts[:-1], stem)
    words = (path.name,) if stem in _ENTRY_STEMS else (path.name, stem)
    for word in dict.fromkeys(w for w in words if w):
        target = path.parts if word == path.name else module
        for m in re.finditer(rf"(?<![\w\-]){re.escape(word)}(?![\w\-])", text):
            before = text[max(0, m.start() - _WINDOW) : m.start()]
            if _refers(before, text, word, target, reader, word == path.name):
                return True
    return False


#: How far back a path or module path written before a name is read.
_WINDOW = 256


def _join_dots(text: str) -> str:
    """Python text :func:`code_text` tokenized, with its dotted names joined again.

    ``aiohttp . client`` reads ``aiohttp.client``; ``from . . client`` reads
    ``from ..client``, a relative import.
    """
    text = re.sub(r"\.\s+(?=[\w.])", ".", text)
    text = re.sub(r"([)\]])\s+(?=\.)", r"\1", text)
    return re.sub(
        r"\b(\w+)\s+(?=\.)", lambda m: m[0] if m[1] in ("from", "import") else m[1], text
    )


def _refers(
    before: str,
    text: str,
    word: str,
    target: tuple[str, ...],
    reader: str | None,
    file_name: bool,
) -> bool:
    """Whether *word*, written after *before* in *text*, refers to the file at *target*.

    *target* is the file's path parts, ending in *word*; *file_name* is whether
    *word* is the whole file name, which counts bare in a config too.
    """
    if before.endswith(("/", "\\")):
        written = _PATH_TAIL.search(before).group()  # type: ignore[union-attr]
        return _tail_matches((*_path_parts(written), word), target)
    if not before.endswith("."):
        return reader is not None or file_name
    chain = _MODULE_TAIL.search(before).group()  # type: ignore[union-attr]
    names = [n for n in chain.split(".") if n]
    dots = len(chain) - len(chain.lstrip("."))
    if not dots:
        if _tail_matches((*names, word), target):
            return True
        # ``h.client`` after ``import tests.autobahn.client as h``: an alias may be the package.
        return re.search(rf"\bas\s+{re.escape(names[0])}\b", text) is not None
    start = before[: len(before) - len(chain)]
    if start and not start[-1].isspace() and start[-1] not in "\"'`(":
        return False  # ``f().client``, ``x?.client``: an attribute, not a module path
    if reader is None:
        return True
    base = PurePosixPath(reader.lower()).parent
    for _ in range(dots - 1):
        base = base.parent
    return (*(p for p in base.parts if p != "."), *names, word) == target


def _path_parts(written: str) -> tuple[str, ...]:
    """The directory names of a *written* path, without ``.``, ``..`` or a leading alias."""
    parts = [c for c in re.split(r"[/\\]", written) if c not in ("", ".", "..")]
    if parts and not (parts[0][0].isalnum() or parts[0][0] == "_"):
        parts = parts[1:]  # an alias (``@/``, ``~/``, ``$lib/``) stands for some directory
    return tuple(parts)


def _tail_matches(parts: tuple[str, ...], target: tuple[str, ...]) -> bool:
    """Whether *parts* and *target* agree over their common trailing length."""
    k = min(len(parts), len(target))
    return k > 0 and parts[-k:] == target[-k:]


def _spells_dir(text: str, folder: PurePosixPath) -> bool:
    """Whether lowercased *text* writes a path ending in *folder*'s names.

    ``src/test/helpers``, or a relative ``./helpers`` (an alias target), does. A lone
    ``tests/`` in a glob or a test path list does not: it names where tests live.
    Ceiling: an alias target written as a lone name (tsconfig ``"helpers/*"``) is not
    read, so a test importing through that alias by another name stays unseen.
    """
    target = tuple(p.lower() for p in folder.parts)
    for m in _PATH_TOKEN.finditer(text):
        written = m.group()
        parts = _path_parts(written)
        if len(parts) >= (1 if written.startswith(".") else 2) and _tail_matches(parts, target):
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
