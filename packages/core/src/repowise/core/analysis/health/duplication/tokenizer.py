"""Tree-sitter token extraction for duplication detection.

The tokenizer walks a parsed tree and yields a stream of ``Token``s, one
per leaf node, excluding:

- whitespace-only nodes
- comment nodes (``comment``, ``line_comment``, ``block_comment``,
  ``doc_comment``)
- syntax-error and missing nodes

Two normalization knobs control how aggressive matching is:

- **identifiers** are normalized to a single placeholder ``ID`` so that
  ``foo`` and ``bar`` collide. This is the v1 default — it catches
  semantically-equivalent clones with renamed variables.
- **literals** (numbers, strings) are normalized to ``LIT`` for the same
  reason; string content is rarely the meaningful signal in a clone.

Operators and keywords pass through as their literal token text so we
preserve structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node


_COMMENT_KINDS = frozenset(
    {
        "comment",
        "line_comment",
        "block_comment",
        "doc_comment",
        "documentation_comment",
        "shebang",
    }
)

_IDENTIFIER_KINDS = frozenset(
    {
        "identifier",
        "property_identifier",
        "field_identifier",
        "shorthand_property_identifier",
        "type_identifier",
        "scoped_identifier",
    }
)

_LITERAL_KINDS = frozenset(
    {
        "string",
        "string_literal",
        "string_fragment",
        "raw_string_literal",
        "interpreted_string_literal",
        "integer",
        "float",
        "number",
        "integer_literal",
        "float_literal",
        "decimal_integer_literal",
        "decimal_floating_point_literal",
        "true",
        "false",
        "null",
        "nil",
        "None",
        "boolean",
    }
)


@dataclass(frozen=True)
class Token:
    """One AST token with the source location of its origin node."""

    kind: str  # normalized token category or literal text
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    start_byte: int
    end_byte: int


def _is_skippable(node: Node) -> bool:
    if node.type in _COMMENT_KINDS:
        return True
    # Tree-sitter exposes ERROR / MISSING for parse errors - drop them
    # so a single broken file can't pollute the hash stream.
    return bool(getattr(node, "has_error", False) and node.child_count == 0)


def tokenize_tree(root: Node, source: bytes) -> list[Token]:
    """Walk *root* and return the flattened token list.

    Iterative DFS — uses a stack rather than recursion so very deep
    files don't blow the recursion limit.
    """
    out: list[Token] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if _is_skippable(node):
            continue
        if node.child_count == 0:
            tok = _tokenize_leaf(node, source)
            if tok is not None:
                out.append(tok)
            continue
        # Push in reverse so we visit in source order on the next pop.
        for child in reversed(node.children):
            stack.append(child)
    return out


def _tokenize_leaf(node: Node, source: bytes) -> Token | None:
    if node.type in _IDENTIFIER_KINDS:
        kind = "ID"
    elif node.type in _LITERAL_KINDS:
        kind = "LIT"
    else:
        # Use the raw token text for operators / keywords / punctuation.
        text = source[node.start_byte : node.end_byte]
        if not text.strip():
            return None
        try:
            kind = text.decode("utf-8", errors="replace")
        except Exception:
            return None
    return Token(
        kind=kind,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
    )


def tokenize_file(language: str, source: bytes) -> list[Token]:
    """Parse *source* and return its normalized token stream.

    Returns an empty list when the language is unsupported or parsing
    fails — callers treat that as "no clone candidates from this file".
    """
    try:
        from tree_sitter import Parser

        from repowise.core.ingestion.parser import _get_language
    except Exception:
        return []

    grammar = _get_language(language)
    if grammar is None:
        return []
    try:
        parser = Parser(grammar)
        tree = parser.parse(source)
    except Exception:
        return []
    return tokenize_tree(tree.root_node, source)
