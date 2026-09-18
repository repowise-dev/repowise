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
* ``total`` counts the **broad** tier: narrow, plus the language's dialect,
  plus the assertion shapes the narrow tier structurally cannot see (a ``with``
  header, a property-terminated chain, a split call, a private helper). Its
  readers are the advisory ``mock_saturated_test`` and ``assertion_free_test``.
* ``verifications`` counts mock verifications, which are in neither count
  above. ``assertion_free_test`` asks whether a test checks anything, and a
  verification is an oracle, so it counts there; ``mock_saturated_test``
  measures verification itself, so counting it in that marker's denominator
  would blind it. The same call is read two ways on purpose.
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

# Assertion tiers. Narrow implies broad. Verification is a fourth label rather
# than a stronger tier: these are names, not a rank, and nothing compares them
# by order.
_NOT_ASSERTION = 0
_BROAD = 1
_NARROW = 2
_VERIFICATION = 3


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


def _find_property_chain_call(stmt: Node, lmap: LanguageNodeMap) -> Node | None:
    """The call at the foot of a property-terminated chain, if any.

    chai and should.js end an assertion in a property rather than a call:
    ``expect(x).to.be.null`` is a member expression whose innermost object is
    the ``expect(x)`` call, so the statement is not a call node and every
    lookup above it declines. Descending the ``object`` field finds it, and a
    language whose grammar has no such field simply gets nothing.
    """
    return _descend_to_call(next((c for c in stmt.children if c.is_named), None), lmap)


def _descend_to_call(node: Node | None, lmap: LanguageNodeMap) -> Node | None:
    """Unwrap ``await`` and walk a property chain down to the call at its foot."""
    while node is not None:
        if node.type in lmap.assert_call_kinds:
            return node
        if node.type in _AWAIT_WRAPPER_KINDS:
            node = next((c for c in node.children if c.is_named), None)
            continue
        node = node.child_by_field_name("object")
    return None


def _split_callee_matches_assert(call_node: Node) -> bool:
    """An assert-ish name in a split call's ``name`` field.

    Java states a call as ``object`` + ``name`` and exposes no single callee
    node, so :func:`_callee_matches_assert` reaches only the receiver and misses
    ``TestUtils.assertStatusException(...)``. It is answered at the BROAD tier
    rather than in the narrow one on purpose: the narrow tier is what the
    calibrated block markers count, and admitting these names there was measured
    to move assertion runs. Promoting it is a calibrated change needing its own
    before/after. LANGUAGE_SUPPORT.md#code-health-coverage.
    """
    if call_node.child_by_field_name("function") is not None:
        return False
    name = call_node.child_by_field_name("name")
    if name is None or name.text is None:
        return False
    text = name.text.decode("utf-8", errors="replace").lower()
    return any(text.startswith(p) for p in NARROW_PREFIXES)


def _private_callee_matches_assert(call_node: Node) -> bool:
    """An assert-ish callee behind a leading underscore.

    ``_assert_no_secret_leak(msg)`` is an assertion helper by any reading, but
    the narrow prefixes anchor at the start of the name and the underscore
    breaks the anchor, so the call went uncounted and the tests calling it read
    as assertion-free. Python marks a helper private this way constantly.
    Demoted to broad like the other shapes the narrow tier cannot see.
    """
    names = _callee_names(call_node)
    if names is None:
        return False
    called, roots = names
    return any(
        name.lstrip("_").startswith(NARROW_PREFIXES) for name in (called, *roots) if name
    )


def _dialect_tier(call_node: Node, dialect: AssertDialect) -> int:
    """Broad-tier verdict: assertion, verification, or neither.

    Names are exact and read from both ends of the call, because a verification
    reads either way round: ``verify(mock)`` is the callee, and in
    ``verify(mock).save()`` it is the receiver of ``save``.
    """
    names = _callee_names(call_node)
    if names is None:
        return _NOT_ASSERTION
    called, roots = names
    verdict = _receiver_method_verdict(called, roots, dialect.receiver_methods)
    if verdict is not None:
        # A listed receiver settles the call; it is exhaustive about its own
        # methods, so a decline here must not fall through to the name lists.
        return _BROAD if verdict else _NOT_ASSERTION
    if called in dialect.assert_names or roots & dialect.assert_names:
        return _BROAD
    if called in dialect.verify_names or roots & dialect.verify_names:
        return _VERIFICATION
    return _NOT_ASSERTION


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


def _find_context_manager_call(stmt: Node, lmap: LanguageNodeMap) -> Node | None:
    """An assertion-shaped call in a ``with`` header, body excluded."""
    stack = [c for c in stmt.children if c.is_named and c.type not in lmap.block_kinds]
    while stack:
        node = stack.pop()
        if node.type in lmap.assert_call_kinds:
            return node
        stack.extend(c for c in node.children if c.is_named and c.type not in lmap.block_kinds)
    return None


