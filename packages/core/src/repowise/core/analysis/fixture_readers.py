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

import functools
import io
import posixpath
import re
import tokenize
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
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
    out: dict[str, list[str]] = {h: [] for h in names}
    pairs = _ReaderIndex(names)
    for p in sorted(test_files):  # file by file, so its per-text reads are cached once
        text = texts.get(p)
        runs = None if text is None else _run_starts(text)
        for h in pairs.helpers_for(p, text, runs):
            if p != h and _reads(p, h, names[h], texts, runs):
                out[h].append(p)
    return out


#: A run of the characters a name's edges are told apart by in :func:`_names_helper`
#: and :func:`_writes_dir`.
_RUN = re.compile(r"[\w\-]+")


class _ReaderIndex:
    """Which helpers a test file can reach at all, so :func:`_reads` runs only for those.

    Checking every test file against every helper is tests x helpers regex scans
    over whole texts, which dominated indexing on a large suite. A pair is skipped
    only where :func:`_reads` must be false: the file is not under the helper's
    directory or the one above, and its text lacks one of the ``[\\w-]`` runs of
    every name the helper is written by. Each run of a name is bounded by the
    name's own separators or by the match's ``(?<![\\w\\-])``/``(?![\\w\\-])``
    edges, so a text naming it holds each run whole.
    """

    def __init__(self, names: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]]) -> None:
        self.near: dict[str, list[str]] = {}
        self.parent: dict[str, list[str]] = {}
        self.by_run: dict[str, list[tuple[frozenset[str], str]]] = {}
        self.always: list[str] = []
        for h, (_, dirs) in names.items():
            parent, near = _helper_dirs(h)
            self.near.setdefault(str(near), []).append(h)
            self.parent.setdefault(str(parent), []).append(h)
            # The words as _names_helper and _writes_dir spell them: ``ΟΣ.py`` lowercased
            # whole is ``οσ.py``, its stem alone ``ος``.
            for name in {*_named_by(h)[2], *(d.lower() for d in dirs)}:
                runs = _RUN.findall(name)
                if runs:
                    self.by_run.setdefault(runs[0], []).append((frozenset(runs), h))
                else:
                    self.always.append(h)

    def helpers_for(
        self, p: str, text: str | None, runs: Mapping[str, list[int]] | None
    ) -> set[str]:
        """The helpers test file *p* (code *text* and its :func:`_run_starts` *runs*,
        ``None`` when unread) may reach."""
        from .test_selection import is_runnable_test

        dirs = [p[:i] for i, c in enumerate(p) if c == "/"]
        found: set[str] = set()
        near = [h for d in dirs for h in self.near.get(d, ())]
        if near and is_runnable_test(p):
            found.update(near)
        if text is None or runs is None:
            return found
        beside = [h for d in dirs for h in self.parent.get(d, ())]
        if beside and _LISTING.search(text):
            found.update(beside)
        found.update(self.always)
        for run in runs:
            for needed, h in self.by_run.get(run, ()):
                if all(r in runs for r in needed):
                    found.add(h)
        return found


def _naming_texts(
    test_files: Collection[str], wanted: Collection[str], read: Callable[[str], str | None]
) -> dict[str, str] | None:
    """``{test file: its code text}`` for each holding one of *wanted* or a listing;
    ``None`` when one cannot be read. Python's dotted names are joined again."""
    texts: dict[str, str] = {}
    for p in sorted(test_files):
        raw = read(p)
        if raw is None:
            return None
        text = raw.lower()
        if any(map(text.__contains__, wanted)) or _LISTING.search(text):
            code = code_text(p, text)
            texts[p] = _join_dots(code) if p.endswith(".py") else code
    return texts


def _reads(
    p: str,
    helper: str,
    found: tuple[tuple[str, ...], tuple[str, ...]],
    texts: Mapping[str, str],
    runs: Mapping[str, list[int]] | None = None,
) -> bool:
    """Whether test file *p* (its text's :func:`_run_starts` *runs*, if held) may
    reach *helper* (:func:`helper_readers`)."""
    from .test_selection import is_runnable_test

    parent, near = _helper_dirs(helper)
    if p.startswith(f"{near}/") and is_runnable_test(p):
        return True
    text = texts.get(p)
    if text is None:
        return False
    if p.startswith(f"{parent}/") and _LISTING.search(text):
        return True
    _, dirs = found
    return _names_helper(text, helper, reader=p, runs=runs) or any(
        _writes_dir(text, d, runs) for d in dirs
    )


