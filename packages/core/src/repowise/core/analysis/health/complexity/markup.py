"""Lines that hold only markup or literal data: no logic.

In a JSX file, tags, attributes and text are markup. An attribute value of plain
data (``style={{ color: "red" }}``, ``title={label}``) is markup too, and so is a
component's ``const`` data literal used only there. In every language, the
continuation lines of a multi-line string (a text block, heredoc, raw or
template string) are data. A call, an operator, a function or control flow
keeps a line code.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from typing import TYPE_CHECKING

from .nloc import _source_lines

if TYPE_CHECKING:
    from tree_sitter import Node

#: Grammars that parse JSX; a ``.ts`` file reads with ``typescript`` and has none.
_JSX_GRAMMARS = frozenset({"tsx", "javascript"})
#: The ``{...}`` slot inside markup; what it holds is code again unless it is data.
_EXPRESSION_SLOT = "jsx_expression"
#: Nodes a plain data value is built from: literals, references, objects, arrays.
_DATA_KINDS = frozenset(
    {
        "string",
        "string_fragment",
        "escape_sequence",
        "template_string",
        "template_substitution",
        "number",
        "true",
        "false",
        "null",
        "undefined",
        "identifier",
        "property_identifier",
        "shorthand_property_identifier",
        "member_expression",
        "object",
        "pair",
        "array",
        "spread_element",
        "parenthesized_expression",
    }
)
#: ``x as const`` / ``x satisfies T``: only the value is data, the type is skipped.
_TYPED_VALUE = frozenset({"as_expression", "satisfies_expression"})


def _is_data(node: Node) -> bool:
    """True when *node* holds no call, operator, function or control flow.

    Nested JSX counts as data here: its own ``{...}`` slots are judged on their own.
    """
    stack = list(node.named_children)
    while stack:
        child = stack.pop()
        kind = child.type
        if kind.startswith("jsx_") or "comment" in kind:
            continue
        if kind in _TYPED_VALUE:
            stack.extend(child.named_children[:1])
        elif kind in _DATA_KINDS or (
            kind == "unary_expression" and child.children[0].type == "-"
        ):
            stack.extend(child.named_children)
        else:
            return False
    return True


def _is_data_slot(node: Node) -> bool:
    """An attribute's ``{...}`` value that is plain data."""
    parent = node.parent
    return parent is not None and parent.type == "jsx_attribute" and _is_data(node)


def _in_data_slot(node: Node, scope: Node) -> bool:
    """*node* sits in a data attribute value below *scope*."""
    slot = node.parent
    while slot is not None and slot != scope and slot.type != _EXPRESSION_SLOT:
        slot = slot.parent
    return slot is not None and slot != scope and _is_data_slot(slot)


def _used_only_in_data_slots(name: Node, scope: Node) -> bool:
    """Every other mention of *name* in *scope* sits in a data attribute value."""
    text = name.text
    uses = 0
    stack = [scope]
    while stack:
        node = stack.pop()
        if node.type not in ("identifier", "shorthand_property_identifier"):
            stack.extend(node.children)
        elif node != name and node.text == text:
            if not _in_data_slot(node, scope):
                return False
            uses += 1
    return uses > 0


def _data_literal_name(node: Node) -> Node | None:
    """The name of ``const X = {...}`` / ``[...]`` of plain data in a block, else ``None``."""
    declarators = node.named_children
    if node.children[0].type != "const" or len(declarators) != 1:
        return None
    name = declarators[0].child_by_field_name("name")
    value = declarators[0].child_by_field_name("value")
    if name is None or value is None or name.type != "identifier":
        return None
    literal = value.named_children[0] if value.type in _TYPED_VALUE else value
    return name if literal.type in ("object", "array") and _is_data(literal) else None


