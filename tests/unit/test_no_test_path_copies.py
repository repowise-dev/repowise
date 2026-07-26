"""No thirteenth copy of "is this path a test?" (#1103).

An architecture check rather than a behaviour one: it fails when a new private
test-path predicate appears anywhere outside :mod:`repowise.core.test_paths`.
Twelve of these existed and disagreed on five real layouts, and two of them
carried docstrings claiming to mirror implementations they had already drifted
from, so a reviewer noticing the thirteenth is not a plan.

The remaining entries in ``_KNOWN`` are the ones still to be converted. The list
only ever shrinks: deleting a copy means deleting its line here, and adding one
means this test tells you to use the shared module instead.
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
)

# Copies that still exist, with the reason each is still here. Shrink, never grow.
#
# Note what is deliberately *not* listed: `communities._is_test_node` and
# `move_method._file_is_test` read the flag off a graph node and fall back to the
# shared module. That is the pattern to copy, not a copy to remove.
_KNOWN: frozenset[str] = frozenset(
    {
        # The MCP tools and the stats router, converted next. Each re-derives
        # from a substring token list weaker than the shared rules.
        "packages/server/src/repowise/server/mcp_server/tool_search.py",
        "packages/server/src/repowise/server/mcp_server/tool_search_symbols.py",
        "packages/server/src/repowise/server/mcp_server/_answer_pipeline.py",
        "packages/server/src/repowise/server/routers/stats.py",
    }
)

_PACKAGES = pathlib.Path(__file__).resolve().parents[2] / "packages"

# The one place these are allowed to be defined.
_HOME = "packages/core/src/repowise/core/test_paths.py"


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
        for node in tree.body:  # module level only
            if isinstance(node, ast.FunctionDef) and node.name in _PREDICATE_NAMES:
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


@pytest.mark.parametrize("name", ["is_test_path", "is_test_support_path", "is_test_related_path"])
def test_shared_module_exports_the_replacements(name: str) -> None:
    assert callable(getattr(test_paths, name))
