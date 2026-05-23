"""Tree-sitter walker → CCN, max nesting, cognitive complexity.

One AST pass per file. For each function/method discovered at the top
level (or nested directly inside a class body / impl block) we recurse
through its body, accumulating:

- **CCN** — McCabe cyclomatic complexity. Start at 1; +1 per branch /
  loop / case / catch / boolean operator.
- **max_nesting** — deepest stack of nesting-contributing nodes within
  the function body.
- **cognitive** — SonarSource-style weighted score: each nesting node
  adds ``1 + current_depth`` (so deeper nesting hurts more); boolean
  operators add a flat +1; jumps (``return``/``break``/``continue``)
  do not contribute (kept simple in v1).

Anonymous functions (lambdas, arrow functions, closures) recurse for
their containing function's metrics — they do not produce their own
``FunctionComplexity`` row.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from .languages import LanguageNodeMap, get_language_map

if TYPE_CHECKING:
    from tree_sitter import Node

log = structlog.get_logger(__name__)


@dataclass
class ConditionComplexity:
    """One control-flow condition with its boolean-operator count.

    Emitted by the walker as a side-channel to ``FunctionComplexity``;
    does not affect CCN or cognitive complexity. Consumed by the
    ``complex_conditional`` biomarker.
    """

    line: int  # 1-indexed start line of the enclosing construct
    operator_count: int
    enclosing_construct: str  # "if" | "while" | "for" | "ternary" | "case"


@dataclass
class FunctionComplexity:
    """Per-function metrics produced by the walker."""

    name: str
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    ccn: int
    max_nesting: int
    cognitive: int
    nloc: int  # non-blank lines inside the body
    # Number of top-level body sub-blocks whose internal nesting reached
    # ≥ 2 — used by ``bumpy_road``. A flat function has 0 bumps.
    bumps: int = 0
    # Number of declared parameters on the function signature — used by
    # ``primitive_obsession``. Counted via the tree-sitter ``parameters``
    # field; 0 when the language lacks an explicit list or extraction fails.
    param_count: int = 0
    # Per-condition boolean-operator counts collected during the walk.
    # Empty when no branch/loop carries compound boolean expressions.
    complex_conditions: list[ConditionComplexity] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.complex_conditions is None:
            self.complex_conditions = []


def _find_name(node: Node) -> str:
    """Best-effort: return the text of the first identifier child."""
    # Search a couple of common field names first.
    for field_name in ("name", "identifier"):
        child = node.child_by_field_name(field_name)
        if child is not None and child.text is not None:
            return child.text.decode("utf-8", errors="replace")
    for child in node.children:
        if (
            child.type in ("identifier", "property_identifier", "field_identifier")
            and child.text is not None
        ):
            return child.text.decode("utf-8", errors="replace")
    return "<anonymous>"


def _count_nloc(node: Node, source: bytes) -> int:
    """Return the count of non-blank lines spanned by *node*."""
    start = node.start_point[0]
    end = node.end_point[0]
    if end < start:
        return 0
    try:
        snippet = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    except Exception:
        return end - start + 1
    return sum(1 for line in snippet.splitlines() if line.strip())


def _is_boolean_operator(node: Node, lmap: LanguageNodeMap) -> bool:
    """True if this node represents a logical ``&&`` / ``||`` operator."""
    if node.type in lmap.boolean_operator_kinds:
        return True
    if node.type in lmap.boolean_operator_text_kinds:
        # The operator child carries the literal token text.
        for child in node.children:
            if child.text is None:
                continue
            tok = child.text
            if tok in (b"&&", b"||", b"and", b"or"):
                return True
    return False


_BODY_FIELD_NAMES = (
    "body",
    "consequence",
    "alternative",
    "else_clause",
    "block",
)


def _count_boolean_ops_in_condition(node: Node, lmap: LanguageNodeMap) -> int:
    """Count ``&&`` / ``||`` / ``and`` / ``or`` operators in a condition.

    Walks the subtree rooted at *node* but does not descend into nested
    function bodies (lambdas / closures used as condition values are
    rare and would skew the count).
    """
    if node is None:
        return 0
    count = 0
    stack: list[Node] = [node]
    while stack:
        cur = stack.pop()
        if cur is not node and cur.type in lmap.function_kinds:
            continue
        if _is_boolean_operator(cur, lmap):
            count += 1
        for child in cur.children:
            stack.append(child)
    return count


def _enclosing_construct(node: Node, lmap: LanguageNodeMap) -> str:
    if node.type in lmap.loop_kinds:
        return "for" if "for" in node.type else "while"
    if node.type in lmap.case_kinds:
        return "case"
    if node.type in lmap.catch_kinds:
        return "catch"
    if node.type in lmap.branch_kinds:
        if "ternary" in node.type or "conditional" in node.type:
            return "ternary"
        return "if"
    return "if"


def _condition_subtrees(node: Node) -> list[Node]:
    """Best-effort: pull the *condition* parts out of a branch/loop node.

    Prefers the tree-sitter ``condition`` named field where exposed
    (Python, TS, Java, Rust, Go all use it for most branch shapes).
    Falls back to all direct children except recognised body fields and
    syntactic punctuation.
    """
    cond = node.child_by_field_name("condition")
    if cond is not None:
        return [cond]
    # Switch case value (TS, Java)
    value = node.child_by_field_name("value")
    if value is not None and "case" in node.type:
        return [value]
    # Fallback: direct children minus bodies / blocks.
    body_nodes: set[int] = set()
    for fname in _BODY_FIELD_NAMES:
        child = node.child_by_field_name(fname)
        if child is not None:
            body_nodes.add(child.id)
    out: list[Node] = []
    for child in node.children:
        if child.id in body_nodes:
            continue
        if not child.is_named:
            continue
        if child.type in ("block", "compound_statement", "statement_block"):
            continue
        out.append(child)
    return out


def _walk_function_body(
    body_node: Node,
    lmap: LanguageNodeMap,
) -> tuple[int, int, int, int, list[ConditionComplexity]]:
    """Recursive AST walk. Returns (ccn, max_nesting, cognitive, bumps,
    complex_conditions).

    Starts CCN at 1 (the entry path). Nested function bodies are
    skipped — they will (or already did) produce their own
    ``FunctionComplexity``.

    ``bumps`` counts how many *direct* children of the function body
    contain nested control flow that reaches a depth of ≥ 2. A function
    with several heavy independent branches is "bumpy" in
    CodeScene/SonarSource terminology.

    ``complex_conditions`` is an additive side-channel — collected for
    every branch/loop/case construct encountered. The CCN / cognitive
    accumulation logic is unchanged.
    """

    ccn = 1
    max_nesting = 0
    cognitive = 0
    bumps = 0
    conditions: list[ConditionComplexity] = []

    def _recurse(node: Node, depth: int) -> None:
        nonlocal ccn, max_nesting, cognitive

        # Don't descend into nested function bodies — they're walked
        # separately at the top level. Lambdas / arrow functions DO
        # contribute to the enclosing function's complexity.
        if node.type in lmap.function_kinds:
            return

        nesting_increment = 0
        ccn_increment = 0

        if (
            node.type in lmap.branch_kinds
            or node.type in lmap.loop_kinds
            or node.type in lmap.case_kinds
            or node.type in lmap.catch_kinds
        ):
            ccn_increment = 1
            nesting_increment = 1
            # Side-channel: count compound boolean ops in this
            # construct's condition. Does not affect ccn/cognitive
            # (boolean operators are still tallied independently by
            # the regular recursion below).
            op_count = 0
            for sub in _condition_subtrees(node):
                op_count += _count_boolean_ops_in_condition(sub, lmap)
            if op_count > 0:
                conditions.append(
                    ConditionComplexity(
                        line=node.start_point[0] + 1,
                        operator_count=op_count,
                        enclosing_construct=_enclosing_construct(node, lmap),
                    )
                )
        elif node.type in lmap.try_kinds:
            # TRY opens a nesting level but does not branch on its own.
            nesting_increment = 1
        elif node.type in lmap.switch_kinds:
            # Switch opens nesting; each case contributes its own +1.
            nesting_increment = 1
        elif _is_boolean_operator(node, lmap):
            ccn_increment = 1

        ccn += ccn_increment
        new_depth = depth + nesting_increment
        if nesting_increment:
            # SonarSource cognitive: each nesting node adds (1 + depth).
            cognitive += 1 + depth
        elif ccn_increment:
            # Flat +1 for boolean operators (no nesting impact).
            cognitive += 1

        if new_depth > max_nesting:
            max_nesting = new_depth

        for child in node.children:
            _recurse(child, new_depth)

    for child in body_node.children:
        # Per-child peak depth: temporarily swap max_nesting out so we
        # can read just this child's contribution, then restore.
        outer_max = max_nesting
        max_nesting = 0
        _recurse(child, 0)
        child_peak = max_nesting
        max_nesting = max(outer_max, child_peak)
        if child_peak >= 2:
            bumps += 1

    return ccn, max_nesting, cognitive, bumps, conditions


def _collect_function_nodes(root: Node, lmap: LanguageNodeMap) -> list[Node]:
    """All function / method definition nodes in the file.

    Iterative pre-order traversal. We descend into class / module
    bodies but do **not** recurse below a function — nested defs are
    rare and treated as their own top-level entry. Lambda kinds are
    skipped (they're walked inline as part of their enclosing fn).
    """
    out: list[Node] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type in lmap.function_kinds:
            out.append(node)
            # Don't descend further — nested defs roll up into the
            # enclosing fn's complexity via _walk_function_body
            # (which skips nested function_kinds).
            continue
        for child in node.children:
            stack.append(child)
    return out


def walk_file_complexity(
    abs_path: str,
    language: str,
    source: bytes,
) -> list[FunctionComplexity]:
    """Walk one file's AST. Returns one ``FunctionComplexity`` per fn.

    Returns an empty list when:
      - the language is unsupported (no entry in ``LANGUAGE_MAPS``)
      - the tree-sitter language package isn't installed
      - parsing fails
    """
    lmap = get_language_map(language)
    if lmap is None:
        return []

    try:
        from tree_sitter import Parser

        # Reuse the ingestion parser's language registry. Importing
        # lazily avoids pulling tree-sitter at module load time when
        # health is run from a context where it isn't installed.
        from repowise.core.ingestion.parser import _get_language
    except Exception as exc:
        log.debug("complexity_walker_import_failed", error=str(exc))
        return []

    grammar = _get_language(language)
    if grammar is None:
        return []

    try:
        parser = Parser(grammar)
        tree = parser.parse(source)
    except Exception as exc:
        log.debug("complexity_walker_parse_failed", path=abs_path, error=str(exc))
        return []

    results: list[FunctionComplexity] = []
    for fn_node in _collect_function_nodes(tree.root_node, lmap):
        body = fn_node.child_by_field_name("body") or fn_node
        ccn, max_nest, cognitive, bumps, conditions = _walk_function_body(body, lmap)
        results.append(
            FunctionComplexity(
                name=_find_name(fn_node),
                start_line=fn_node.start_point[0] + 1,
                end_line=fn_node.end_point[0] + 1,
                ccn=ccn,
                max_nesting=max_nest,
                cognitive=cognitive,
                nloc=_count_nloc(body, source),
                bumps=bumps,
                param_count=_count_parameters(fn_node),
                complex_conditions=conditions,
            )
        )
    return results


def _count_parameters(fn_node: Node) -> int:
    """Best-effort parameter-list size for *fn_node*.

    Looks at tree-sitter ``parameters`` / ``parameter_list`` / ``parameters_list`` fields and counts non-punctuation
    children. Returns 0 when no parameter list is found.
    """
    params = fn_node.child_by_field_name("parameters")
    if params is None:
        for child in fn_node.children:
            if child.type in ("parameters", "parameter_list", "formal_parameters"):
                params = child
                break
    if params is None:
        return 0
    count = 0
    for child in params.children:
        if child.type in ("(", ")", ",", "self", "cls", ":", "*", "**"):
            continue
        if child.is_named:
            count += 1
    return count