def _data_declarations(root: Node) -> set[tuple[int, int]]:
    """Byte spans of ``const X = {...}`` in a function body, X used only as attribute data."""
    spans: set[tuple[int, int]] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        scope = node.parent
        if node.type != "lexical_declaration" or scope is None or scope.type != "statement_block":
            continue
        name = _data_literal_name(node)
        if name is not None and _used_only_in_data_slots(name, scope):
            spans.add((node.start_byte, node.end_byte))
    return spans


def _opens_markup(node: Node, in_markup: bool, data_decls: set[tuple[int, int]]) -> bool:
    """Whether *node*'s tokens are markup, given its parent's state."""
    kind = node.type
    if kind == _EXPRESSION_SLOT:
        return in_markup and _is_data_slot(node)
    if kind.startswith("jsx_"):
        return True
    if kind == "lexical_declaration" and (node.start_byte, node.end_byte) in data_decls:
        return True
    return in_markup


def _token_rows(root: Node) -> tuple[set[int], set[int]]:
    """Rows holding a markup token and rows holding a code token, comments skipped."""
    markup: set[int] = set()
    code: set[int] = set()
    data_decls = _data_declarations(root)
    stack: list[tuple[Node, bool]] = [(root, False)]
    while stack:
        node, in_markup = stack.pop()
        if "comment" in node.type:
            continue
        in_markup = _opens_markup(node, in_markup, data_decls)
        if node.children:
            stack.extend((child, in_markup) for child in node.children)
        elif node.start_byte < node.end_byte:
            rows = range(node.start_point[0], node.end_point[0] + 1)
            (markup if in_markup else code).update(rows)
    return markup, code


#: Node kinds of a literal across grammars: plain, raw, verbatim, template and
#: interpolated strings, text blocks and heredocs; then initializer lists, arrays,
#: maps, sets, tuples and struct literals. ``file_scan`` collects the multi-line
#: ones outside docstrings.
LITERAL_KINDS = frozenset(
    {
        "string",
        "raw_string",
        "string_literal",
        "template_string",
        "concatenated_string",
        "raw_string_literal",
        "verbatim_string_literal",
        "multiline_string_literal",
        "multi_line_string_literal",
        "interpolated_string_expression",
        "interpolated_string",
        "encapsed_string",
        "heredoc",
        "heredoc_body",
        "nowdoc",
        "sigil",
        "initializer_list",
        "array_initializer",
        "initializer_expression",
        "collection_expression",
        "literal_value",
        "array_expression",
        "tuple_expression",
        "struct_expression",
        "list",
        "dictionary",
        "set",
        "tuple",
        "array",
        "object",
        "hash",
        "array_literal",
        "dictionary_literal",
        "array_creation_expression",
        "list_literal",
        "set_or_map_literal",
    }
)
#: Name fragments of the nodes literal data is built from: text, names, numbers.
_DATA_PARTS = (
    "string",
    "str_text",
    "heredoc",
    "nowdoc",
    "content",
    "fragment",
    "escape",
    "identifier",
    "interpolat",
    "literal",
    "number",
    "integer",
    "float",
    "expansion",
    "symbol",
    "character",
)
#: A function literal is code whatever its name says.
_CODE_PARTS = ("func", "lambda", "closure")
#: Data nodes whose names carry none of the fragments: names, constants, member access.
_DATA_NAMES = frozenset(
    {
        "name",
        "variable_name",
        "true",
        "false",
        "null",
        "nil",
        "none",
        "undefined",
        "member_expression",
        "field_expression",
        "attribute",
        "navigation_expression",
        "selector_expression",
        "field_access",
        "member_access_expression",
        "parenthesized_expression",
        "boolean",
        # The pieces of a table: elements, keys, designators and element types.
        "initializer_list",
        "initializer_pair",
        "field_designator",
        "subscript_designator",
        "array_initializer",
        "initializer_expression",
        "implicit_array_creation_expression",
        "collection_expression",
        "collection_element",
        "expression_element",
        "literal_value",
        "literal_element",
        "keyed_element",
        "array_expression",
        "tuple_expression",
        "struct_expression",
        "field_initializer_list",
        "field_initializer",
        "list",
        "dictionary",
        "set",
        "tuple",
        "pair",
        "array",
        "object",
        "hash",
        "array_creation_expression",
        "array_element_initializer",
        "generic_name",
        "type_argument_list",
        "type_arguments",
        "template_argument_list",
        "type_descriptor",
        "dimensions",
        "array_rank_specifier",
        "minus_operator",  # Dart's `-` in `-1`, under a `prefix_operator`
    }
)
#: A sign on a number (``-1``) keeps it data; any other operator is code.
_SIGNS = frozenset({b"-", b"+"})


