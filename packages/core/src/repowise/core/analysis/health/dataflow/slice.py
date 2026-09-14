"""Extract Method slicing: find safe, single-exit spans with IN/OUT inference.

The first user-facing consumer of the dataflow layer. Given a function's
analysis (CFG + def/use from D2), it finds contiguous statement spans that can
be lifted into a helper method without changing behaviour, and infers the
helper's signature:

- **IN (parameters)** -- variables the span *reads* whose value is produced
  before the span (defined-before, used-inside).
- **OUT (return)** -- variables the span *defines* that are *used after* it,
  with no intervening redefinition (so the helper returns the live value).

**Extractability predicate (precision-first).** A span is a candidate only when
it cuts at statement boundaries within a single block (so never a partial
branch or a mid-``try`` split), contains no control-flow jump that leaves the
region (``return`` / ``raise`` / ``break`` / ``continue`` -> single clean exit),
removes real complexity (at least one decision point), is substantial enough to
matter, and has at most one return and a small parameter list. Everything else
is suppressed -- ten great extractions, not two hundred maybes.

Line-based liveness over D2's def/use occurrences realises the IN/OUT inference;
the CFG/jump scan realises the single-exit predicate. The jump and nested-scope
node kinds come from the language's ``LanguageNodeMap`` (the same source the CFG
builder uses), so every full-tier language whose map populates them is served by
this one slicer.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap
    from .analyze import FunctionAnalysis
    from .defuse import FunctionDefUse

# Gates (precision-first; tuned to suppress trivial or unwieldy extractions).
_MIN_STMTS = 2  # at least two statements
_MIN_SLICE_NLOC = 6  # the extracted helper is substantial
_MIN_CCN_REMOVED = 1  # extraction must remove a real decision point
_MAX_PARAMS = 5  # too many ins => the span is not cohesive
_MAX_RETURNS = 1  # a single clean return (v1); multi-output is future work
# Backstop against a pathological function producing too many sub-ranges.
_MAX_CANDIDATES = 4000


@dataclass(frozen=True)
class Extraction:
    """One safe Extract Method candidate over a function.

    ``start_line`` / ``end_line`` bound the span (1-indexed, inclusive).
    ``params`` are the inferred IN variables, ``returns`` the inferred OUT
    variable(s). ``slice_nloc`` is the span's statement line count and
    ``ccn_removed`` the decision points it carries (the complexity the residual
    method sheds).
    """

    start_line: int
    end_line: int
    params: tuple[str, ...]
    returns: tuple[str, ...]
    slice_nloc: int
    ccn_removed: int


def find_extractions(analysis: FunctionAnalysis, lmap: LanguageNodeMap) -> list[Extraction]:
    """Return safe Extract Method candidates for *analysis*, best first.

    Best is most complexity removed, then largest span, then fewest parameters,
    then earliest -- a deterministic order. Empty when the function has no AST
    node retained or no span clears the extractability gates.
    """
    fn_node = analysis.fn_node
    if fn_node is None:
        return []
    # A subtree tree-sitter could not parse is not a function whose statements
    # we understand, so no span in it is safe to lift. This fires on macro-heavy
    # C/C++ headers, where an unterminated function-like macro
    # (``ABSL_NAMESPACE_BEGIN``) makes the parser emit one bogus
    # ``function_definition`` spanning a whole class or namespace — proposing to
    # extract "statements" from that is a wrong suggestion, not a weak one.
    # Language-agnostic and strictly subtractive: a clean parse is unaffected.
    if fn_node.has_error:
        return []
    body = fn_node.child_by_field_name("body")
    if body is None:
        return []
    # The statement container of the body (Go nests it in a ``statement_list``
    # inside the ``block``); spans covering it whole are not extractions.
    body_container = _unwrap_container(body, lmap.block_kinds)

    def_lines, use_lines = _var_lines(analysis.def_use)
    hoisted = _hoisted_bindings(def_lines, use_lines)
    decision_kinds = (
        lmap.branch_kinds
        | lmap.loop_kinds
        | lmap.case_kinds
        | lmap.catch_kinds
        | lmap.boolean_operator_kinds
    )
    jump_kinds = lmap.return_kinds | lmap.raise_kinds | lmap.break_kinds | lmap.continue_kinds
    scope_kinds = lmap.function_kinds | lmap.lambda_kinds
    # Expression-oriented grammars (nonempty ``statement_wrapper_kinds``): a
    # block's last child that is not a statement is its tail expression -- the
    # block's implicit value. A span ending on one would silently drop that
    # value when lifted into a helper, so such spans are refused outright.
    tail_stmt_kinds = (
        lmap.statement_wrapper_kinds | lmap.local_decl_kinds
        if lmap.statement_wrapper_kinds
        else None
    )

    out: list[Extraction] = []
    evaluated = 0
    for block, loop in _all_blocks(fn_node, lmap.block_kinds, scope_kinds, lmap.loop_kinds):
        stmts = block.named_children
        n = len(stmts)
        is_body = block.id == body_container.id
        # One subtree walk per statement, then O(1) metrics per span via
        # prefix sums. _span_metrics processes each span statement's subtree
        # independently, so a span's decision count is the sum over its
        # statements and its jump bit the OR — walking each statement once
        # replaces the per-span re-walks that made this loop O(n^2 * subtree).
        if n >= _MIN_STMTS:
            dec_prefix = [0]
            jump_prefix = [0]
            # Named-nested-function count rides the same prefix sums, for the
            # same reason: the check is a subtree walk, and asking it per
            # candidate span would put the O(n^2 * subtree) cost straight back.
            nested_prefix = [0]
            for st in stmts:
                d, jmp = _span_metrics([st], decision_kinds, jump_kinds, scope_kinds)
                dec_prefix.append(dec_prefix[-1] + d)
                jump_prefix.append(jump_prefix[-1] + (1 if jmp else 0))
                nested_prefix.append(
                    nested_prefix[-1] + (1 if _holds_a_named_nested_function([st], lmap) else 0)
                )
        for i in range(n):
            for j in range(i, n):
                evaluated += 1
                if evaluated > _MAX_CANDIDATES:
                    return _sorted(out)
                length = j - i + 1
                if length < _MIN_STMTS:
                    continue
                # Never extract the whole function body (that is not a split).
                if is_body and length == n:
                    continue
                if (
                    tail_stmt_kinds is not None
                    and j == n - 1
                    and stmts[j].type not in tail_stmt_kinds
                ):
                    continue
                decisions = dec_prefix[j + 1] - dec_prefix[i]
                has_jump = jump_prefix[j + 1] > jump_prefix[i]
                if has_jump or decisions < _MIN_CCN_REMOVED:
                    continue
                span = stmts[i : j + 1]
                slice_nloc = sum(st.end_point[0] - st.start_point[0] + 1 for st in span)
                if slice_nloc < _MIN_SLICE_NLOC:
                    continue
                s = span[0].start_point[0] + 1
                e = span[-1].end_point[0] + 1
                params, returns = _infer_in_out(def_lines, use_lines, s, e)
                if len(params) > _MAX_PARAMS or len(returns) > _MAX_RETURNS:
                    continue
                if not _outs_definitely_assigned(span, returns, def_lines, lmap):
                    continue
                if any(s <= first_def <= e and first_use < s for first_def, first_use in hoisted):
                    continue
                if nested_prefix[j + 1] > nested_prefix[i]:
                    continue
                if loop is not None and not _loop_carry_free(
                    span, loop, s, e, def_lines, use_lines, lmap
                ):
                    continue
                out.append(
                    Extraction(
                        start_line=s,
                        end_line=e,
                        params=params,
                        returns=returns,
                        slice_nloc=slice_nloc,
                        ccn_removed=decisions,
                    )
                )
    return _sorted(out)


def _sorted(candidates: list[Extraction]) -> list[Extraction]:
    return sorted(
        candidates,
        key=lambda x: (-x.ccn_removed, -x.slice_nloc, len(x.params), x.start_line),
    )


def _var_lines(def_use: FunctionDefUse) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Per-variable sorted def lines and use lines from D2's facts.

    Parameter definitions are included (seeded at the signature line), so a
    parameter naturally counts as "defined before" any body span.
    """
    def_lines: dict[str, list[int]] = defaultdict(list)
    use_lines: dict[str, list[int]] = defaultdict(list)
    for d in def_use.definitions:
        def_lines[d.var].append(d.line)
    for bdu in def_use.blocks.values():
        for u in bdu.uses:
            use_lines[u.name].append(u.line)
    for lines in def_lines.values():
        lines.sort()
    for lines in use_lines.values():
        lines.sort()
    return def_lines, use_lines


