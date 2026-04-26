"""Shared AST helpers used by extractor modules."""

from __future__ import annotations

from tree_sitter import Node


def node_text(node: Node | None, src: str) -> str:
    """Return the text of a tree-sitter *node*, or ``""`` if *node* is None."""
    if node is None:
        return ""
    if node.text is not None:
        return node.text.decode("utf-8", errors="replace")
    return src[node.start_byte : node.end_byte]


def extract_go_receiver_type(receiver_text: str) -> str | None:
    """Extract 'Calculator' from '(c *Calculator)' or '(c Calculator)'."""
    text = receiver_text.strip("() ")
    parts = text.split()
    for part in reversed(parts):
        clean = part.lstrip("*")
        if clean and clean[0].isupper():
            return clean
    return None


def refine_go_type_kind(type_spec_node: Node, src: str) -> str:
    """Refine the generic 'struct' kind for Go type_spec nodes."""
    type_node = type_spec_node.child_by_field_name("type")
    if type_node is None:
        return "struct"
    type_text = node_text(type_node, src).strip()
    if type_text.startswith("struct"):
        return "struct"
    if type_text.startswith("interface"):
        return "interface"
    return "type_alias"


def refine_kotlin_class_kind(class_node: Node) -> str:
    """Refine 'class' kind for Kotlin class_declaration nodes.

    In tree-sitter-kotlin v1.x, interfaces and enum classes all use
    ``class_declaration`` — the keyword child (``class``, ``interface``,
    ``enum``) distinguishes them.
    """
    for child in class_node.children:
        if child.type == "interface":
            return "interface"
        if child.type == "enum":
            return "enum"
    return "class"


def clean_string_literal(text: str) -> str:
    """Strip quote characters from a Python string literal."""
    text = text.strip()
    for triple in ('"""', "'''"):
        if text.startswith(triple) and text.endswith(triple) and len(text) >= 6:
            return text[3:-3].strip()
    for q in ('"', "'"):
        if text.startswith(q) and text.endswith(q) and len(text) >= 2:
            return text[1:-1].strip()
    return text


def find_preceding_jsdoc(node: Node, src: str) -> str | None:
    """Return the JSDoc comment immediately before *node*, if any."""
    parent = node.parent
    if parent is None:
        return None
    siblings = list(parent.children)
    idx = next((i for i, s in enumerate(siblings) if s.id == node.id), -1)
    if idx <= 0:
        return None
    prev = siblings[idx - 1]
    if prev.type == "comment":
        text = node_text(prev, src).strip()
        if text.startswith("/**"):
            return clean_jsdoc(text)
    return None


def find_preceding_block_comment(node: Node, src: str, prefix: str) -> str | None:
    """Return the block comment immediately before *node* that starts with *prefix*."""
    parent = node.parent
    if parent is None:
        return None
    siblings = list(parent.children)
    idx = next((i for i, s in enumerate(siblings) if s.id == node.id), -1)
    if idx <= 0:
        return None
    prev = siblings[idx - 1]
    if prev.type in ("block_comment", "comment"):
        text = node_text(prev, src).strip()
        if text.startswith(prefix):
            return clean_jsdoc(text)
    return None


def clean_jsdoc(text: str) -> str:
    """Strip JSDoc / block-comment delimiters and leading asterisks."""
    lines = text.splitlines()
    cleaned: list[str] = []
    for line in lines:
        line = line.strip().lstrip("/*").lstrip()
        if line:
            cleaned.append(line)
    return "\n".join(cleaned).strip()
