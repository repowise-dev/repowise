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

import re
import sys
from bisect import bisect_left
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


_NEWLINE = re.compile(rb"\n")

# One leaf token in ``Token`` field order: (kind, start_line, end_line,
# start_byte, end_byte, name).
TokenRow = tuple[str, int, int, int, int, str]


def token_rows(
    root: Node, source: bytes, skip_kinds: frozenset[str] = frozenset()
) -> list[TokenRow]:
    """Walk *root* and return one ``TokenRow`` per kept leaf, in source order.

    Dropped: comments, parse-error leaves, whitespace-only leaves, and whole
    subtrees whose kind is in *skip_kinds* (import statements) or that are
    re-exports. A clone scan walks every node of every file, so this is a
    cursor walk that reads each node's attributes once and emits plain
    tuples; ``tokenize_tree`` wraps them in ``Token`` for callers that want
    objects.
    """
    out: list[TokenRow] = []
    append = out.append
    intern = sys.intern
    # A node's row is the number of newlines before its byte offset, which
    # a C bisect answers faster than building tree-sitter Point objects.
    newlines = [m.start() for m in _NEWLINE.finditer(source)]
    cursor = root.walk()
    while True:
        node = cursor.node
        ntype = node.type
        child_count = node.child_count
        if not (
            ntype in _COMMENT_KINDS
            # Tree-sitter exposes ERROR / MISSING for parse errors - drop them
            # so a single broken file can't pollute the hash stream.
            or (child_count == 0 and node.has_error)
            or ntype in skip_kinds
            # JS/TS ``export { a } from "./b"``: an import in all but name.
            or (ntype == "export_statement" and node.child_by_field_name("source") is not None)
        ):
            if child_count == 0:
                start_byte = node.start_byte
                end_byte = node.end_byte
                name = ""
                if ntype in _IDENTIFIER_KINDS:
                    kind = "ID"
                    name = intern(source[start_byte:end_byte].decode("utf-8", errors="replace"))
                elif ntype in _LITERAL_KINDS:
                    kind = "LIT"
                else:
                    # Raw token text for operators / keywords / punctuation.
                    # Interned: a repo-sized population of equal spellings
                    # becomes one object per distinct spelling.
                    text = source[start_byte:end_byte]
                    kind = intern(text.decode("utf-8", errors="replace")) if text.strip() else ""
                if kind:
                    append(
                        (
                            kind,
                            bisect_left(newlines, start_byte) + 1,
                            bisect_left(newlines, end_byte) + 1,
                            start_byte,
                            end_byte,
                            name,
                        )
                    )
            elif cursor.goto_first_child():
                continue
        while not cursor.goto_next_sibling():
            if not cursor.goto_parent():
                return out


def tokenize_tree(
    root: Node, source: bytes, skip_kinds: frozenset[str] = frozenset()
) -> list[Token]:
    """Walk *root* and return the flattened token list (see ``token_rows``)."""
    return [Token(*row) for row in token_rows(root, source, skip_kinds)]


def tokenize_file(language: str, source: bytes, path: str | None = None) -> list[Token]:
    """Parse *source* and return its normalized token stream (see ``tokenize_file_rows``)."""
    return [Token(*row) for row in tokenize_file_rows(language, source, path)]


def tokenize_file_rows(language: str, source: bytes, path: str | None = None) -> list[TokenRow]:
    """Parse *source* and return its normalized token rows.

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
    return token_rows(tree.root_node, source, import_node_kinds(language))