def _infer_in_out(
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
    s: int,
    e: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Infer IN (parameters) and OUT (return) variables for span ``[s, e]``.

    IN: a variable read in the span whose first in-span read is not preceded by
    an in-span write, and which has a definition before the span (a parameter or
    an earlier assignment). OUT: a variable written in the span and read after
    it, with no redefinition between the span and that first later read.
    """
    params: list[str] = []
    returns: list[str] = []
    for var in sorted(set(def_lines) | set(use_lines)):
        dl = def_lines.get(var, [])
        ul = use_lines.get(var, [])
        in_uses = [ln for ln in ul if s <= ln <= e]
        in_defs = [ln for ln in dl if s <= ln <= e]

        if in_uses and any(ln < s for ln in dl):
            first_use = in_uses[0]
            if not any(ln < first_use for ln in in_defs):
                params.append(var)

        if in_defs:
            after_uses = [ln for ln in ul if ln > e]
            if after_uses:
                first_after = after_uses[0]
                redefined = any(e < ln < first_after for ln in dl)
                if not redefined:
                    returns.append(var)
    return tuple(params), tuple(returns)


def _holds_a_named_nested_function(span: list[Node], lmap: LanguageNodeMap) -> bool:
    """True when the span contains a nested function that binds its own name.

    Def/use is computed per function and deliberately does not descend into a
    nested scope, so a sibling closure's call to such a helper is invisible
    here. Lifting the declaration out of the scope moves the binding away from
    those callers, and nothing in the facts would show it.

    A named nested function exists to be called from elsewhere in its scope, so
    the safe answer is to refuse rather than to guess at its callers. An
    anonymous lambda bound to a local is a value and stays governed by the
    ordinary IN/OUT rules. Measured on this repo's ranked head:
    ``vscode/src/features/changeIntel.ts::registerChangeIntel`` offered its whole
    ``render`` declaration while a sibling closure called it.
    """
    kinds = lmap.function_kinds
    if not kinds:
        return False
    for st in span:
        stack = [st]
        while stack:
            node = stack.pop()
            if node.type in kinds and node.child_by_field_name("name") is not None:
                return True
            stack.extend(node.children)
    return False


def _hoisted_bindings(
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
) -> list[tuple[int, int]]:
    """First-definition lines of names the function reads before defining them.

    A read before the only definition of a name can only work by hoisting: a
    JS/TS ``function foo()`` declaration is visible from the top of its scope,
    so code above it calls it. A span holding that definition cannot be lifted -
    an OUT cannot express it, because the value has to exist *before* the span
    runs, not after - so :func:`find_extractions` refuses any span containing
    one of these lines.

    Returns ``(first_def, first_use)`` pairs. A span refuses when its range holds
    a ``first_def`` whose ``first_use`` sits *above* the span: the earlier read
    has no earlier definition to answer it, so the span holds the only one.
    A read that sits inside the span, before the definition, is a different
    shape and is left to the loop-carried gate.

    Computed once per function because the shape is rare (usually no name
    qualifies at all), and asking per candidate span meant walking every
    variable thousands of times. Both lists are sorted by :func:`_var_lines`,
    so the first entry of each is the earliest.

    Measured on this repo's ranked head:
    ``vscode/src/features/changeIntel.ts::registerChangeIntel`` offered its whole
    ``render`` declaration as a span while ``render(partners)`` sat above it.
    """
    hoisted: list[tuple[int, int]] = []
    for var, defs in def_lines.items():
        uses = use_lines.get(var)
        if defs and uses and uses[0] < defs[0]:
            hoisted.append((defs[0], uses[0]))
    return sorted(hoisted)


def _outs_definitely_assigned(
    span: list[Node],
    returns: tuple[str, ...],
    def_lines: dict[str, list[int]],
    lmap: LanguageNodeMap,
) -> bool:
    """True when every OUT variable is written on *every* path through the span.

    A conditionally written OUT is the unsound case line-based liveness cannot
    see: the helper returns the unwritten value on the paths that skip the
    write, and the caller's assignment then clobbers the live one. The proof
    needs branch structure, so it runs structurally here and the span is
    refused whenever the proof does not go through.
    """
    return all(_stmts_assign(span, var, def_lines.get(var, []), lmap) for var in returns)


def _stmts_assign(stmts: list[Node], var: str, defs: list[int], lmap: LanguageNodeMap) -> bool:
    """True when *stmts* writes *var* on every path through them."""
    return any(_stmt_assigns(st, var, defs, lmap) for st in stmts)


def _stmt_assigns(st: Node, var: str, defs: list[int], lmap: LanguageNodeMap) -> bool:
    """True only when *st* is proved to write *var* however it is entered.

    Positive proof only: a statement kind this cannot classify returns False.
    Defaulting to True would let any statement sharing a line with a
    conditional write stand in as the proof.
    """
    lo = st.start_point[0] + 1
    hi = st.end_point[0] + 1
    if not any(lo <= d <= hi for d in defs):
        return False
    if _is_conditional(st, lmap):
        return _conditional_assigns(st, var, defs, lmap)
    if st.type in lmap.block_kinds:
        container = _unwrap_container(st, lmap.block_kinds)
        return _stmts_assign(container.named_children, var, defs, lmap)
    if st.type in lmap.with_kinds or st.type in lmap.statement_wrapper_kinds:
        return _stmts_assign(list(st.named_children), var, defs, lmap)
    return _unconditional_write(st, var, lmap)


def _unconditional_write(st: Node, var: str, lmap: LanguageNodeMap) -> bool:
    """True when *st* contains a write to *var* no branch or loop guards.

    The walk stops at any control container (a loop body may run zero times, a
    branch arm may not be taken, a catch arm may not fire) and at nested
    scopes, so only writes on the statement's own straight-line path count.
    """
    write_kinds = lmap.assignment_kinds | lmap.augmented_assign_kinds | lmap.local_decl_kinds
    if not write_kinds:
        return False
    barriers = (
        lmap.loop_kinds
        | lmap.try_kinds
        | lmap.catch_kinds
        | lmap.switch_kinds
        | lmap.case_kinds
        | lmap.branch_kinds
        | lmap.if_kinds
        | lmap.function_kinds
        | lmap.lambda_kinds
    )
    if st.type in barriers:
        return False
    stack = [st]
    while stack:
        node = stack.pop()
        if node.id != st.id and node.type in barriers:
            continue
        if node.type in write_kinds and _writes_var(node, var):
            return True
        stack.extend(node.children)
    return False


def _writes_var(node: Node, var: str) -> bool:
    """True when *var* is on the binding side of this write node."""
    for field in ("left", "pattern", "declarator", "name"):
        target = node.child_by_field_name(field)
        if target is not None:
            return var in _identifiers(target)
    return var in _identifiers(node)


def _is_conditional(node: Node, lmap: LanguageNodeMap) -> bool:
    return node.type in lmap.if_kinds or (
        node.type in lmap.branch_kinds and node.child_by_field_name("condition") is not None
    )


def _conditional_assigns(node: Node, var: str, defs: list[int], lmap: LanguageNodeMap) -> bool:
    """True when a conditional chain is exhaustive and every arm writes *var*.

    Two chain shapes. Python flattens ``elif``/``else`` into repeated
    ``alternative`` fields on one ``if``, so the terminal else is a sibling of
    the chained arms. The C family nests instead: the single ``alternative`` is
    another ``if``, whose own else ends the chain. Both must be exhaustive.
    """
    consequence = node.child_by_field_name("consequence") or node.child_by_field_name("body")
    if consequence is None:
        return False
    alternatives = list(node.children_by_field_name("alternative"))
    if not alternatives:
        return False  # no else at all: some path skips the write
    if not _branch_assigns(consequence, var, defs, lmap):
        return False
    for alt in alternatives:
        if _is_conditional(alt, lmap):
            # Nested chain (C family): it carries the chain's terminal else.
            if len(alternatives) == 1:
                return _conditional_assigns(alt, var, defs, lmap)
            # Flattened chain (Python's ``elif``): the else is a sibling below.
            arm = alt.child_by_field_name("consequence") or alt.child_by_field_name("body")
            if arm is None or not _branch_assigns(arm, var, defs, lmap):
                return False
        elif not _branch_assigns(alt, var, defs, lmap):
            return False
    return any(not _is_conditional(alt, lmap) for alt in alternatives)


def _branch_assigns(node: Node, var: str, defs: list[int], lmap: LanguageNodeMap) -> bool:
    if _is_conditional(node, lmap):
        return _conditional_assigns(node, var, defs, lmap)
    if node.type in lmap.block_kinds:
        container = _unwrap_container(node, lmap.block_kinds)
        return _stmts_assign(container.named_children, var, defs, lmap)
    return _stmts_assign(list(node.named_children), var, defs, lmap)


def _loop_carry_free(
    span: list[Node],
    loop: Node,
    s: int,
    e: int,
    def_lines: dict[str, list[int]],
    use_lines: dict[str, list[int]],
    lmap: LanguageNodeMap,
) -> bool:
    """True when a span nested in *loop* carries no state between iterations.

    Two shapes are refused. A variable the span writes that the loop also reads
    without the span having written it first is loop-carried: that read takes
    the previous iteration's value, and lifting the write into a helper whose
    result is discarded silently drops it.

    The read does not have to sit above the span. ``clojure.py::_spec_namespaces``
    reads ``expect_ns`` and then writes it, both inside one candidate span, in a
    ``while`` loop: textually the read precedes the write, so nothing follows
    the span to make the variable an OUT, and the state machine breaks on the
    next iteration. Testing only for reads above the span missed it, so the test
    is "read with no in-span write before it", which subsumes the old one.

    Also refused: a call on a name the loop header reads, which mutates the
    state the loop iterates over (``entries.remove(entry)`` inside
    ``for entry in entries``), and which an IN/OUT signature cannot express.
    """
    loop_start = loop.start_point[0] + 1
    for var, lines in def_lines.items():
        in_span_writes = [ln for ln in lines if s <= ln <= e]
        if not in_span_writes:
            continue
        for read in use_lines.get(var, []):
            if not loop_start <= read <= e:
                continue
            if not any(write < read for write in in_span_writes):
                return False

    carried = _loop_header_names(loop, lmap)
    if not carried:
        return True
    scope_kinds = lmap.function_kinds | lmap.lambda_kinds
    for st in span:
        for call in _descend(st, lmap.call_kinds, scope_kinds):
            callee = call.child_by_field_name("function")
            if callee is None:
                named = call.named_children
                callee = named[0] if named else None
            root = _receiver_root(callee) if callee is not None else None
            if root is not None and root in carried:
                return False
    return True


def _receiver_root(node: Node) -> str | None:
    """The base name a call is made on: ``entries`` in ``entries.remove(x)``.

    Only the root counts. Matching any name in the callee read
    ``existing.pages.push(page)`` as touching the ``pages`` the loop iterates,
    when the receiver is ``existing`` and the collision is in a member name.
    """
    cur = node
    while True:
        nxt = None
        for field in ("object", "operand", "value", "argument", "function"):
            child = cur.child_by_field_name(field)
            if child is not None:
                nxt = child
                break
        if nxt is None:
            break
        cur = nxt
    if not cur.children and cur.type.endswith("identifier"):
        text = cur.text
        return text.decode("utf-8", "replace") if text else None
    return None


# Fields a loop header binds through. Read structurally: a line-range test
# cannot tell the header's binder from a variable reassigned on the body's
# first line, and getting that wrong drops the iterated collection out of the
# carried set, which is exactly the name the mutation check exists to catch.
_BINDER_FIELDS = ("left", "pattern", "declarator", "name")


def _loop_header_names(loop: Node, lmap: LanguageNodeMap) -> set[str]:
    """Names the loop header reads, minus the ones it binds (the loop variable)."""
    body = loop.child_by_field_name("body")
    names: set[str] = set()
    # The binder is a field of the loop itself in most grammars, and of a
    # header clause it wraps in Go's ``range``.
    bound: set[str] = _binder_names(loop)
    for child in loop.children:
        if body is not None and child.id == body.id:
            continue
        names |= _identifiers(child)
        bound |= _binder_names(child)
    return names - bound


def _binder_names(node: Node) -> set[str]:
    """Identifiers in the binding position of *node* or of a header clause it
    wraps (Go nests its range clause inside the ``for``)."""
    out: set[str] = set()
    for field in _BINDER_FIELDS:
        target = node.child_by_field_name(field)
        if target is not None:
            out |= _identifiers(target)
    if not out:
        for child in node.named_children:
            for field in _BINDER_FIELDS:
                target = child.child_by_field_name(field)
                if target is not None:
                    out |= _identifiers(target)
    return out


def _identifiers(node: Node) -> set[str]:
    out: set[str] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if not cur.children and cur.type.endswith("identifier"):
            text = cur.text
            if text:
                out.add(text.decode("utf-8", "replace"))
        stack.extend(cur.children)
    return out


def _descend(root: Node, kinds: frozenset[str], skip: frozenset[str]) -> list[Node]:
    found: list[Node] = []
    stack = [root]
    while stack:
        cur = stack.pop()
        if cur.type in kinds:
            found.append(cur)
        for child in cur.children:
            if child.type in skip:
                continue
            stack.append(child)
    return found


def _unwrap_container(node: Node, block_kinds: frozenset[str]) -> Node:
    """Descend through a single nested statement-container (Go's
    ``block`` -> ``statement_list``) to the node whose named children are the
    actual statements; returns *node* unchanged when it is already that node."""
    cur = node
    while True:
        named = cur.named_children
        if len(named) == 1 and named[0].type in block_kinds:
            cur = named[0]
        else:
            return cur


def _all_blocks(
    fn_node: Node,
    block_kinds: frozenset[str],
    scope_kinds: frozenset[str],
    loop_kinds: frozenset[str],
) -> list[tuple[Node, Node | None]]:
    """Every statement container in the function (body + nested), excluding the
    bodies of nested functions / lambdas, each paired with the innermost loop
    enclosing it (``None`` at loop-free depth)."""
    body = fn_node.child_by_field_name("body")
    if body is None:
        return []
    blocks: list[tuple[Node, Node | None]] = []
    stack: list[tuple[Node, Node | None]] = [(body, None)]
    while stack:
        node, loop = stack.pop()
        if node.type in block_kinds:
            blocks.append((node, loop))
        is_loop = node.type in loop_kinds
        body = node.child_by_field_name("body") if is_loop else None
        for child in node.children:
            if child.type in scope_kinds:
                continue
            # Only the loop's body repeats. A ``for ... else`` clause runs once.
            in_loop = node if (is_loop and body is not None and child.id == body.id) else loop
            stack.append((child, in_loop))
    return blocks


def _span_metrics(
    span: list[Node],
    decision_kinds: frozenset[str],
    jump_kinds: frozenset[str],
    scope_kinds: frozenset[str],
) -> tuple[int, bool]:
    """Decision-point count and jump presence within *span* (nested scopes are
    not descended into)."""
    decisions = 0
    has_jump = False
    for root in span:
        stack: list[Node] = [root]
        while stack:
            node = stack.pop()
            t = node.type
            if t in jump_kinds:
                has_jump = True
            if t in decision_kinds:
                decisions += 1
            for child in node.children:
                if child.type in scope_kinds:
                    continue
                stack.append(child)
    return decisions, has_jump
