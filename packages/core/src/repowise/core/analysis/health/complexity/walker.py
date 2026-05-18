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
class FunctionComplexity:
    """Per-function metrics produced by the walker."""

    name: str
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    ccn: int
    max_nesting: int
    cognitive: int
    nloc: int  # non-blank lines inside the body


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


def _walk_function_body(
    body_node: Node,
    lmap: LanguageNodeMap,
) -> tuple[int, int, int]:
    """Recursive AST walk. Returns (ccn, max_nesting, cognitive).

    Starts CCN at 1 (the entry path). Nested function bodies are
    skipped — they will (or already did) produce their own
    ``FunctionComplexity``.
    """

    ccn = 1
    max_nesting = 0
    cognitive = 0

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
        _recurse(child, 0)

    return ccn, max_nesting, cognitive


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
        ccn, max_nest, cognitive = _walk_function_body(body, lmap)
        results.append(
            FunctionComplexity(
                name=_find_name(fn_node),
                start_line=fn_node.start_point[0] + 1,
                end_line=fn_node.end_point[0] + 1,
                ccn=ccn,
                max_nesting=max_nest,
                cognitive=cognitive,
                nloc=_count_nloc(body, source),
            )
        )
    return results
