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

from collections.abc import Iterable
from pathlib import PurePosixPath

_PY_SUFFIXES = (".py", ".pyi")

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


def build_python_module_index(paths: Iterable[str]) -> dict[str, str]:
    """Map dotted module name → repo-relative file path for every Python file.

    When two files derive the same dotted name (a real package module and a
    like-named loose module under ``tests/`` / ``fixtures/``, or namespace
    packages split across roots) the higher-priority path wins
    deterministically — see :func:`_index_priority` — so resolution never
    depends on iteration order.
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
    return index
