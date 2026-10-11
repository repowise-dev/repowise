"""The starting name an Extract Method plan gives the helper it asks you to write.

Sources, in order: a ``timed(..., "label")`` stage label on the span, a banner
comment over it (the author's own name for the block), and the span's single
OUT value as ``compute_<out>``. An OUT name describes a value, not an action,
so it is never given to a span that acts outside itself (``dataflow.effects``);
nor is a banner that opens with a value verb (``_VALUE_VERBS``). Whatever the
source, a name a symbol in the helper's scope already uses is dropped. ``None``
is the honest answer when nothing anchors a name, and every surface asks the
reader to name the helper then.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..complexity.ast_utils import _find_function_entry_name
from ..dataflow.effects import (
    Ownership,
    binding_target,
    bound_names,
    declared_names,
    has_outside_effects,
    imported_names,
    owned_names,
)
from ..dataflow.span import enclosing_with, section_heads, span_statements
from .naming import banner_words, join_identifier, label_words, split_words

if TYPE_CHECKING:
    from ..complexity.languages import LanguageNodeMap
    from ..dataflow import Extraction, FunctionAnalysis

# OUT values whose name describes the variable's role, not the block's
# product: ``compute_result`` names nothing the reader did not know.
_UNINFORMATIVE_OUT = frozenset(
    {"out", "result", "results", "value", "values", "ret", "tmp", "temp", "data", "item"}
)

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

# Verbs that promise a value. A banner opening with one over a span that acts
# outside itself names it the way an OUT name would, wrongly.
_VALUE_VERBS = frozenset({"compute", "calculate", "calc", "derive", "get", "build", "make"})

# How far below a scope a sibling definition can sit: ``export const f = () =>``
# is four levels under a TS module, a decorated Python method three under its
# class. Deeper nodes are expressions, which a scan for names need not visit.
_SIBLING_DEPTH = 4


def out_value_name(
    analysis: FunctionAnalysis, extraction: Extraction, language: str | None = None
) -> str | None:
    """``compute_<out>`` for the slice's single informative OUT value, in
    *language*'s identifier convention; ``None`` without one.

    A span whose single product is ``average`` is, by construction, the code
    that computes it, so ``compute_average`` describes it without inferring
    intent. Convention is per language: Python, Rust and C++ keep
    ``compute_average``; Go, Java and the TypeScript/JavaScript family take
    ``computeAverage``. The out value's own casing is kept as word boundaries,
    so ``meanValue`` is ``computeMeanValue`` in Java, not ``compute_meanvalue``.
    """
    if len(extraction.returns) != 1:
        return None
    out_words = split_words(extraction.returns[0])
    if not out_words:
        return None
    # ``_UNINFORMATIVE_OUT`` is keyed on the single-word slug, matching the
    # names it holds (``meanValue`` is a product, ``result`` is a role).
    if "_".join(out_words) in _UNINFORMATIVE_OUT:
        return None
    return join_identifier(["compute", *out_words], _convention(language))


def helper_name(
    analysis: FunctionAnalysis,
    extraction: Extraction,
    lmap: LanguageNodeMap,
    language: str | None,
    modules: frozenset[str] = frozenset(),
) -> str | None:
    """A deterministic starting name for the helper, before the collision
    check, or ``None`` when nothing in the code anchors one. *modules* are the
    file's imported names (:func:`dataflow.effects.imported_names`)."""
    stmts = span_statements(analysis.fn_node, extraction.start_line, extraction.end_line, lmap)
    if not stmts:
        return None
    words = _anchored_words(stmts, lmap)
    own = Ownership(
        owned=owned_names(stmts, lmap),
        declared=declared_names(analysis.fn_node, analysis.def_use, lmap),
        modules=modules,
    )
    effects = extraction.needs_async or has_outside_effects(stmts, own, lmap)
    if words and not (effects and words[0] in _VALUE_VERBS):
        return join_identifier(words, _convention(language))
    return None if effects else out_value_name(analysis, extraction, language)


def _anchored_words(stmts: list[Any], lmap: LanguageNodeMap) -> list[str]:
    """The words of the label or banner naming the span, ``[]`` without one.

    The span may be the whole body of ``with timed(...)``; else the label or
    banner heads its first statement. A label or banner names only its own
    section, so one heading a later top-level statement means the span runs
    across sections, and neither names it.
    """
    wrapper = enclosing_with(stmts, lmap)
    if wrapper is not None and (label := _label(wrapper)):
        return label
    heads = [_label(stmt) or banner_words(comment) for stmt, comment in section_heads(stmts)]
    # A later head means a second section starts inside the span.
    return heads[0] if heads and not any(heads[1:]) else []


def _label(stmt: Any) -> list[str]:
    return label_words((stmt.text or b"").decode("utf-8", "replace"))


def _convention(language: str | None) -> str:
    return NAME_CONVENTION.get(language or "", _SNAKE_CASE)


def _key(name: str) -> str:
    """Names collide regardless of a leading underscore or case: ``_compute_x``
    takes ``compute_x``, Go's ``ComputeX`` takes ``computeX``."""
    return name.lstrip("_").casefold()


class ScopeNames:
    """The names already taken where each helper lands, so a suggested name
    never shadows a sibling or another plan's helper.

    A helper lands where its host function sits: a method's class, a nested
    function's enclosing function, else the module. Taken there: the functions
    defined in it, the names it assigns, and at module level its imports; plus
    the host's own parameters and locals, which the helper's call sits among.
    A colliding name is dropped rather than suffixed (``compute_total_2`` says
    nothing the reader can use), and the first plan in source order keeps it.
    Go's package scope spans files; only this file's names are seen.

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

    def _scope_names(self, scope: Any) -> set[str]:
        names = {_key(n) for n in _defined_names(scope, self._lmap)}
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


def _defined_names(scope: Any, lmap: LanguageNodeMap) -> set[str]:
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
