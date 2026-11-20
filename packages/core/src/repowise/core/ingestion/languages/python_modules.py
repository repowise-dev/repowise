"""Python module-path ↔ file-path mapping (import semantics).

Maps a fully-qualified dotted module name (``pkg.sub.mod``) to the
repo-relative file that defines it, and vice versa, by deriving each
``.py`` file's *importable* dotted name from its package chain.

The mapping is purely structural — it reads a set of repo-relative POSIX
paths and the presence of ``__init__.py`` files, with no filesystem access
and no repo-specific assumptions. Because the dotted name is computed
relative to each file's *source root* (the first ancestor directory that is
not itself a package), the same logic resolves:

* **flat layouts** — ``foo/bar.py`` → ``foo.bar`` (root is the source root);
* **src layouts** — ``src/pkg/mod.py`` → ``pkg.mod``;
* **monorepo layouts** — ``packages/core/src/pkg/mod.py`` → ``pkg.mod``.

This is what lets absolute imports such as
``from repowise.core.providers.llm.base import BaseProvider`` resolve to
``packages/core/src/repowise/core/providers/llm/base.py`` — a path the naive
``{dotted}.py`` / ``src/{dotted}.py`` probes never reach.

Shared by the Python import resolver (:mod:`resolvers.python`) and the
Python dynamic-import hints extractor
(:mod:`dynamic_hints.python_imports`).
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import PurePosixPath

_PY_SUFFIXES = (".py", ".pyi")

# Stdlib top-level names, independent of the indexer's own Python: the running
# interpreter's set drops modules removed in 3.12/3.13 that older code imports.
STDLIB_NAMES = sys.stdlib_module_names | {
    "asynchat", "asyncore", "distutils", "imp", "smtpd",  # removed in 3.12
    "aifc", "audioop", "cgi", "cgitb", "chunk", "crypt", "imghdr", "mailcap",
    "msilib", "nis", "nntplib", "ossaudiodev", "pipes", "sndhdr", "spwd",
    "sunau", "telnetlib", "uu", "xdrlib", "lib2to3",  # removed in 3.13
}  # fmt: skip

# Path segments that mark a file as a low-value resolution target. Mirrors
# the stem-disambiguation policy in ``graph/_stem.py``: a loose module under
# ``tests/`` / ``fixtures/`` / ``examples/`` that happens to derive the same
# dotted name as a real package module must never win the mapping. Kept local
# (rather than imported from ``graph``) to avoid an import cycle — the graph
# package depends on this module through the resolver.
_LOW_VALUE_PATH_SEGMENTS = frozenset(
    {
        "tests",
        "test",
        "_tests",
        "__tests__",
        "testing",
        "test_apps",
        "testdata",
        "test_data",
        "fixtures",
        "examples",
        "example",
        "samples",
        "sample",
        "scripts",
        "benchmarks",
        "bench",
        "docs",
        "doc",
    }
)


def _index_priority(path: str) -> tuple[int, int, str]:
    """Sort key for choosing among files that derive the same dotted name.

    Lower sorts first: prefer non-test/non-fixture paths, then shallower
    paths, then a stable lexicographic tiebreak.
    """
    parts = path.split("/")
    low_value = 1 if any(seg in _LOW_VALUE_PATH_SEGMENTS for seg in parts) else 0
    return (low_value, len(parts), path)


def _is_package_dir(dir_posix: str, path_set: frozenset[str] | set[str]) -> bool:
    """True if *dir_posix* contains an ``__init__.py`` (i.e. is a package)."""
    init = f"{dir_posix}/__init__.py" if dir_posix not in ("", ".") else "__init__.py"
    return init in path_set


def module_parts(path: str) -> tuple[str, ...]:
    """The dotted-path segments of a Python file from the repository root (a package for ``__init__``).

    Every tail of the result is a name the file may be imported under; which
    one depends on the source root, which this does not decide.
    """
    parts = PurePosixPath(path).with_suffix("").parts
    return parts[:-1] if parts and parts[-1] == "__init__" else parts


def dotted_module_for(path: str, path_set: frozenset[str] | set[str]) -> str | None:
    """Return the dotted module name *path* is importable as, or ``None``.

    Two strategies, in order:

    1. **``src`` source root.** If the path contains a ``src`` directory that
       is not itself a package, everything below the last such ``src`` is the
       module path. This is the standard ``src``-layout and the monorepo
       ``packages/*/src`` layout, and — crucially — it also resolves PEP 420
       *namespace packages* (a top-level package dir with no ``__init__.py``,
       as used to share one import namespace across several distributions),
       which the ``__init__``-chain alone cannot see.

    2. **Regular-package chain.** Otherwise, the file must be a genuine package
       member (an ``__init__.py``, or living in a directory that has one); the
       name is the chain of ``__init__``-bearing directories down to the file.

    Loose modules in non-package directories with no ``src`` root (e.g.
    ``vendor/util.py``) return ``None`` on purpose — they are not reliably
    importable by a fully-qualified dotted path, and resolving them is the job
    of the flat-/src-layout candidates and the stem-map heuristics (which
    encode richer disambiguation: parent-dir match, test-fixture demotion).
    Claiming them here would override those with a weaker guess.
    """
    p = PurePosixPath(path)
    if p.suffix not in _PY_SUFFIXES:
        return None

    is_init = p.name in ("__init__.py", "__init__.pyi")
    dir_parts = list(p.parts[:-1])

    # Strategy 1 — strip up to and including the last real ``src`` root.
    for i in range(len(dir_parts) - 1, -1, -1):
        if dir_parts[i] != "src":
            continue
        src_dir = "/".join(dir_parts[: i + 1])
        if _is_package_dir(src_dir, path_set):
            continue  # a package literally named ``src`` — not a source root
        comps = dir_parts[i + 1 :] + ([] if is_init else [p.stem])
        return ".".join(comps) if comps else None

    # Strategy 2 — regular-package ``__init__`` chain.
    if not is_init and not _is_package_dir(p.parent.as_posix(), path_set):
        return None
    parts: list[str] = [] if is_init else [p.stem]
    cur = p.parent
    while str(cur) not in ("", ".") and _is_package_dir(cur.as_posix(), path_set):
        parts.append(cur.name)
        cur = cur.parent
    if not parts:
        return None
    parts.reverse()
    return ".".join(parts)


def source_roots(path_set: frozenset[str] | set[str]) -> set[str]:
    """Directories a Python import root sits at, as the package layout shows.

    The directory above each top-level regular package (one with an
    ``__init__.py`` whose parent has none), and every ``src`` directory that
    is not itself a package. ``""`` is the repository root.
    """
    roots: set[str] = set()
    for path in path_set:
        p = PurePosixPath(path)
        if p.name != "__init__.py":
            continue
        parent = p.parent.parent.as_posix()
        parent = "" if parent == "." else parent
        if not _is_package_dir(parent, path_set):
            roots.add(parent)
        for i, part in enumerate(p.parts[:-1]):
            if part == "src":
                src = "/".join(p.parts[: i + 1])
                if not _is_package_dir(src, path_set):
                    roots.add(src)
    return roots


def _namespace_names(path_set: frozenset[str] | set[str]) -> dict[str, str]:
    """PEP 420 names: each Python file named from every source root above it.

    A module in a directory with no ``__init__.py`` (``backend/middleware/jwt.py``)
    is still importable as ``middleware.jwt`` when ``backend`` is an import root,
    and ``backend/scripts/tools/x.py`` in a regular package ``tools`` is also
    ``scripts.tools.x`` from that root. A bare top-level name is left to the
    resolver, which knows the importer's own roots. A name whose first segment
    is a stdlib module is skipped: an inferred root never shadows the stdlib.
    """
    roots = source_roots(path_set)
    names: dict[str, str] = {}
    for path in path_set:
        if PurePosixPath(path).suffix not in _PY_SUFFIXES:
            continue
        parts = module_parts(path)
        regular = dotted_module_for(path, path_set)
        for depth in range(len(parts) - 1):
            if "/".join(parts[:depth]) not in roots:
                continue
            comps = parts[depth:]
            if comps[0] in STDLIB_NAMES or not all(c.isidentifier() for c in comps):
                continue
            dotted = ".".join(comps)
            if dotted == regular:
                continue
            current = names.get(dotted)
            if current is None or _index_priority(path) < _index_priority(current):
                names[dotted] = path
    return names


def build_python_module_index(paths: Iterable[str]) -> dict[str, str]:
    """Map dotted module name → repo-relative file path for every Python file.

    When two files derive the same dotted name (a real package module and a
    like-named loose module under ``tests/`` / ``fixtures/``, or namespace
    packages split across roots) the higher-priority path wins
    deterministically — see :func:`_index_priority` — so resolution never
    depends on iteration order. A regular-package name always beats a
    namespace-package name (:func:`_namespace_names`), and is listed after
    it, so a reverse map built by iterating the index keeps the regular name.
    """
    path_set = paths if isinstance(paths, (set, frozenset)) else set(paths)
    index: dict[str, str] = {}
    for p in path_set:
        dotted = dotted_module_for(p, path_set)
        if not dotted:
            continue
        current = index.get(dotted)
        if current is None or _index_priority(p) < _index_priority(current):
            index[dotted] = p
    namespace = {k: v for k, v in _namespace_names(path_set).items() if k not in index}
    return {**namespace, **index}
