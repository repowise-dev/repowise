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
the CFG/jump scan realises the single-exit predicate. Python only for now;
the jump classification is the sole language-specific piece.
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

# Python jump node types (a span containing any of these is not single-exit).
# The sole language-specific set here; generalised behind a dialect later.
_JUMP_KINDS: frozenset[str] = frozenset(
    {"return_statement", "raise_statement", "break_statement", "continue_statement"}
)
# Nested scopes whose statements/jumps belong to a different function.
_SCOPE_BOUNDARIES: frozenset[str] = frozenset(
    {"lambda", "function_definition", "async_function_definition"}
)

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
    body = fn_node.child_by_field_name("body")
    if body is None:
        return []

    def_lines, use_lines = _var_lines(analysis.def_use)
    decision_kinds = (
        lmap.branch_kinds
        | lmap.loop_kinds
        | lmap.case_kinds
        | lmap.catch_kinds
        | lmap.boolean_operator_kinds
    )

    out: list[Extraction] = []
    evaluated = 0
    for block in _all_blocks(fn_node):
        stmts = block.named_children
        n = len(stmts)
        is_body = block.id == body.id
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
                span = stmts[i : j + 1]
                decisions, has_jump = _span_metrics(span, decision_kinds)
                if has_jump or decisions < _MIN_CCN_REMOVED:
                    continue
                slice_nloc = sum(st.end_point[0] - st.start_point[0] + 1 for st in span)
                if slice_nloc < _MIN_SLICE_NLOC:
                    continue
                s = span[0].start_point[0] + 1
                e = span[-1].end_point[0] + 1
                params, returns = _infer_in_out(def_lines, use_lines, s, e)
                if len(params) > _MAX_PARAMS or len(returns) > _MAX_RETURNS:
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


def _all_blocks(fn_node: Node) -> list[Node]:
    """Every statement ``block`` in the function (body + nested), excluding the
    bodies of nested functions / lambdas."""
    body = fn_node.child_by_field_name("body")
    if body is None:
        return []
    blocks: list[Node] = []
    stack: list[Node] = [body]
    while stack:
        node = stack.pop()
        if node.type == "block":
            blocks.append(node)
        for child in node.children:
            if child.type in _SCOPE_BOUNDARIES:
                continue
            stack.append(child)
    return blocks


def _span_metrics(span: list[Node], decision_kinds: frozenset[str]) -> tuple[int, bool]:
    """Decision-point count and jump presence within *span* (nested scopes are
    not descended into)."""
    decisions = 0
    has_jump = False
    for root in span:
        stack: list[Node] = [root]
        while stack:
            node = stack.pop()
            t = node.type
            if t in _JUMP_KINDS:
                has_jump = True
            if t in decision_kinds:
                decisions += 1
            for child in node.children:
                if child.type in _SCOPE_BOUNDARIES:
                    continue
                stack.append(child)
    return decisions, has_jump