@functools.lru_cache(maxsize=4096)
def _helper_dirs(helper: str) -> tuple[PurePosixPath, PurePosixPath]:
    """*helper*'s directory and the one above it (its own directory at the top level)."""
    parent = PurePosixPath(helper).parent
    return parent, parent.parent if str(parent.parent) != "." else parent


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
                _names_helper(text := text.lower(), helper, reader=None, at=d)
                or _spells_dir(text, folder, d)
            ):
                return True
    return False


#: Characters a written path is made of, up to the name it ends in.
_PATH_TAIL = re.compile(r"[\w.\-/\\@~#$]*$")
#: Characters a dotted module path is made of, up to the name it ends in.
_MODULE_TAIL = re.compile(r"[\w.]*$")
#: A written path holding at least one slash.
_PATH_TOKEN = re.compile(r"(?<![\w.\-@~#$])[\w.\-@~#$]*(?:[/\\][\w.\-@~#$]*)+")


def _names_helper(
    text: str,
    helper: str,
    reader: str | None,
    at: PurePosixPath | None = None,
    runs: Mapping[str, list[int]] | None = None,
) -> bool:
    """Whether lowercased *text* names *helper* by its file name or stem.

    A name counts when written as a path whose directories line up with the
    helper's (``autobahn/client/client.py``, ``../client``), or as a module path
    resolving to it (``tests.autobahn.client.client``, ``.client`` beside it). A
    different dotted name (``aiohttp.client``, ``self.client``) does not, unless
    its first name is an alias the text binds with ``as``. A bare stem may still be
    an import of this exact file (``from . import client``), so it counts for a
    test file *reader*; in a config (*reader* ``None``, in directory *at*) only a bare
    file name does, and a relative path counts when it resolves to the helper from *at*.
    *runs* is the text's :func:`_run_starts` when the caller holds it.
    """
    path, stem, words = _named_by(helper)
    module = (*path.parts[:-1], stem)
    for word in words:
        target = path.parts if word == path.name else module
        for start in _word_starts(text, word, runs):
            before = text[max(0, start - _WINDOW) : start]
            if before.endswith(("/", "\\")):
                found = _path_refers(before, word, target, at)
            elif before.endswith("."):
                found = _module_refers(before, text, word, target, reader)
            else:  # a bare word, or a file name in a config
                found = reader is not None or (word == path.name and "." in word)
            if found:
                return True
    return False


def _word_starts(
    text: str, word: str, runs: Mapping[str, list[int]] | None = None
) -> Iterator[int]:
    """Where *word* stands alone in *text*, in order, as ``re.finditer`` finds it (no overlaps).

    With *runs*, the text's :func:`_run_starts`, they are read off it: the many
    helpers asking one text about a common name (``models``, ``tests``) each pay
    a lookup, not a scan.
    """
    first = _RUN.match(word)
    if runs is None or first is None:
        for m in re.finditer(rf"(?<![\w\-]){re.escape(word)}(?![\w\-])", text):
            yield m.start()
        return
    resume = 0
    for start in _alone(text, word, runs.get(first.group(), ())):
        if start >= resume:
            yield start
            resume = start + len(word)


def _alone(text: str, word: str, starts: Iterable[int]) -> Iterator[int]:
    """Those of *starts*, where *word*'s first ``[\\w-]`` run opens a run of *text*,
    at which *word* stands alone: no ``[\\w-]`` on either side, overlaps included.

    Where the word stands alone its first run is a whole run of the text, so no
    other start can hold it.
    """
    end = len(word)
    return (s for s in starts if text.startswith(word, s) and not _RUN.match(text, s + end))


def _run_starts(text: str) -> dict[str, list[int]]:
    """``{[\\w-] run: where it starts}`` for every maximal run in *text*."""
    out: dict[str, list[int]] = {}
    for m in _RUN.finditer(text):
        out.setdefault(m.group(), []).append(m.start())
    return out


def _named_by(helper: str) -> tuple[PurePosixPath, str, tuple[str, ...]]:
    """*helper*'s lowercased path, stem, and the words :func:`_names_helper` looks for.

    The whole path is lowercased before it is split, as a name in a text is.
    """
    path = PurePosixPath(helper.lower())
    stem = path.name.split(".")[0]
    words = (path.name,) if stem in _ENTRY_STEMS else (path.name, stem)
    return path, stem, tuple(dict.fromkeys(w for w in words if w))