def _rows(node: Node) -> range:
    return range(node.start_point[0], node.end_point[0] + 1)


def _is_data_node(node: Node) -> bool:
    """A literal's text, a name, a number, a table or member access: no call, operator or function."""
    kind = node.type
    if kind in _DATA_NAMES or kind.endswith("_type"):
        return True
    if "unary" in kind or "prefix" in kind:
        return node.children[0].text in _SIGNS
    if any(part in kind for part in _CODE_PARTS):
        return False
    return any(part in kind for part in _DATA_PARTS)


def _literal_token_rows(literal: Node, data: set[int], code: set[int]) -> None:
    """Sort *literal*'s token rows into *data* and *code*.

    An embedded piece that is not data (``${call()}``, ``#{a + b}``) makes its rows code.
    """
    stack = [literal]
    while stack:
        node = stack.pop()
        if "comment" in node.type:
            continue
        if node.is_named and not _is_data_node(node):
            code.update(_rows(node))
        elif node.children:
            stack.extend(node.children)
        elif node.start_byte < node.end_byte:
            data.update(_rows(node))


def _edge_rows_with_code(literal: Node, source: bytes) -> list[int]:
    """The literal's first and last rows when a token outside it shares them.

    A trailing ``,`` or ``;`` closes the literal and leaves its last row data.
    """
    start, end = literal.start_byte, literal.end_byte
    line_end = source.find(b"\n", end)
    rows = []
    if source[source.rfind(b"\n", 0, start) + 1 : start].strip():
        rows.append(literal.start_point[0])
    if source[end : line_end if line_end >= 0 else len(source)].strip(b" \t\r,;"):
        rows.append(literal.end_point[0])
    return rows


def _string_rows(literals: list[Node], source: bytes) -> set[int]:
    """Rows inside the multi-line *literals* that hold nothing but literal data.

    The row that opens a literal stays code when code precedes it there
    (``q = \"\"\"``), so a long text block costs its statement one line.
    """
    data: set[int] = set()
    code: set[int] = set()
    covered_to = -1
    for literal in sorted(literals, key=lambda node: (node.start_byte, -node.end_byte)):
        if literal.start_byte < covered_to:
            continue
        covered_to = literal.end_byte
        _literal_token_rows(literal, data, code)
        code.update(_edge_rows_with_code(literal, source))
    return data - code


def markup_rows(root: Node, grammar_tag: str, source: bytes, literals: list[Node]) -> list[int]:
    """Sorted 0-indexed non-blank rows whose every token is markup or literal data.

    *literals* are the file's multi-line literals (strings, tables) outside docstrings.
    """
    rows = _string_rows(literals, source)
    if grammar_tag in _JSX_GRAMMARS and (b"/>" in source or b"</" in source):
        markup, code = _token_rows(root)
        rows |= markup - code
    lines = _source_lines(source)
    return sorted(row for row in rows if row < len(lines) and lines[row].strip())


def rows_within(rows: list[int], node: Node) -> int:
    """How many of the sorted *rows* fall inside *node*'s line span."""
    return bisect_right(rows, node.end_point[0]) - bisect_left(rows, node.start_point[0])


def rows_in_ranges(rows: list[int], ranges: list[tuple[int, int]]) -> int:
    """How many of the sorted *rows* fall inside the inclusive, disjoint *ranges*."""
    return sum(bisect_right(rows, last) - bisect_left(rows, first) for first, last in ranges)
