"""The name an Extract Method plan gives the helper it asks for: a placeholder.

The agent applying the plan names the helper for what it returns or does.
The placeholder (``extracted_helper`` in the language's casing, numbered when
taken where the helper lands) keeps the header and the call valid code until
then.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

from ..complexity.ast_utils import _find_function_entry_name
from ..dataflow.bindings import binding_target, bound_names, imported_names
from .naming import join_identifier

if TYPE_CHECKING:
    from ..complexity.languages import LanguageNodeMap
    from ..dataflow import FunctionAnalysis

# How each language that reaches this detector joins the words of a helper
# name. Only the languages the Extract Method slicer has a dialect for
# (``dataflow/dialects/__init__.py``) can appear here. C++ is deliberately
# absent: it has no single convention (the standard library is snake_case,
# Google style is PascalCase, Qt is camelCase), so it keeps the snake_case
# default rather than getting one answer that is wrong for most C++ repos.
NAME_CONVENTION: dict[str, str] = {
    "go": "camelCase",
    "java": "camelCase",
    "typescript": "camelCase",
    "tsx": "camelCase",
    "javascript": "camelCase",
    "jsx": "camelCase",
    "svelte": "camelCase",
    "vue": "camelCase",
}
_SNAKE_CASE = "snake_case"

_PLACEHOLDER_WORDS = ["extracted", "helper"]

# How far below a scope a sibling definition can sit: ``export const f = () =>``
# is four levels under a TS module, a decorated Python method three under its
# class. Deeper nodes are expressions, which a scan for names need not visit.
_SIBLING_DEPTH = 4


def placeholder_names(language: str | None) -> Iterator[str]:
    """``extracted_helper``, ``extracted_helper_2``, ... (``extractedHelper``,
    ``extractedHelper2`` where the language writes camelCase): the first one
    free where the helper lands is its name (:meth:`ScopeNames.claim_first`)."""
    convention = NAME_CONVENTION.get(language or "", _SNAKE_CASE)
    yield join_identifier(_PLACEHOLDER_WORDS, convention)
    for n in itertools.count(2):
        yield join_identifier([*_PLACEHOLDER_WORDS, str(n)], convention)


def _key(name: str) -> str:
    """Names collide regardless of a leading underscore or case: ``_compute_x``
    takes ``compute_x``, Go's ``ComputeX`` takes ``computeX``."""
    return name.lstrip("_").casefold()


class ScopeNames:
    """The names already taken where each helper lands, so a placeholder
    never shadows a sibling or another plan's helper.

    A helper lands where its host function sits: a method's class, a nested
    function's enclosing function, else the module. Taken there: the functions
    defined in it, the names it assigns, and at module level its imports; plus
    the host's own parameters and locals, which the helper's call sits among.
    A taken placeholder moves to the next number, and the first plan in source
    order keeps the lowest. Go's package scope spans files; only this file's
    names are seen.

    Names come from the tree, not ingestion symbols: ``RefactoringContext``
    carries no per-file symbol list, and the tree is already parsed.
    """

    def __init__(self, lmap: LanguageNodeMap) -> None:
        self._lmap = lmap
        self._taken: dict[int, set[str]] = {}
        self._imports: dict[int, frozenset[str]] = {}

    def imports(self, fn_node: Any) -> frozenset[str]:
        if fn_node is None:
            return frozenset()
        root = _root_of(fn_node)
        if root.id not in self._imports:
            self._imports[root.id] = imported_names(root)
        return self._imports[root.id]

    def claim(self, analysis: FunctionAnalysis, name: str | None) -> str | None:
        fn_node = analysis.fn_node
        if name is None or fn_node is None:
            return name
        scope = _enclosing_scope(fn_node, self._lmap)
        taken = self._taken.get(scope.id)
        if taken is None:
            taken = self._taken[scope.id] = self._scope_names(scope)
        key = _key(name)
        if key in taken or key in {_key(d.var) for d in analysis.def_use.definitions}:
            return None
        taken.add(key)
        return name

    def claim_first(self, analysis: FunctionAnalysis, names: Iterable[str]) -> str | None:
        """The first of *names* free in the helper's scope, claimed; ``None``
        when every one is taken (:func:`placeholder_names` never runs out)."""
        return next((name for name in names if self.claim(analysis, name)), None)

    def release(self, analysis: FunctionAnalysis, names: Iterable[str]) -> None:
        """Give back *names*, claimed for a plan that was not offered. Each was
        free when claimed, so none is a name the scope defines."""
        if analysis.fn_node is None:
            return
        taken = self._taken.get(_enclosing_scope(analysis.fn_node, self._lmap).id)
        if taken is not None:
            taken.difference_update(_key(name) for name in names)

    def _scope_names(self, scope: Any) -> set[str]:
        names = {_key(n) for n in defined_names(scope, self._lmap)}
        if scope.parent is None:
            names |= {_key(n) for n in self.imports(scope)}
        return names


def _root_of(node: Any) -> Any:
    while node.parent is not None:
        node = node.parent
    return node


def _enclosing_scope(fn_node: Any, lmap: LanguageNodeMap) -> Any:
    holders = lmap.class_kinds | lmap.function_kinds | lmap.lambda_kinds
    node = fn_node.parent
    while node is not None and node.parent is not None and node.type not in holders:
        node = node.parent
    return node if node is not None else fn_node


def defined_names(scope: Any, lmap: LanguageNodeMap) -> set[str]:
    """Functions defined and names assigned directly in *scope* (not in nested
    functions or classes)."""
    fn_kinds = lmap.function_kinds | lmap.lambda_kinds
    stop = fn_kinds | lmap.class_kinds
    names: set[str] = set()
    stack = [(child, 1) for child in scope.children]
    while stack:
        node, depth = stack.pop()
        if node.type in fn_kinds:
            names.add(_find_function_entry_name(node, lmap))
            continue
        target = binding_target(node, lmap)
        if target is not None:
            names.update(bound_names(target))
        if node.type in stop or depth >= _SIBLING_DEPTH:
            continue
        stack.extend((child, depth + 1) for child in node.children)
    return names
