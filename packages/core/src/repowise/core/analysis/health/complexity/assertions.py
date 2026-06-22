"""Assertion-block detection (test-quality smells).

Finds runs of ≥2 consecutive assertion statements within a function body,
recorded as ``(start_line, end_line, count)``. Opt-in per language via the
``LanguageNodeMap`` ``assert_kinds`` / ``assert_call_kinds`` fields; a language
that maps neither produces nothing (never a false positive). Consumed by the
``large_assertion_block`` / ``duplicated_assertion_block`` biomarkers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .ast_utils import _IDENTIFIER_SUFFIX
from .languages import LanguageNodeMap

if TYPE_CHECKING:
    from tree_sitter import Node

# Callee-name prefixes that mark a call as a test assertion. Matched
# case-insensitively against every identifier in the call's callee chain,
# so ``assertEqual`` / ``assert_eq`` / ``Assert.assertTrue`` / ``expect``
# all qualify. Deliberately small — these two prefixes cover xUnit-family
# (``assert*``) and the BDD/expect family (``expect(...)``).
_ASSERT_CALL_PREFIXES = ("assert", "expect")
_EXPRESSION_STATEMENT = "expression_statement"
_AWAIT_WRAPPER_KINDS = ("await_expression", "await", "parenthesized_expression")


def _callee_matches_assert(call_node: Node) -> bool:
    """True if any identifier in *call_node*'s callee chain is assert-ish.

    Only the callee (the ``function`` / ``macro`` field) is inspected, not
    the arguments — so ``foo(assertion)`` does not match while
    ``expect(x).toBe(y)`` and ``self.assertEqual(...)`` do.
    """
    callee = call_node.child_by_field_name("function") or call_node.child_by_field_name("macro")
    # Fallback when no ``function``/``macro`` field is exposed: the first
    # named child is usually the callee.
    roots = [callee] if callee is not None else [c for c in call_node.children if c.is_named][:1]
    stack: list[Node] = list(roots)
    while stack:
        node = stack.pop()
        if node.type.endswith(_IDENTIFIER_SUFFIX) and node.text is not None:
            name = node.text.decode("utf-8", errors="replace").lower()
            if any(name.startswith(p) for p in _ASSERT_CALL_PREFIXES):
                return True
        for child in node.children:
            stack.append(child)
    return False


def _find_assert_call(stmt: Node, kinds: frozenset[str]) -> Node | None:
    """Find an assertion-call node that is *stmt*'s own expression.

    Searches direct named children and one level deeper (to see through
    ``await`` / parenthesis wrappers) — but no further, so a call buried in
    an argument or a nested block is not mistaken for the statement's
    expression.
    """
    for child in stmt.children:
        if not child.is_named:
            continue
        if child.type in kinds:
            return child
        if child.type in _AWAIT_WRAPPER_KINDS:
            for gc in child.children:
                if gc.is_named and gc.type in kinds:
                    return gc
    return None


def _is_assertion_statement(stmt: Node, lmap: LanguageNodeMap) -> bool:
    """True if *stmt* is a test assertion (bare ``assert`` or assert call)."""
    if stmt.type in lmap.assert_kinds:
        return True
    if not lmap.assert_call_kinds:
        return False
    # Some grammars (Kotlin) have no ``expression_statement`` wrapper — the
    # call node sits directly in the statement list. Match it as the
    # statement itself. (Wrapper languages never hit this: their call nodes
    # only ever appear as the single child of an ``expression_statement``,
    # so they can't form a run of ≥2 at this level.)
    if stmt.type in lmap.assert_call_kinds:
        return _callee_matches_assert(stmt)
    if stmt.type != _EXPRESSION_STATEMENT:
        return False
    call = _find_assert_call(stmt, lmap.assert_call_kinds)
    return call is not None and _callee_matches_assert(call)


def _collect_assertion_blocks(body_node: Node, lmap: LanguageNodeMap) -> list[tuple[int, int, int]]:
    """Runs of ≥2 consecutive assertion statements within a function body.

    Each run is recorded as ``(start_line, end_line, count)``. Runs are
    found per statement-list (a block's direct children), so an assertion
    sequence broken by a non-assertion statement starts a new run. Nested
    function bodies are skipped — their assertions belong to them.
    """
    if not lmap.assert_kinds and not lmap.assert_call_kinds:
        return []
    blocks: list[tuple[int, int, int]] = []

    def _scan_siblings(parent: Node) -> None:
        run_start = 0
        run_end = 0
        run_count = 0
        for child in parent.children:
            if not child.is_named:
                continue
            if _is_assertion_statement(child, lmap):
                if run_count == 0:
                    run_start = child.start_point[0] + 1
                run_end = child.end_point[0] + 1
                run_count += 1
            else:
                if run_count >= 2:
                    blocks.append((run_start, run_end, run_count))
                run_count = 0
        if run_count >= 2:
            blocks.append((run_start, run_end, run_count))

    def _visit(node: Node) -> None:
        _scan_siblings(node)
        for child in node.children:
            if child.type in lmap.function_kinds:
                continue  # nested fn — walked as its own entry
            _visit(child)

    _visit(body_node)
    return blocks
