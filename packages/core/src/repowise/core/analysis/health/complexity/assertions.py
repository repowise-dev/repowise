"""Assertion-block detection (test-quality smells).

Finds runs of ≥2 consecutive assertion statements within a function body,
recorded as ``(start_line, end_line, count)``, and the body's total assertion
count. Opt-in per language via the ``LanguageNodeMap`` ``assert_kinds`` /
``assert_call_kinds`` fields; a language that maps neither produces nothing
(never a false positive). Consumed by the ``large_assertion_block`` /
``duplicated_assertion_block`` biomarkers, and by ``mock_saturated_test``,
which divides mock setup by the total.

Two tiers are counted in one walk, and which marker reads which is the whole
design (``asserts/lexicon.py`` carries the vocabulary and the evidence):

* ``blocks`` counts the **narrow** tier only — an ``assert``/``expect`` callee
  or the language's own ``assert`` statement. The two block markers are
  calibrated on it, so it takes no per-language and no user vocabulary, and a
  broad-only statement breaks a run exactly as a non-assertion always has.
* ``total`` counts the **broad** tier, which is narrow plus the language's
  dialect. Its only reader is the advisory ``mock_saturated_test``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..asserts.lexicon import NARROW_PREFIXES, AssertDialect
from .ast_utils import _IDENTIFIER_SUFFIX, _callee_names, _receiver_method_verdict
from .languages import LanguageNodeMap

if TYPE_CHECKING:
    from tree_sitter import Node

_EXPRESSION_STATEMENT = "expression_statement"
_AWAIT_WRAPPER_KINDS = ("await_expression", "await", "parenthesized_expression")

# Assertion tiers. Narrow implies broad, so a statement carries one of three.
_NOT_ASSERTION = 0
_BROAD = 1
_NARROW = 2


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
            if any(name.startswith(p) for p in NARROW_PREFIXES):
                return True
        for child in node.children:
            stack.append(child)
    return False


def _callee_matches_dialect(call_node: Node, dialect: AssertDialect) -> bool:
    """True if *call_node* asserts in this language's broad vocabulary.

    Names are exact and read from both ends of the call, because a verification
    reads either way round: ``verify(mock)`` is the callee, and in
    ``verify(mock).save()`` it is the receiver of ``save``.
    """
    names = _callee_names(call_node)
    if names is None:
        return False
    called, roots = names
    verdict = _receiver_method_verdict(called, roots, dialect.receiver_methods)
    if verdict is not None:
        return verdict
    return called in dialect.assert_names or bool(roots & dialect.assert_names)


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


def _assertion_tier(stmt: Node, lmap: LanguageNodeMap, dialect: AssertDialect | None) -> int:
    """The tier *stmt* asserts at: ``_NARROW``, ``_BROAD`` or ``_NOT_ASSERTION``.

    The broad tier is consulted only once the narrow one has declined, which is
    what keeps a language with no dialect classifying exactly as narrow alone.
    """
    if stmt.type in lmap.assert_kinds:
        return _NARROW
    if not lmap.assert_call_kinds:
        return _NOT_ASSERTION
    if stmt.type in lmap.assert_call_kinds:
        # Some grammars (Kotlin) have no ``expression_statement`` wrapper — the
        # call node sits directly in the statement list. Match it as the
        # statement itself. (Wrapper languages never hit this: their call nodes
        # only ever appear as the single child of an ``expression_statement``,
        # so they can't form a run of ≥2 at this level.)
        call: Node | None = stmt
    elif stmt.type == _EXPRESSION_STATEMENT:
        call = _find_assert_call(stmt, lmap.assert_call_kinds)
    else:
        return _NOT_ASSERTION
    if call is None:
        return _NOT_ASSERTION
    if _callee_matches_assert(call):
        return _NARROW
    if dialect is not None and _callee_matches_dialect(call, dialect):
        return _BROAD
    return _NOT_ASSERTION


def _is_assertion_statement(
    stmt: Node, lmap: LanguageNodeMap, dialect: AssertDialect | None = None
) -> bool:
    """True if *stmt* is a test assertion at the broad tier."""
    return _assertion_tier(stmt, lmap, dialect) != _NOT_ASSERTION


def _collect_assertion_facts(
    body_node: Node, lmap: LanguageNodeMap, dialect: AssertDialect | None = None
) -> tuple[list[tuple[int, int, int]], int]:
    """``(blocks, total)`` assertion facts for one function body.

    *blocks* are runs of ≥2 consecutive **narrow-tier** assertion statements,
    each recorded as ``(start_line, end_line, count)``. Runs are found per
    statement-list (a block's direct children), so an assertion sequence broken
    by a non-assertion statement starts a new run — and a broad-only statement
    breaks one, because these runs are what the calibrated markers read.
    Nested function bodies are skipped: their assertions belong to them.

    *total* counts **broad-tier** assertion **statements** only, at block level.
    The run scan keeps scanning everywhere, which is a deliberate asymmetry: it
    feeds the calibrated ``duplicated_assertion_block``, and narrowing it would
    change scored findings. A run needs two siblings so it rarely fires off a
    statement list, but a total counts each match on its own and would
    double-count every assertion in a language whose ``assert_call_kinds`` is
    its plain call node. Block level also makes it commensurable with
    ``mock_walk._count_body_setup``.
    """
    if not lmap.assert_kinds and not lmap.assert_call_kinds:
        return [], 0
    blocks: list[tuple[int, int, int]] = []
    total = 0

    def _scan_siblings(parent: Node, *, count_total: bool) -> None:
        nonlocal total
        run_start = 0
        run_end = 0
        run_count = 0
        for child in parent.children:
            if not child.is_named:
                continue
            tier = _assertion_tier(child, lmap, dialect)
            if count_total and tier != _NOT_ASSERTION:
                total += 1
            if tier == _NARROW:
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
        _scan_siblings(node, count_total=node is body_node or node.type in lmap.block_kinds)
        for child in node.children:
            if child.type in lmap.function_kinds:
                continue  # nested fn, not collected as its own entry either
            _visit(child)

    _visit(body_node)
    return blocks, total
