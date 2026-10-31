"""Lines of a JSX file that hold only markup: tags, attributes and text, no logic.

An attribute value of plain data (``style={{ color: "red" }}``, ``title={label}``)
is markup too, and so is a component's ``const`` data literal used only there.
A call, an operator, a function or control flow keeps a line code.
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


def markup_rows(root: Node, grammar_tag: str, source: bytes) -> list[int]:
    """Sorted 0-indexed non-blank rows whose every token is markup; ``[]`` without JSX."""
    if grammar_tag not in _JSX_GRAMMARS or (b"/>" not in source and b"</" not in source):
        return []
    markup, code = _token_rows(root)
    lines = _source_lines(source)
    return sorted(row for row in markup - code if row < len(lines) and lines[row].strip())


def rows_within(rows: list[int], node: Node) -> int:
    """How many of the sorted *rows* fall inside *node*'s line span."""
    return bisect_right(rows, node.end_point[0]) - bisect_left(rows, node.start_point[0])