def _assertion_tier(stmt: Node, lmap: LanguageNodeMap, dialect: AssertDialect | None) -> int:
    """The tier *stmt* asserts at, one of the four module constants.

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
        if call is None:
            # A property-terminated assertion is capped at broad for the same
            # reason as the split shape: the narrow tier is what the calibrated
            # block markers count.
            chained = _find_property_chain_call(stmt, lmap)
            if chained is not None:
                return _tier_without_narrow(chained, dialect)
    elif stmt.type in lmap.with_kinds:
        # ``with pytest.raises(...)`` / ``with self.assertRaises(...)`` is the
        # oracle, but the call sits in the header rather than in a statement of
        # its own, so every tier above misses it.
        call = _find_context_manager_call(stmt, lmap)
        return _NOT_ASSERTION if call is None else _tier_without_narrow(call, dialect)
    else:
        return _NOT_ASSERTION
    if call is None:
        return _NOT_ASSERTION
    if _callee_matches_assert(call):
        return _NARROW
    if _split_callee_matches_assert(call) or _private_callee_matches_assert(call):
        return _BROAD
    if dialect is not None:
        return _dialect_tier(call, dialect)
    return _NOT_ASSERTION


def _tier_without_narrow(call: Node, dialect: AssertDialect | None) -> int:
    """Classify *call*, demoting a narrow verdict to ``_BROAD``.

    Used for the assertion shapes the narrow tier structurally cannot see: a
    ``with`` header and a property-terminated chain. A narrow verdict for either
    would let it join an assertion *run*, and runs are what the calibrated block
    markers read. Capping at broad keeps those byte-identical while the total
    still sees the oracle.
    """
    if _callee_matches_assert(call):
        return _BROAD
    if dialect is None:
        return _NOT_ASSERTION
    return _dialect_tier(call, dialect)


def _is_assertion_statement(
    stmt: Node, lmap: LanguageNodeMap, dialect: AssertDialect | None = None
) -> bool:
    """True when *stmt* is an assertion, in either assertion tier.

    A verification is deliberately excluded: ``mock_walk`` calls this to keep
    assertions out of its mock-setup count, and a verification is exactly what
    ``mock_saturated_test`` means by mock setup.
    """
    return _assertion_tier(stmt, lmap, dialect) in (_NARROW, _BROAD)


def _collect_assertion_facts(
    body_node: Node, lmap: LanguageNodeMap, dialect: AssertDialect | None = None
) -> tuple[list[tuple[int, int, int]], int, int]:
    """``(blocks, total, verifications)`` assertion facts for one function body.

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

    *verifications* counts mock verifications, on the same block-level gate as
    *total* and in neither of the other two counts. See the module docstring.
    """
    if not lmap.assert_kinds and not lmap.assert_call_kinds:
        return [], 0, 0
    blocks: list[tuple[int, int, int]] = []
    total = 0
    verifications = 0

    def _scan_siblings(parent: Node, *, count_total: bool) -> None:
        nonlocal total, verifications
        run_start = 0
        run_end = 0
        run_count = 0
        for child in parent.children:
            if not child.is_named:
                continue
            tier = _assertion_tier(child, lmap, dialect)
            if count_total:
                if tier == _VERIFICATION:
                    verifications += 1
                elif tier != _NOT_ASSERTION:
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
        # Lambda kinds join the block kinds because an expression-bodied arrow
        # has no statement at all: ``waitFor(() => expect(x).toBe(1))`` keeps
        # its assertion directly under the arrow. Run detection below is
        # unconditional and so is untouched by this.
        counts_here = (
            node is body_node
            or node.type in lmap.block_kinds
            or node.type in lmap.lambda_kinds
        )
        _scan_siblings(node, count_total=counts_here)
        for child in node.children:
            if child.type in lmap.function_kinds:
                continue  # nested fn, not collected as its own entry either
            _visit(child)

    _visit(body_node)
    # A lambda with an expression body holds no statement: ``it("x", () =>
    # expect(a).toBe(b))`` puts the assertion where only the body node itself
    # sits, so the sibling scan above never sees it. Classified here, demoted to
    # broad like the other shapes, and it cannot form a run because a run needs
    # two siblings.
    if body_node.type not in lmap.block_kinds and lmap.assert_call_kinds:
        call = _descend_to_call(body_node, lmap)
        if call is not None:
            tier = _tier_without_narrow(call, dialect)
            if tier == _VERIFICATION:
                verifications += 1
            elif tier != _NOT_ASSERTION:
                total += 1
    return blocks, total, verifications