#: How far back a path or module path written before a name is read.
_WINDOW = 256


def _join_dots(text: str) -> str:
    """Python text :func:`code_text` tokenized, with its dotted names joined again.

    ``aiohttp . client`` reads ``aiohttp.client``; ``from . . client`` reads
    ``from ..client``, a relative import.
    """
    pieces = _STRING.split(text)
    for i in range(0, len(pieces), 2):  # the code between string literals, which stay as written
        code = re.sub(r"\.\s+(?=[\w.])", ".", pieces[i])
        code = re.sub(r"([)\]])\s+(?=\.)", r"\1", code)
        pieces[i] = re.sub(
            r"\b(\w+)\s+(?=\.)", lambda m: m[0] if m[1] in ("from", "import") else m[1], code
        )
    return "".join(pieces)


#: A one-line string literal: its text is as written, not tokens joined by spaces.
_STRING = re.compile(r"""("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')""")


@functools.lru_cache(maxsize=8)
def _aliases(text: str) -> frozenset[str]:
    """The names *text* binds with ``as``."""
    return frozenset(re.findall(r"\bas\s+(\w+)", text))


def _path_refers(
    before: str, word: str, target: tuple[str, ...], at: PurePosixPath | None
) -> bool:
    """Whether *word*, ending the path written at the end of *before*, is the file at *target*.

    In a config in directory *at*, a relative path resolves from there.
    """
    written = _PATH_TAIL.findall(before)[0].replace("\\", "/")
    if at is not None and written.startswith("."):
        return posixpath.normpath(f"{at}/{written}{word}".lower()) == "/".join(target)
    return _tail_matches((*_path_parts(written), word), target)


def _module_refers(
    before: str, text: str, word: str, target: tuple[str, ...], reader: str | None
) -> bool:
    """Whether *word*, ending the dotted name at the end of *before*, is the module at *target*."""
    chain = _MODULE_TAIL.findall(before)[0]
    names = [n for n in chain.split(".") if n]
    dots = len(chain) - len(chain.lstrip("."))
    if not dots:
        # ``h.client`` after ``import tests.autobahn.client as h``: an alias may be the package.
        return _tail_matches((*names, word), target) or names[0] in _aliases(text)
    start = before[: len(before) - len(chain)]
    if start and not start[-1].isspace() and start[-1] not in "\"'`(":
        return False  # ``f().client``, ``x?.client``: an attribute, not a module path
    if reader is None:
        return False  # no runner reads a module path relative to its config
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


def _spells_dir(text: str, folder: PurePosixPath, at: PurePosixPath) -> bool:
    """Whether lowercased *text*, a config in directory *at*, writes *folder* as a path.

    ``src/test/helpers``, or a relative ``./test/helpers`` resolving to it from *at*
    (an alias target), does. A lone ``tests/`` in a glob or a test path list does
    not: it names where tests live. A path shorter than *folder* matches on its
    trailing names, as it may be written from a root config or a base directory.
    Ceiling: an alias target written as a lone name (tsconfig ``"helpers/*"``) is not
    read, so a test importing through that alias by another name stays unseen.
    """
    target = tuple(p.lower() for p in folder.parts)
    for m in _PATH_TOKEN.finditer(text):
        written = m.group().replace("\\", "/")
        if written.startswith("."):
            if posixpath.normpath(f"{at}/{written}".lower()).strip("/") == "/".join(target):
                return True
        elif len(parts := _path_parts(written)) >= 2 and _tail_matches(parts, target):
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


def _writes_dir(text: str, name: str, runs: Mapping[str, list[int]] | None = None) -> bool:
    """Whether *text* writes directory *name* as a path: next to a quote or a slash.

    ``from typing import`` names a module, ``"typing"`` or ``tests/typing/`` a directory.
    *runs* is the text's :func:`_run_starts` when the caller holds it.
    """
    if not name:
        return False
    first = _RUN.match(name := name.lower())
    if runs is None or first is None:
        n = re.escape(name)
        return re.search(rf"[\"'/\\]{n}(?![\w\-])|(?<![\w\-]){n}[\"'/\\]", text) is not None
    end = len(name)
    return any(
        (s > 0 and text[s - 1] in _PATH_EDGES) or text[s + end : s + end + 1] in _PATH_EDGES
        for s in _alone(text, name, runs.get(first.group(), ()))
    )


#: The characters next to which a written name is a path (:func:`_writes_dir`).
_PATH_EDGES = frozenset("\"'/\\")


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
