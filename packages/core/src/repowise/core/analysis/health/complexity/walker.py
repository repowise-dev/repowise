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
their containing function's metrics when they are nested in a named
function. Module-level lambdas, such as route callbacks, produce their
own ``FunctionComplexity`` row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from ..perf.dialects import PERF_DIALECTS
from ..perf.io_boundaries import collect_io_names
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
    # Runs of ≥2 consecutive assertion statements within the body, each
    # ``(start_line, end_line, count)``. Populated only for languages whose
    # ``LanguageNodeMap`` opts into assertion detection (``assert_kinds`` /
    # ``assert_call_kinds``). Consumed by the test-quality biomarkers.
    assertion_blocks: list[tuple[int, int, int]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.complex_conditions is None:
            self.complex_conditions = []
        if self.assertion_blocks is None:
            self.assertion_blocks = []


@dataclass
class ClassComplexity:
    """Per-class aggregate metrics produced by the walker.

    Emitted only for languages whose ``LanguageNodeMap`` opts into
    class-level analysis (``class_kinds`` non-empty). Consumed by the
    ``low_cohesion`` (LCOM4) and ``god_class`` biomarkers.

    ``lcom4`` is the LCOM4 cohesion metric — the number of connected
    components in the graph whose nodes are the class's methods and whose
    edges link methods that share an instance field or call one another.
    ``1`` means a fully cohesive class (or "no signal": see the safety
    valve in ``_compute_lcom4``). Higher values mean the class splinters
    into unrelated method clusters.
    """

    name: str
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    method_count: int
    total_nloc: int
    methods: list[FunctionComplexity]
    lcom4: int = 1
    max_method_ccn: int = 0
    field_count: int = 0


@dataclass(frozen=True)
class ErrorHandlingHit:
    """One error-handling anti-pattern occurrence in a file.

    Collected by the walker's whole-tree pass (see
    ``_collect_error_handling``) and consumed by the ``error_handling``
    biomarker. ``kind`` is one of:

    - ``swallowed_catch`` — a catch/except whose body has no real handling
      (empty, or only ``pass`` / ``...`` / a docstring / comments).
    - ``bare_except`` — Python ``except:`` / ``except Exception:`` /
      ``except BaseException:`` (catch-all) regardless of body.
    - ``unsafe_unwrap`` — Rust ``.unwrap()`` / ``.expect()`` /
      ``.unwrap_unchecked()`` calls and ``panic!`` / ``unreachable!`` /
      ``todo!`` / ``unimplemented!`` macros (latent panic-on-error).
    - ``go_swallow`` — Go empty ``if err != nil {}`` block, or a
      blank-identifier discard of a multi-return call's value.
    """

    kind: str
    line: int  # 1-indexed


@dataclass(frozen=True)
class PerfHit:
    """One performance-risk occurrence in a file (the ``performance`` dimension).

    Collected by the walker's whole-tree perf pass (see
    ``_collect_perf_hits``) and lifted into findings by the perf biomarkers.
    Precision-first: every hit is loop-body-scoped and execution-sink-gated so
    an unsupported language / parse failure / builder-only call yields nothing.

    ``kind`` is one of:

    - ``io_in_loop`` — an execution sink at an I/O boundary (db / network /
      filesystem / subprocess) inside a real, data-dependent loop body. The
      boundary kind is carried in ``detail``.
    - ``string_concat_in_loop`` — string accumulation (``+=`` onto a string)
      inside a loop, instead of a buffer / ``join``.
    - ``blocking_sync_in_async`` — a known blocking sync call (``time.sleep`` /
      sync ``requests`` / ``subprocess`` / ``os.system`` / bare ``open``) inside
      an ``async def``, not awaited. ``detail`` carries the offending API.
    """

    kind: str
    line: int  # 1-indexed
    function: str | None = None
    detail: str = ""
    # Reachability path for a cross-function hit: the resolved symbol-node
    # chain ``A -> B -> ... -> sink`` (PR4). Empty for same-function hits —
    # its emptiness is what distinguishes the two cases downstream.
    path: tuple[str, ...] = ()


@dataclass(frozen=True)
class PerfFnFacts:
    """Per-function facts the cross-function perf bridge needs (PR4).

    Collected on the same whole-tree perf pass as ``PerfHit``. Two signals:

    - ``loop_call_targets`` — the rightmost names of calls made inside a loop
      body that are NOT themselves a direct I/O sink (those are already a
      same-function ``io_in_loop`` hit). Each is a candidate helper whose body
      might reach a sink transitively — the entry points of the reachability
      walk.
    - ``bare_sink_kind`` — the boundary kind of an I/O sink this function
      executes at ``loop_depth 0`` (its own top level, not nested in a loop),
      else ``None``. A function with a bare sink is a *reachability target*:
      when a loop in another function calls into it, that sink runs once per
      iteration. The ``loop_depth 0`` requirement is deliberate — a sink the
      function runs inside its own loop is already counted there.

    ``func_start`` is the 1-indexed line of the enclosing function's ``def``
    (0 for module scope, which maps to the synthetic ``::__module__`` node);
    the bridge resolves it to a graph symbol node by line containment.
    """

    function: str | None
    func_start: int
    # ``(callee_name, call_line)`` pairs for the loop-nested non-sink calls.
    loop_call_targets: tuple[tuple[str, int], ...]
    bare_sink_kind: str | None


@dataclass
class FileComplexity:
    """Walker output for one file: per-function and per-class metrics.

    ``walk_file`` returns this; ``walk_file_complexity`` is the
    backward-compatible thin wrapper that returns only ``functions``.
    """

    functions: list[FunctionComplexity]
    classes: list[ClassComplexity]
    file_nloc: int = 0
    # Error-handling anti-pattern occurrences (whole-tree pass). Empty when
    # the language is unsupported or parsing failed — "no signal", never a
    # false positive.
    error_handling_hits: list[ErrorHandlingHit] = field(default_factory=list)
    # Performance-risk occurrences (whole-tree perf pass). Empty when the
    # language opts out of the perf pass (no ``call_kinds``) or parsing failed.
    perf_hits: list[PerfHit] = field(default_factory=list)
    # Names imported from an I/O-typed library in this file, mapped to their
    # boundary kind (db / network / filesystem / subprocess / lock). The
    # per-file import bridge; PR4's cross-function reachability consumes it.
    io_boundary_names: dict[str, str] = field(default_factory=dict)
    # Per-function facts feeding PR4's cross-function N+1 detection: which
    # callees each function invokes inside a loop body, and whether the
    # function holds a bare (non-loop) I/O sink. Empty when the language opts
    # out of the perf pass. Consumed by ``perf.crossfn``, not by a biomarker.
    perf_fn_facts: list[PerfFnFacts] = field(default_factory=list)


# Leaf node types that carry a declared name at the bottom of a C/C++
# ``declarator`` chain.
_DECLARATOR_NAME_KINDS = frozenset(
    {"identifier", "field_identifier", "type_identifier", "qualified_identifier"}
)


def _find_name(node: Node) -> str:
    """Best-effort: return the text of the first identifier child."""
    # Search a couple of common field names first.
    for field_name in ("name", "identifier"):
        child = node.child_by_field_name(field_name)
        if child is not None and child.text is not None:
            return child.text.decode("utf-8", errors="replace")
    # C / C++: the function name is not a direct child but nested inside a
    # ``declarator`` chain (``function_definition → function_declarator →
    # field_identifier``). Languages with a ``name`` field never reach here.
    decl = node.child_by_field_name("declarator")
    hops = 0
    while decl is not None and hops < 6:
        if decl.type in _DECLARATOR_NAME_KINDS and decl.text is not None:
            return decl.text.decode("utf-8", errors="replace")
        decl = decl.child_by_field_name("declarator") or decl.child_by_field_name("name")
        hops += 1
    for child in node.children:
        if (
            child.type in ("identifier", "property_identifier", "field_identifier")
            and child.text is not None
        ):
            return child.text.decode("utf-8", errors="replace")
    return "<anonymous>"


def _node_text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _find_assigned_lambda_name(node: Node) -> str | None:
    parent = node.parent
    while parent is not None:
        if parent.type == "variable_declarator":
            name = parent.child_by_field_name("name")
            if name is not None and name.text is not None:
                return _node_text(name)
        if parent.type in {"assignment_expression", "assignment_pattern"}:
            left = parent.child_by_field_name("left")
            if left is None:
                left = next((child for child in parent.children if child is not node), None)
            if left is not None and left.text is not None:
                return _node_text(left)
        if parent.type not in {"parenthesized_expression", "as_expression", "satisfies_expression"}:
            return None
        parent = parent.parent
    return None


def _find_call_callback_callee(node: Node) -> str | None:
    parent = node.parent
    while parent is not None and parent.type in {
        "parenthesized_expression",
        "as_expression",
        "satisfies_expression",
    }:
        parent = parent.parent
    if parent is None or parent.type != "arguments":
        return None
    call = parent.parent
    if call is None or call.type != "call_expression":
        return None
    callee = call.child_by_field_name("function")
    if callee is None or callee.text is None:
        return None
    return " ".join(_node_text(callee).split())


_TEST_SUITE_CALLBACK_CALLEES = frozenset({"describe", "context", "suite"})


def _is_test_suite_callback(node: Node, lmap: LanguageNodeMap) -> bool:
    if node.type not in lmap.lambda_kinds:
        return False
    callee_text = _find_call_callback_callee(node)
    if callee_text is None:
        return False
    return any(part in _TEST_SUITE_CALLBACK_CALLEES for part in callee_text.split("."))


def _find_function_entry_name(node: Node, lmap: LanguageNodeMap) -> str:
    if node.type not in lmap.lambda_kinds:
        return _find_name(node)
    assigned = _find_assigned_lambda_name(node)
    if assigned:
        return assigned
    if callee := _find_call_callback_callee(node):
        return f"{callee} callback"
    return f"<anonymous@{node.start_point[0] + 1}>"


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


def _count_file_nloc(source: bytes) -> int:
    """Count non-blank lines in *source* bytes (plain fallback, no tree)."""
    try:
        text = source.decode("utf-8", errors="replace")
    except Exception:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


def _count_file_nloc_tree(root_node: Node, source: bytes) -> int:
    """Count lines that have at least one non-comment token.

    Lines where all content is inside comment nodes are excluded; lines
    with real code plus a trailing comment still count.
    """
    try:
        lines = source.decode("utf-8", errors="replace").splitlines()
    except Exception:
        return 0
    code_lines: set[int] = set()
    stack = [root_node]
    while stack:
        node = stack.pop()
        if "comment" in node.type:
            continue
        if not node.children and node.start_byte < node.end_byte:
            for line in range(node.start_point[0], node.end_point[0] + 1):
                if line < len(lines) and lines[line].strip():
                    code_lines.add(line)
        else:
            for child in node.children:
                stack.append(child)
    return len(code_lines)


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


def _collect_case_children(node: Node, lmap: LanguageNodeMap) -> list[Node]:
    """Collect all case/arm nodes from a switch/match node.

    In Rust, ``match_expression`` contains a ``match_block`` which in
    turn holds the ``match_arm`` nodes. Other languages may place cases
    directly under the switch node.  This helper handles both layouts.
    """
    cases: list[Node] = []
    for child in node.children:
        if child.type in lmap.case_kinds:
            cases.append(child)
        else:
            # Descend one level into intermediate wrapper nodes (e.g.
            # ``match_block``) that are not themselves control flow.
            for grandchild in child.children:
                if grandchild.type in lmap.case_kinds:
                    cases.append(grandchild)
    return cases


def _is_flat_match(node: Node, lmap: LanguageNodeMap) -> bool:
    """Return True if *node* is a match/switch with only simple arms.

    A "flat" match has arms whose bodies are single expressions without
    nested control flow (no ``if``, ``match``, ``for``, ``while``,
    ``loop``, ``if_let``, ``while_let`` expressions, and no ``block``
    with multiple statements).  Flat matches contribute 1 CCN point for
    the match keyword itself but do NOT count each arm individually.
    """
    complex_types = lmap.branch_kinds | lmap.loop_kinds | lmap.switch_kinds
    cases = _collect_case_children(node, lmap)
    if not cases:
        return False
    return all(not _subtree_contains_complex(arm, complex_types) for arm in cases)


def _subtree_contains_complex(arm_node: Node, complex_types: frozenset[str]) -> bool:
    """Return True if *arm_node*'s subtree contains complex control flow.

    A ``block`` with more than one statement is also considered complex.
    """
    stack: list[Node] = list(arm_node.children)
    while stack:
        cur = stack.pop()
        if cur.type in complex_types:
            return True
        # A block with multiple named children (statements) is complex.
        if cur.type == "block":
            named_children = [c for c in cur.children if c.is_named]
            if len(named_children) > 1:
                return True
        for child in cur.children:
            stack.append(child)
    return False


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

    # Track match_expression nodes identified as "flat" so their arms
    # are not individually counted as branch points.
    flat_match_ids: set[int] = set()

    def _recurse(node: Node, depth: int) -> None:
        nonlocal ccn, max_nesting, cognitive

        # Don't descend into nested function bodies — they're walked
        # separately at the top level. Lambdas / arrow functions DO
        # contribute to the enclosing function's complexity.
        if node.type in lmap.function_kinds:
            return

        nesting_increment = 0
        ccn_increment = 0

        # Check if this is a case/arm inside a flat match — skip it.
        # In Rust the parent chain is match_arm → match_block → match_expression,
        # so we check both parent and grandparent.
        _parent = node.parent
        is_flat_match_arm = False
        if (
            node.type in lmap.case_kinds
            and _parent is not None
            and (
                _parent.id in flat_match_ids
                or (_parent.parent is not None and _parent.parent.id in flat_match_ids)
            )
        ):
            is_flat_match_arm = True

        if is_flat_match_arm:
            # Flat match arms: no CCN increment, no nesting increment.
            pass
        elif (
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
            # Detect flat match: all arms are simple single-expression arms.
            if _is_flat_match(node, lmap):
                flat_match_ids.add(node.id)
                # Flat match: count 1 CCN point for the match itself,
                # open a nesting level, but arms won't be counted.
                ccn_increment = 1
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


# ----------------------------------------------------------------------
# Assertion-block detection (test-quality smells)
# ----------------------------------------------------------------------

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


def _collect_function_nodes(root: Node, lmap: LanguageNodeMap) -> list[Node]:
    """All function / method definition nodes in the file.

    Iterative pre-order traversal. We descend into class / module
    bodies but do **not** recurse below a function or lambda. Lambdas found
    before any function boundary are module-level executable units (for
    example route callbacks) and get their own entry; lambdas inside an
    already-collected function still roll up into that function.
    """
    out: list[Node] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type in lmap.lambda_kinds and _is_test_suite_callback(node, lmap):
            stack.extend(node.children)
            continue
        if node.type in lmap.function_kinds or node.type in lmap.lambda_kinds:
            out.append(node)
            continue
        for child in node.children:
            stack.append(child)
    return out


# ----------------------------------------------------------------------
# Error-handling anti-pattern detection (error_handling biomarker)
# ----------------------------------------------------------------------
# Ported from the bench-validated detector (24/24 fixtures across 11
# languages). Precision-first: every detector targets the unambiguous
# shape and degrades to "no signal" rather than guessing.

# Block-like body node types of a catch/except clause.
_EH_BLOCK_KINDS = frozenset({"block", "statement_block", "compound_statement"})
# Statement node types that count as "no real handling" inside a catch body.
_EH_TRIVIAL_STMT = frozenset({"comment", "pass_statement", "line_comment", "block_comment"})
# Rust: each of these is a latent panic-on-error.
_RUST_UNWRAP_METHODS = frozenset({"unwrap", "expect", "unwrap_unchecked"})
_RUST_PANIC_MACROS = frozenset({"panic", "unreachable", "todo", "unimplemented"})


def _eh_text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _eh_named(node: Node) -> list[Node]:
    return [c for c in node.children if c.is_named]


def _eh_find_body_block(clause: Node) -> Node | None:
    """The block-like child of a catch/except clause (its handler body)."""
    for c in clause.children:
        if c.type in _EH_BLOCK_KINDS:
            return c
    # Kotlin catch_block / some grammars nest the block one level down.
    for c in clause.children:
        for g in c.children:
            if g.type in _EH_BLOCK_KINDS:
                return g
    return None


def _eh_is_trivial_stmt(stmt: Node, language: str) -> bool:
    if stmt.type in _EH_TRIVIAL_STMT:
        return True
    if language == "python" and stmt.type == "expression_statement":
        inner = _eh_named(stmt)
        if not inner:
            return True
        # ``...`` or a docstring as the entire statement.
        if inner[0].type in ("ellipsis", "string"):
            return True
    return False


def _eh_body_is_swallowed(block: Node, language: str) -> bool:
    real = [c for c in _eh_named(block) if not _eh_is_trivial_stmt(c, language)]
    return len(real) == 0


def _eh_is_bare_except(clause: Node) -> bool:
    """Python ``except:`` / ``except Exception:`` / ``except BaseException:``."""
    kids = [c for c in clause.children if c.type != "comment"]
    after = [c for c in kids if c.type not in ("except", ":") and c.type not in _EH_BLOCK_KINDS]
    if not after:
        return True  # bare ``except:``
    # ``except Exception:`` / ``except BaseException:`` (single catch-all
    # identifier — a tuple of specific types or an ``as`` binding on a
    # specific type does not match).
    first = after[0]
    return first.type == "identifier" and _eh_text(first) in ("Exception", "BaseException")


def _eh_rust_hit(node: Node) -> bool:
    """True when *node* is an unwrap/expect call or a panic-family macro."""
    if node.type == "call_expression":
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "field_expression":
            fld = fn.child_by_field_name("field")
            return fld is not None and _eh_text(fld) in _RUST_UNWRAP_METHODS
        return False
    if node.type == "macro_invocation":
        mac = node.child_by_field_name("macro")
        return mac is not None and _eh_text(mac) in _RUST_PANIC_MACROS
    return False


def _eh_go_cond_is_err_check(cond_text: str) -> bool:
    t = cond_text.replace(" ", "")
    return "err!=nil" in t or "err==nil" in t


def _eh_go_hit(node: Node) -> bool:
    """Go: empty ``if err != nil {}`` or blank-identifier discard of a call."""
    if node.type == "if_statement":
        cond = node.child_by_field_name("condition")
        cons = node.child_by_field_name("consequence")
        return (
            cond is not None
            and cons is not None
            and _eh_go_cond_is_err_check(_eh_text(cond))
            and len(_eh_named(cons)) == 0
        )
    if node.type in ("short_var_declaration", "assignment_statement"):
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None:
            return False
        left_kids = _eh_named(left)
        has_blank = any(c.type == "blank_identifier" or _eh_text(c) == "_" for c in left_kids)
        right_is_call = any(c.type == "call_expression" for c in _eh_named(right)) or (
            right.type == "expression_list"
            and any(c.type == "call_expression" for c in _eh_named(right))
        )
        # Multi-return discard: ≥2 LHS targets, a call on the RHS, a blank present.
        return has_blank and len(left_kids) >= 2 and right_is_call
    return False


def _collect_error_handling(
    root: Node, language: str, lmap: LanguageNodeMap
) -> list[ErrorHandlingHit]:
    """Whole-tree pass: every error-handling anti-pattern with its line.

    Catch-clause shapes reuse the ``LanguageNodeMap`` catch kinds (Python
    ``except_clause``; JS/TS/Java/C++/C# ``catch_clause``; Kotlin
    ``catch_block``); Rust and Go have no catch nodes and use their own
    recognizers. Module-level code is covered too — anti-patterns are not
    confined to function bodies.
    """
    hits: list[ErrorHandlingHit] = []
    catch_kinds = lmap.catch_kinds
    is_python = language == "python"
    is_rust = language == "rust"
    is_go = language == "go"
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if catch_kinds and node.type in catch_kinds:
            block = _eh_find_body_block(node)
            if block is not None and _eh_body_is_swallowed(block, language):
                hits.append(ErrorHandlingHit("swallowed_catch", node.start_point[0] + 1))
            if is_python and _eh_is_bare_except(node):
                hits.append(ErrorHandlingHit("bare_except", node.start_point[0] + 1))
        elif is_rust and _eh_rust_hit(node):
            hits.append(ErrorHandlingHit("unsafe_unwrap", node.start_point[0] + 1))
        elif is_go and _eh_go_hit(node):
            hits.append(ErrorHandlingHit("go_swallow", node.start_point[0] + 1))
        stack.extend(node.children)
    hits.sort(key=lambda h: h.line)
    return hits


# ----------------------------------------------------------------------
# Performance-risk detection (io_in_loop / string_concat_in_loop /
# blocking_sync_in_async — the ``performance`` health dimension)
# ----------------------------------------------------------------------
# One whole-tree pass mirroring ``_collect_error_handling`` but carrying the
# per-node context the perf signal needs: loop depth, in-async, and the
# enclosing function name. Two non-negotiable refinements (Phase-0 gate: they
# took precision from 49% to 79%) are baked in:
#   1. Loop-BODY scoping — only calls under a loop node's ``body`` field run
#      per-iteration; a call in the ``for x in <iterable>`` header runs once.
#   2. Constant-bound-loop skip — ``for _ in range(<int literals>)`` and loops
#      over literal / ALL_CAPS-named-constant collections are not data-dependent.

def _perf_func_name(node: Node) -> str | None:
    nm = node.child_by_field_name("name")
    if nm is not None and nm.text:
        return nm.text.decode("utf-8", "replace")
    return None


def _collect_perf_hits(
    root: Node, language: str, lmap: LanguageNodeMap
) -> tuple[list[PerfHit], dict[str, str], list[PerfFnFacts]]:
    """Whole-tree perf pass → ``(hits, io_boundary_names, fn_facts)``.

    Iterative DFS carrying ``(node, loop_depth, in_async, func_name,
    func_start)`` — the proven Phase-0 shape, extended with the enclosing
    function's start line so per-function facts can be keyed to a graph symbol
    node. Loop-body scoping and constant-loop skipping are applied so only
    genuinely per-iteration calls are flagged. Returns nothing for languages
    that opt out of the perf pass (empty ``call_kinds``).

    Alongside the same-function ``hits``, it accumulates ``fn_facts`` (one
    :class:`PerfFnFacts` per enclosing function that has any loop-nested call
    or a bare sink) — the input to PR4's cross-function reachability.
    """
    call_kinds = lmap.call_kinds
    dialect = PERF_DIALECTS.get(language)
    if not call_kinds or dialect is None:
        return [], {}, []

    io_names = collect_io_names(root, language)
    has_db_import = any(k == "db" for k in io_names.values())
    markers = dialect.markers
    do_string_concat = "string_concat_in_loop" in markers
    do_blocking = "blocking_sync_in_async" in markers
    do_loop_call_marker = "regex_compile_in_loop" in markers
    do_loop_stmt_marker = "defer_in_loop" in markers
    loop_kinds = lmap.loop_kinds
    fn_kinds = lmap.function_kinds
    lambda_kinds = lmap.lambda_kinds
    async_fn_kinds = lmap.async_function_kinds

    hits: list[PerfHit] = []
    # Per-enclosing-function accumulators keyed by the function's start line
    # (0 = module scope). ``(name, loop_targets{name->min line}, [bare_sink])``.
    fn_acc: dict[int, tuple[str | None, dict[str, int], list[str | None]]] = {}

    def _acc(func_start: int, func_name: str | None) -> tuple[dict[str, int], list[str | None]]:
        entry = fn_acc.get(func_start)
        if entry is None:
            entry = (func_name, {}, [None])
            fn_acc[func_start] = entry
        return entry[1], entry[2]

    # (node, loop_depth, in_async, func_name, func_start)
    stack: list[tuple[Node, int, bool, str | None, int]] = [(root, 0, False, None, 0)]
    while stack:
        node, loop_depth, in_async, func_name, func_start = stack.pop()
        t = node.type

        is_loop = t in loop_kinds
        if is_loop and dialect.is_constant_loop(node):
            is_loop = False

        entering_fn = t in fn_kinds or t in lambda_kinds
        is_async_fn = t in async_fn_kinds or (entering_fn and dialect.is_async_fn(node))
        next_async = True if is_async_fn else (False if entering_fn else in_async)
        next_func = func_name
        next_start = func_start
        if t in fn_kinds:
            next_func = _perf_func_name(node) or func_name
            next_start = node.start_point[0] + 1

        if t in call_kinds:
            method = dialect.callee_method_name(node) or ""
            root_name = dialect.callee_root_name(node) or ""
            parent = node.parent
            awaited = parent is not None and "await" in parent.type
            line = node.start_point[0] + 1
            kind = dialect.sink_kind(
                root_name,
                method,
                awaited=awaited,
                is_attribute=dialect.callee_is_attribute(node),
                io_names=io_names,
                has_db_import=has_db_import,
            )
            if loop_depth >= 1:
                if kind is not None:
                    hits.append(PerfHit("io_in_loop", line, next_func, kind))
                else:
                    marker = (
                        dialect.loop_call_marker(root_name, method, node)
                        if do_loop_call_marker
                        else None
                    )
                    if marker is not None:
                        hits.append(PerfHit(marker, line, next_func, ""))
                    elif method:
                        # A loop-nested call to a non-sink helper: a candidate
                        # entry for cross-function reachability (PR4). Keep the
                        # first line we see the helper called at, for the finding.
                        targets = _acc(next_start, next_func)[0]
                        if method not in targets:
                            targets[method] = line
            elif kind is not None:
                # A sink at loop_depth 0 makes this function a reachability
                # target: a loop elsewhere calling into it runs the sink N times.
                sink_slot = _acc(next_start, next_func)[1]
                if sink_slot[0] is None:
                    sink_slot[0] = kind
            if do_blocking and in_async and not awaited:
                api = dialect.blocking_sync_api(root_name, method)
                if api is not None:
                    hits.append(PerfHit("blocking_sync_in_async", line, next_func, api))
        else:
            if do_blocking and in_async:
                # A non-call member read that blocks in async (C# ``task.Result``).
                mem = dialect.async_blocking_member(node)
                if mem is not None:
                    hits.append(
                        PerfHit("blocking_sync_in_async", node.start_point[0] + 1, next_func, mem)
                    )
            if loop_depth >= 1:
                if do_string_concat and dialect.is_string_concat(node):
                    hits.append(
                        PerfHit("string_concat_in_loop", node.start_point[0] + 1, next_func, "")
                    )
                elif do_loop_stmt_marker:
                    sm = dialect.loop_stmt_marker(node)
                    if sm is not None:
                        hits.append(PerfHit(sm, node.start_point[0] + 1, next_func, ""))

        if is_loop:
            # Only the loop BODY runs per-iteration; the ``for x in <iterable>``
            # header / ``while <cond>`` condition runs once.
            body = node.child_by_field_name("body")
            if body is not None:
                # NB: tree-sitter Node wrappers are not singletons, so compare
                # with ``==`` (identity by tree + byte range), never ``is``.
                for c in node.children:
                    cd = loop_depth + 1 if c == body else loop_depth
                    stack.append((c, cd, next_async, next_func, next_start))
            else:
                for c in node.children:
                    stack.append((c, loop_depth + 1, next_async, next_func, next_start))
        else:
            for c in node.children:
                stack.append((c, loop_depth, next_async, next_func, next_start))

    # Dedup chained sinks: ``result.scalars().all()`` parses as two call nodes
    # on one line (the ``.scalars()`` sink and the ``.all()`` materializer) —
    # one logical query, one finding. Collapse per (kind, line, function).
    seen: set[tuple[str, int, str | None]] = set()
    deduped: list[PerfHit] = []
    for h in hits:
        key = (h.kind, h.line, h.function)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)
    deduped.sort(key=lambda h: (h.line, h.kind))

    fn_facts = [
        PerfFnFacts(
            function=name,
            func_start=start,
            loop_call_targets=tuple(sorted(targets.items())),
            bare_sink_kind=sink[0],
        )
        for start, (name, targets, sink) in fn_acc.items()
        if targets or sink[0] is not None
    ]
    fn_facts.sort(key=lambda f: f.func_start)
    return deduped, io_names, fn_facts


# ----------------------------------------------------------------------
# Class-level analysis (LCOM4 / god-class)
# ----------------------------------------------------------------------

_PROP_FIELD_NAMES = ("property", "attribute", "field", "name")
# ``expression`` is C#'s receiver field on ``member_access_expression`` (its
# ``this`` token is unnamed, so the positional fallback would pick the member).
_OBJECT_FIELD_NAMES = ("object", "value", "argument", "operand", "expression")
_IDENTIFIER_SUFFIX = "identifier"


def _class_name(node: Node) -> str:
    """Best-effort class/impl name.

    Tries the ``name`` field (most class grammars), then ``type`` (Rust's
    ``impl T`` exposes the implemented type there), then the generic
    identifier scan.
    """
    for field_name in ("name", "type"):
        child = node.child_by_field_name(field_name)
        if child is not None and child.text is not None:
            return child.text.decode("utf-8", errors="replace")
    return _find_name(node)


def _collect_class_nodes(root: Node, lmap: LanguageNodeMap) -> list[Node]:
    """All class-like grouping nodes in the file (pre-order).

    Descends through the whole tree so nested classes are found too;
    each becomes its own ``ClassComplexity``.
    """
    out: list[Node] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        # ``is_named`` filters out keyword tokens that share a type name
        # with an expression node (e.g. the ``class`` keyword vs a
        # ``class`` expression in tree-sitter-typescript).
        if node.type in lmap.class_kinds and node.is_named:
            out.append(node)
        for child in node.children:
            stack.append(child)
    return out


def _collect_class_methods(class_node: Node, lmap: LanguageNodeMap) -> list[Node]:
    """Direct method nodes of *class_node*.

    Stops at nested classes (their methods belong to the inner class) and
    does not descend into a method body (nested local defs roll up into
    the method, mirroring ``_collect_function_nodes``).
    """
    methods: list[Node] = []
    stack: list[Node] = list(class_node.children)
    while stack:
        node = stack.pop()
        if node.type in lmap.class_kinds:
            continue  # nested class — its methods are not ours
        if node.type in lmap.function_kinds:
            methods.append(node)
            continue  # don't descend into the method body
        for child in node.children:
            stack.append(child)
    return methods


def _self_member_name(node: Node, lmap: LanguageNodeMap) -> str | None:
    """Extract ``member`` from a ``self.member`` / ``this.member`` access.

    Returns the member name when the receiver token is one of the
    language's ``self_identifiers``; otherwise ``None`` (so ``other.x`` and
    ``a.b.c``'s outer hops are ignored — only direct instance access
    counts toward cohesion).
    """
    obj: Node | None = None
    for field_name in _OBJECT_FIELD_NAMES:
        obj = node.child_by_field_name(field_name)
        if obj is not None:
            break
    if obj is None:
        obj = next((c for c in node.children if c.is_named), None)

    prop: Node | None = None
    for field_name in _PROP_FIELD_NAMES:
        prop = node.child_by_field_name(field_name)
        if prop is not None:
            break
    if prop is None:
        named = [c for c in node.children if c.is_named]
        prop = next(
            (c for c in reversed(named) if c.type.endswith(_IDENTIFIER_SUFFIX)),
            None,
        )

    if obj is None or prop is None or obj is prop:
        return None
    if obj.text is None or prop.text is None:
        return None
    if obj.text.decode("utf-8", errors="replace") not in lmap.self_identifiers:
        return None
    return prop.text.decode("utf-8", errors="replace")


def _collect_self_members(method_node: Node, lmap: LanguageNodeMap) -> set[str]:
    """Set of instance-member names referenced by *method_node*.

    Walks the method body (descending through nested functions/lambdas,
    which close over the same instance) but stops at nested class
    definitions. Both field reads and method calls reduce to a member
    name here — both are evidence two methods touch the same thing.
    """
    members: set[str] = set()
    if not lmap.self_identifiers or not lmap.member_access_kinds:
        return members
    stack: list[Node] = list(method_node.children)
    while stack:
        node = stack.pop()
        if node.type in lmap.class_kinds:
            continue  # nested class has its own self
        if node.type in lmap.member_access_kinds:
            name = _self_member_name(node, lmap)
            if name:
                members.add(name)
        for child in node.children:
            stack.append(child)
    return members


def _compute_lcom4(
    method_nodes: list[Node],
    method_fcs: list[FunctionComplexity],
    lmap: LanguageNodeMap,
) -> tuple[int, int]:
    """Return ``(lcom4, field_count)`` for a class.

    LCOM4 = number of connected components over the methods, where two
    methods are connected if they share an instance member or one calls
    the other (a call shows up as a reference to the callee's name).

    **Safety valve:** if no instance-member references are detected at all
    (a pure-static class, or — importantly — a language whose
    member-access node type we have not mapped), return ``1`` rather than
    ``len(methods)``. This prevents ``low_cohesion`` from false-firing on
    an unverified language: a missing mapping yields "no signal", never a
    spurious high-LCOM hit.
    """
    n = len(method_nodes)
    if n == 0:
        return 1, 0

    members_per_method: list[set[str]] = [
        _collect_self_members(node, lmap) for node in method_nodes
    ]
    total_refs = sum(len(m) for m in members_per_method)
    method_names = {fc.name for fc in method_fcs}
    all_members: set[str] = set().union(*members_per_method) if members_per_method else set()
    field_count = len(all_members - method_names)

    if total_refs == 0:
        return 1, field_count

    # Union-find over method indices.
    parent = list(range(n))

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb

    name_to_idx = {fc.name: i for i, fc in enumerate(method_fcs)}
    # Bucket method indices by each member they reference; the method that
    # *defines* that name (a callee) joins the bucket too, so call edges and
    # shared-field edges are both captured in one pass.
    buckets: dict[str, list[int]] = {}
    for i, members in enumerate(members_per_method):
        for m in members:
            buckets.setdefault(m, []).append(i)
    for member, idxs in buckets.items():
        group = list(idxs)
        callee = name_to_idx.get(member)
        if callee is not None:
            group.append(callee)
        first = group[0]
        for other in group[1:]:
            _union(first, other)

    components = {_find(i) for i in range(n)}
    return len(components), field_count


def _collect_classes(
    root: Node,
    lmap: LanguageNodeMap,
    source: bytes,
    fc_by_node_id: dict[int, FunctionComplexity],
) -> list[ClassComplexity]:
    """Build ``ClassComplexity`` for every class-like node in the file."""
    if not lmap.class_kinds:
        return []
    classes: list[ClassComplexity] = []
    for class_node in _collect_class_nodes(root, lmap):
        method_nodes = _collect_class_methods(class_node, lmap)
        method_fcs = [fc_by_node_id[m.id] for m in method_nodes if m.id in fc_by_node_id]
        # Keep nodes and FCs aligned (a method missing from the function
        # pass — unusual — drops out of both).
        aligned_nodes = [m for m in method_nodes if m.id in fc_by_node_id]
        lcom4, field_count = _compute_lcom4(aligned_nodes, method_fcs, lmap)
        classes.append(
            ClassComplexity(
                name=_class_name(class_node),
                start_line=class_node.start_point[0] + 1,
                end_line=class_node.end_point[0] + 1,
                method_count=len(method_fcs),
                total_nloc=_count_nloc(class_node, source),
                methods=method_fcs,
                lcom4=lcom4,
                max_method_ccn=max((fc.ccn for fc in method_fcs), default=0),
                field_count=field_count,
            )
        )
    return classes


def walk_file(
    abs_path: str,
    language: str,
    source: bytes,
) -> FileComplexity:
    """Walk one file's AST once → per-function and per-class metrics.

    Returns an empty ``FileComplexity`` when:
      - the language is unsupported (no entry in ``LANGUAGE_MAPS``)
      - the tree-sitter language package isn't installed
      - parsing fails

    Class-level metrics are populated only when the language's
    ``LanguageNodeMap`` opts in via ``class_kinds`` (see ``languages.py``).
    """
    lmap = get_language_map(language)
    if lmap is None:
        return FileComplexity(functions=[], classes=[], file_nloc=_count_file_nloc(source))

    try:
        from tree_sitter import Parser

        # Reuse the ingestion parser's language registry. Importing
        # lazily avoids pulling tree-sitter at module load time when
        # health is run from a context where it isn't installed.
        from repowise.core.ingestion.parser import _get_language
    except Exception as exc:
        log.debug("complexity_walker_import_failed", error=str(exc))
        return FileComplexity(functions=[], classes=[], file_nloc=_count_file_nloc(source))

    grammar = _get_language(language)
    if grammar is None:
        return FileComplexity(functions=[], classes=[], file_nloc=_count_file_nloc(source))

    try:
        parser = Parser(grammar)
        tree = parser.parse(source)
    except Exception as exc:
        log.debug("complexity_walker_parse_failed", path=abs_path, error=str(exc))
        return FileComplexity(functions=[], classes=[], file_nloc=_count_file_nloc(source))

    functions: list[FunctionComplexity] = []
    fc_by_node_id: dict[int, FunctionComplexity] = {}
    for fn_node in _collect_function_nodes(tree.root_node, lmap):
        body = fn_node.child_by_field_name("body") or fn_node
        ccn, max_nest, cognitive, bumps, conditions = _walk_function_body(body, lmap)
        fc = FunctionComplexity(
            name=_find_function_entry_name(fn_node, lmap),
            start_line=fn_node.start_point[0] + 1,
            end_line=fn_node.end_point[0] + 1,
            ccn=ccn,
            max_nesting=max_nest,
            cognitive=cognitive,
            nloc=_count_nloc(body, source),
            bumps=bumps,
            param_count=_count_parameters(fn_node),
            complex_conditions=conditions,
            assertion_blocks=_collect_assertion_blocks(body, lmap),
        )
        functions.append(fc)
        fc_by_node_id[fn_node.id] = fc

    classes = _collect_classes(tree.root_node, lmap, source, fc_by_node_id)
    perf_hits, io_boundary_names, perf_fn_facts = _collect_perf_hits(tree.root_node, language, lmap)
    return FileComplexity(
        functions=functions,
        classes=classes,
        file_nloc=_count_file_nloc_tree(tree.root_node, source),
        error_handling_hits=_collect_error_handling(tree.root_node, language, lmap),
        perf_hits=perf_hits,
        io_boundary_names=io_boundary_names,
        perf_fn_facts=perf_fn_facts,
    )


def walk_file_complexity(
    abs_path: str,
    language: str,
    source: bytes,
) -> list[FunctionComplexity]:
    """Backward-compatible wrapper: returns only per-function metrics.

    Prefer ``walk_file`` when class-level metrics are also needed.
    """
    return walk_file(abs_path, language, source).functions


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
