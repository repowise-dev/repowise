"""No third or fourth copy of "is this file documentation?" (#1489).

An architecture check rather than a behaviour one: it fails when a new
hand-written doc-extensions set or constant appears anywhere outside
:mod:`repowise.core.support_paths`.

``_KNOWN`` holds the call sites that answer a narrower question on purpose,
each with its reason. The list only ever shrinks; adding a plain copy means this
test tells you to use `repowise.core.support_paths.DOC_EXTENSIONS` instead.

Ceiling: this matches on constant names and literal set/tuple/list shapes
containing documentation file extension subsets (e.g. .md, .rst, .adoc, .mdx),
so a dynamically computed collection or custom parser check that does not match
these patterns stays invisible. It catches the shapes that actually recurred across
categories, fix_shape, and knowledge_graph.
"""

from __future__ import annotations

import ast
import pathlib

from repowise.core.analysis.knowledge_graph import _DOC_EXTENSIONS as KG_DOC_EXTENSIONS
from repowise.core.generation.categories import _DOC_SUFFIXES
from repowise.core.ingestion.git_indexer.fix_shape import _DOC_EXT
from repowise.core.support_paths import DOC_EXTENSIONS

# Constant name shapes that define documentation extension sets.
_DOC_CONSTANTS = (
    "DOC_EXTENSIONS",
    "_DOC_EXTENSIONS",
    "_DOC_EXT",
    "_DOC_SUFFIXES",
    "_DOC_EXTS",
)

# Canonical doc extension markers that distinguish general documentation sets
# from language parser specs (e.g. .adoc / .rst / .txt alongside .md).
_DOC_MARKERS = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})

# Call sites that answer a narrower question on purpose, with reasons.
_KNOWN: frozenset[str] = frozenset()

_PACKAGES = pathlib.Path(__file__).resolve().parents[2] / "packages"
_HOME = "packages/core/src/repowise/core/support_paths.py"


def _extract_literals(node: ast.AST) -> set[str]:
    """Extract string constant elements from set, tuple, list, or Call literals (e.g. frozenset({...}))."""
    if isinstance(node, ast.Tuple | ast.Set | ast.List):
        return {
            elt.value
            for elt in node.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        }
    if isinstance(node, ast.Call):
        # e.g. frozenset({".md", ...}) or set([".md", ...])
        for arg in node.args:
            literals = _extract_literals(arg)
            if literals:
                return literals
    return set()


def _is_doc_constant_target(target: ast.AST) -> bool:
    return isinstance(target, ast.Name) and target.id in _DOC_CONSTANTS


def _check_assignment(node: ast.Assign | ast.AnnAssign) -> list[str]:
    """Check if an assignment creates a hand-written doc-extensions copy."""
    targets: list[ast.AST] = node.targets if isinstance(node, ast.Assign) else [node.target]

    named_constant = any(_is_doc_constant_target(t) for t in targets)
    literals = _extract_literals(node.value) if node.value is not None else set()

    hits: list[str] = []
    # 1. Named constant assigned to a literal collection rather than referencing DOC_EXTENSIONS
    if named_constant and literals:
        hits.append(f"constant assigned literal {sorted(literals)}")

    # 2. Literal collection containing 4+ doc markers or .adoc with doc extensions
    matched = literals & _DOC_MARKERS
    if (len(matched) >= 4 or (len(matched) >= 3 and ".adoc" in matched)) and not hits:
        hits.append(f"literal doc extensions {sorted(matched)}")

    return hits


def _analyze_file(path: pathlib.Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []

    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            hits.extend(_check_assignment(node))
    return sorted(set(hits))


def _offenders() -> dict[str, list[str]]:
    """Map of repo-relative path -> the forbidden doc-extension copies it defines."""
    found: dict[str, list[str]] = {}
    for path in _PACKAGES.rglob("*.py"):
        rel = path.relative_to(_PACKAGES.parents[0]).as_posix()
        if rel == _HOME:
            continue
        hits = _analyze_file(path)
        if hits:
            found[rel] = hits
    return found


def test_no_new_doc_extension_copies() -> None:
    offenders = {p: hits for p, hits in _offenders().items() if p not in _KNOWN}
    assert not offenders, (
        "New hand-written doc-extensions definition(s) outside support_paths.py:\n"
        + "\n".join(f"  {p}: {', '.join(hits)}" for p, hits in sorted(offenders.items()))
        + "\n\nImport DOC_EXTENSIONS from repowise.core.support_paths instead."
    )


def test_known_copies_still_exist() -> None:
    """Every allowlist entry must still be a real copy, so the list cannot rot."""
    found = _offenders()
    stale = sorted(p for p in _KNOWN if p not in found)
    assert not stale, (
        "These no longer define a doc-extension copy — remove them from _KNOWN:\n"
        + "\n".join(f"  {p}" for p in stale)
    )


def test_shared_constant_exports_expected_extensions() -> None:
    """Verify canonical DOC_EXTENSIONS contains the full set including .mdx."""
    assert DOC_EXTENSIONS == _DOC_MARKERS


def test_subsystem_constants_alias_canonical_constant() -> None:
    """Verify that categories, fix_shape, and knowledge_graph all alias the exact same constant."""
    assert _DOC_SUFFIXES is DOC_EXTENSIONS
    assert _DOC_EXT is DOC_EXTENSIONS
    assert KG_DOC_EXTENSIONS is DOC_EXTENSIONS
