"""No fifteenth copy of "is this path a test?" (#1103).

An architecture check rather than a behaviour one: it fails when a new
test-path predicate appears anywhere outside :mod:`repowise.core.test_paths`.
Fourteen of these existed and disagreed on five real layouts, and two of them
carried docstrings claiming to mirror implementations they had already drifted
from, so a reviewer noticing the fifteenth is not a plan.

``_KNOWN`` holds the call sites that answer a *narrower* question on purpose,
each with its reason. The list only ever shrinks; adding a plain copy means this
test tells you to use the shared module instead.

Ceiling: this matches on names, so a predicate called something the lists below
do not know stays invisible — which is exactly how the two entries in ``_KNOWN``
went unnoticed until someone read for them. It catches the shapes that actually
recurred here, and a wrapper that calls the shared module is not one of them.
The corpus in ``test_test_paths.py`` is what catches a *wrong* answer; this only
catches a second implementation of the question.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import repowise.core.test_paths as test_paths

# Function-name shapes that answer this question. Matched on the definition, so
# a helper named for something else that happens to take a path is unaffected.
_PREDICATE_NAMES = (
    "_is_test_file",
    "_is_test_path",
    "_looks_like_test_path",
    "_is_test_file_name",
    "_is_test_dir_path",
    # Public spellings too. These shadow the shared module's own exported name,
    # which is the worst case for a wrong import, and listing only the private
    # forms is what kept the two in `_KNOWN` below invisible until review.
    "is_test_file",
    "is_test_path",
)

# Module-level constants holding test-path patterns. The `_TEST_PATH_TOKENS`
# tuple existed verbatim in three server modules at once, so a fix to one
# search path silently left the other two behind.
_PREDICATE_CONSTANTS = (
    "_TEST_PATH_TOKENS",
    "_TEST_PATH_RE",
    "_TEST_PATH_FRAGMENTS",
    "_TEST_FILE_SUFFIXES",
    "_TEST_FILE_PREFIXES",
    "_TEST_DIR_FRAGMENTS",
    "_TEST_SUFFIXES",
    "_TEST_DIRS",
    "_TEST_DIR_SEGMENTS",
    "_TEST_FILE_RE",
    "_TEST_FILE_PATTERNS",
)

# Copies that still exist, with the reason each is still here. Shrink, never
# grow — an entry needs the reason it is exempt written in the code it exempts.
#
# Both entries are deliberate divergences rather than unconverted copies: each
# answers a narrower question than the shared rules, with the reason in a
# comment above it. They became visible only when the public name shapes were
# added to `_PREDICATE_NAMES`, which is the point of recording them.
#
# Note what is deliberately *not* listed: `communities._is_test_node` and
# `move_method._file_is_test` read the flag off a graph node and fall back to the
# shared module. That is the pattern to copy, not a copy to remove.
_KNOWN: frozenset[str] = frozenset(
    {
        # Excludes bare `test/` on purpose: `django/test/` and `flask/testing.py`
        # are shipped test-*framework* code, and counting fixes to them would
        # inflate every downstream repo's defect count. Also exempts
        # config-shaped names, because fixing `vitest.config.ts` is a toolchain
        # fix rather than a defect.
        "packages/core/src/repowise/core/ingestion/git_indexer/fix_shape.py",
        # Excludes singular `test/`, `spec/` and `e2e/` on purpose: a route
        # handler under an OpenAPI `spec/` is a real service contract, and
        # over-excluding drops it from the workspace contract map entirely.
        "packages/core/src/repowise/core/workspace/extractors/base.py",
    }
)

_PACKAGES = pathlib.Path(__file__).resolve().parents[2] / "packages"

# The one place these are allowed to be defined.
_HOME = "packages/core/src/repowise/core/test_paths.py"


# The shared answers. A function that calls one of these is a wrapper around
# the single home, not a second implementation of it.
_SHARED = frozenset({"is_test_path", "is_test_support_path", "is_test_related_path"})


def _delegates(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether *fn* answers by calling the shared module rather than re-deriving.

    ``coverage.detector.is_test_file`` adds a source-content branch on top of
    the shared rules, and ``communities._is_test_node`` falls back to them. Both
    are the pattern to copy. Only a body that never reaches the shared module is
    a copy.
    """
    return any(
        (isinstance(node, ast.Name) and node.id in _SHARED)
        or (isinstance(node, ast.Attribute) and node.attr in _SHARED)
        for node in ast.walk(fn)
    )


def _offenders() -> dict[str, list[str]]:
    """Map of repo-relative path -> the predicate names/constants it defines."""
    found: dict[str, list[str]] = {}
    for path in _PACKAGES.rglob("*.py"):
        rel = path.relative_to(_PACKAGES.parents[0]).as_posix()
        if rel == _HOME:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # not ours to police
            continue
        hits: list[str] = []
        # Whole tree, not just module level: a copy tucked inside a class body or
        # nested in a function is still a copy.
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name in _PREDICATE_NAMES
                and not _delegates(node)
            ):
                hits.append(node.name)
            elif isinstance(node, ast.Assign):
                hits.extend(
                    t.id
                    for t in node.targets
                    if isinstance(t, ast.Name) and t.id in _PREDICATE_CONSTANTS
                )
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id in _PREDICATE_CONSTANTS
            ):
                hits.append(node.target.id)
        if hits:
            found[rel] = sorted(hits)
    return found


def test_no_new_test_path_predicates() -> None:
    offenders = {p: names for p, names in _offenders().items() if p not in _KNOWN}
    assert not offenders, (
        "New test-path predicate(s) outside repowise.core.test_paths:\n"
        + "\n".join(f"  {p}: {', '.join(names)}" for p, names in sorted(offenders.items()))
        + "\n\nImport is_test_path / is_test_support_path / is_test_related_path from"
        " repowise.core.test_paths instead. If a call site genuinely needs to differ,"
        " say why in the code and add it to _KNOWN in this file."
    )


def test_known_copies_still_exist() -> None:
    """Every allowlist entry must still be a real copy, so the list cannot rot.

    Without this, a converted call site leaves a stale exemption behind and the
    next copy added to that same file goes unnoticed.
    """
    found = _offenders()
    stale = sorted(p for p in _KNOWN if p not in found)
    assert not stale, (
        "These no longer define a test-path predicate — remove them from _KNOWN:\n"
        + "\n".join(f"  {p}" for p in stale)
    )


def test_a_wrapper_is_not_a_copy() -> None:
    """The check must not push call sites into re-deriving to dodge it."""
    wrapper = ast.parse(
        "def _is_test_path(p):\n"
        "    return is_test_related_path(p) or p.endswith('.fixture')\n"
    ).body[0]
    copy = ast.parse("def _is_test_path(p):\n    return '/tests/' in p\n").body[0]
    assert _delegates(wrapper)
    assert not _delegates(copy)


@pytest.mark.parametrize("name", ["is_test_path", "is_test_support_path", "is_test_related_path"])
def test_shared_module_exports_the_replacements(name: str) -> None:
    assert callable(getattr(test_paths, name))
