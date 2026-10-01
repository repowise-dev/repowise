"""Tree-sitter token extraction for duplication detection.

The tokenizer walks a parsed tree and yields a stream of ``Token``s, one
per leaf node, excluding:

- whitespace-only nodes
- comment nodes (``comment``, ``line_comment``, ``block_comment``,
  ``doc_comment``)
- syntax-error and missing nodes
- import statements and re-exports (``export ... from``): every file opens
  with one, so they pair up without being duplicated logic

Two normalization knobs control how aggressive matching is:

- **identifiers** are normalized to a single placeholder ``ID`` so that
  ``foo`` and ``bar`` collide. This is the v1 default — it catches
  semantically-equivalent clones with renamed variables.
- **literals** (numbers, strings) are normalized to ``LIT`` for the same
  reason; string content is rarely the meaningful signal in a clone.

Operators and keywords pass through as their literal token text so we
preserve structure. Each identifier token also keeps its raw text in
``Token.name`` so a hash match can be checked against the real names.
"""

from __future__ import annotations

import sys
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
        # Pascal (tree-sitter-pascal): numbers and strings are ``literalNumber``
        # / ``literalString``; ``True`` / ``False`` / ``Nil`` are named keyword
        # tokens (``kTrue`` / ``kFalse`` / ``kNil``), which fall through to the
        # raw-text branch below like any other keyword -- fine, since their
        # text is already the shortest possible normalized form.
        "literalNumber",
        "literalString",
    }
)


# Config "import" kinds that also cover real code: generic calls or commands
# (Ruby ``require``, shell ``source``) and GDScript's superclass clause.
_NOT_IMPORT_KINDS = frozenset({"call", "command", "function_call", "extends_statement"})


@dataclass(frozen=True, slots=True)
class Token:
    """One AST token with the source location of its origin node."""

    kind: str  # normalized token category or literal text
    start_line: int  # 1-indexed
    end_line: int  # 1-indexed
    start_byte: int
    end_byte: int
    name: str = ""  # raw identifier text; empty for every other token


def import_node_kinds(language: str) -> frozenset[str]:
    """The language's import statement node kinds, minus ones that are real code."""
    from repowise.core.ingestion.language_configs import LANGUAGE_CONFIGS

    config = LANGUAGE_CONFIGS.get(language)
    if config is None:
        return frozenset()
    return frozenset(config.import_node_types) - _NOT_IMPORT_KINDS


def _is_skippable(node: Node) -> bool:
    if node.type in _COMMENT_KINDS:
        return True
    # Tree-sitter exposes ERROR / MISSING for parse errors - drop them
    # so a single broken file can't pollute the hash stream.
    return bool(getattr(node, "has_error", False) and node.child_count == 0)


def _is_reexport(node: Node) -> bool:
    # JS/TS ``export { a } from "./b"``: an import in all but name.
    return node.type == "export_statement" and node.child_by_field_name("source") is not None


def tokenize_tree(
    root: Node, source: bytes, skip_kinds: frozenset[str] = frozenset()
) -> list[Token]:
    """Walk *root* and return the flattened token list.

    Subtrees whose node kind is in *skip_kinds* (import statements) and
    re-exports are dropped whole. Iterative DFS — uses a stack rather than
    recursion so very deep files don't blow the recursion limit.
    """
    out: list[Token] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if _is_skippable(node) or node.type in skip_kinds or _is_reexport(node):
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
    name = ""
    if node.type in _IDENTIFIER_KINDS:
        kind = "ID"
        name = sys.intern(source[node.start_byte : node.end_byte].decode("utf-8", errors="replace"))
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
        # A clone scan holds one Token per leaf and later keeps every kind
        # through collision verification. Operators and keywords otherwise
        # create one equal Python string per occurrence; interning turns that
        # repo-sized string population into one object per distinct spelling.
        kind=sys.intern(kind),
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        start_byte=node.start_byte,
        end_byte=node.end_byte,
        name=name,
    )


def tokenize_file(language: str, source: bytes, path: str | None = None) -> list[Token]:
    """Parse *source* and return its normalized token stream.

    Returns an empty list when the language is unsupported or parsing
    fails — callers treat that as "no clone candidates from this file".
    ``path`` selects the grammar where the language tag does not settle it
    (a ``.tsx`` file is tagged ``typescript`` and needs the JSX grammar) and
    gates Pascal's project-file sanitizer. Omitting it costs both.
    """
    try:
        from tree_sitter import Parser

        from repowise.core.ingestion.parser import _get_language, grammar_tag_for
    except Exception:
        return []

    grammar = _get_language(grammar_tag_for(language, path or ""))
    if grammar is None:
        return []
    try:
        from repowise.core.ingestion.sfc_source import prepare_source

        parser = Parser(grammar)
        # Markup-blanked TS buffer for SFCs, identical offsets; Pascal's
        # project-file sanitizer for that language. No-op else.
        source = prepare_source(language, source, path=path)
        tree = parser.parse(source)
    except Exception:
        return []
    return tokenize_tree(tree.root_node, source, import_node_kinds(language))
