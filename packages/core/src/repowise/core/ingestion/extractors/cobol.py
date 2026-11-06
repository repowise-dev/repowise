"""COBOL naming and procedure-range helpers for the declarative parser."""

from __future__ import annotations

import re

from tree_sitter import Node

from ..cobol.fragments import FRAGMENT_PROGRAM

_SECTION_SUFFIX = re.compile(r"\s+SECTION\s*\.\s*$", re.IGNORECASE)
_PARAGRAPH_SUFFIX = re.compile(r"\s*\.\s*$")
_PROGRAM_ID_IN_ERROR = re.compile(r"\bPROGRAM-ID\s*\.\s*([A-Z0-9][A-Z0-9-]*)", re.IGNORECASE)
_PROCEDURE_HEADERS = frozenset({"section_header", "paragraph_header"})
_UNNAMED = frozenset({"FILLER", FRAGMENT_PROGRAM})


def normalize_cobol_symbol_name(raw_name: str, node_type: str) -> str:
    """Return COBOL's case-insensitive identifier in a stable graph form."""
    name = raw_name.strip()
    if node_type in {"program_definition", "ERROR"}:
        recovered = _PROGRAM_ID_IN_ERROR.search(name)
        if recovered:
            name = recovered.group(1)
    elif node_type == "section_header":
        name = _SECTION_SUFFIX.sub("", name)
    elif node_type == "paragraph_header":
        name = _PARAGRAPH_SUFFIX.sub("", name)
    name = name.upper()
    # FILLER is an unnamed data item, and the wrapper program is synthetic; "" drops both.
    return "" if name in _UNNAMED else name


def _level(node: Node) -> str:
    level = node.named_children[0] if node.named_children else None
    if level is None or level.type != "level_number" or level.text is None:
        return ""
    return level.text.decode("utf-8", errors="replace").strip()


def cobol_condition_parent(def_node: Node) -> str | None:
    """The data item a level-88 condition name belongs to, else None.

    The grammar lists data entries as flat siblings, and an 88 entry follows
    the item it tests or another 88 of that item. A FILLER item has no name
    to hang the condition on, so its 88s stay top level.
    """
    if def_node.type != "data_description" or _level(def_node) != "88":
        return None
    item = def_node.prev_named_sibling
    while item is not None and item.type == "data_description" and _level(item) == "88":
        item = item.prev_named_sibling
    if item is None or item.type != "data_description":
        return None
    name = next((c for c in item.named_children if c.type == "entry_name"), None)
    if name is None or name.text is None:
        return None
    raw = name.text.decode("utf-8", errors="replace")
    return normalize_cobol_symbol_name(raw, item.type) or None


def normalize_cobol_call_target(raw_name: str, _node_type: str) -> str:
    """Canonicalize static ``CALL`` literals and ``PERFORM`` labels."""
    name = raw_name.strip()
    if len(name) >= 2 and name[0] == name[-1] and name[0] in {"'", '"'}:
        name = name[1:-1]
    return name.upper()


def cobol_symbol_end_line(node: Node, default_end_line: int) -> int:
    """Extend a section/paragraph header across the procedure body it owns.

    The COBOL grammar represents procedure headers and statements as siblings,
    unlike languages whose function node wraps its body. Extending the range
    lets the generic caller-attribution pass select the innermost paragraph.
    """
    if node.type == "ERROR":
        ancestor = node.parent
        while ancestor is not None:
            if ancestor.type == "program_definition":
                return max(default_end_line, ancestor.end_point[0] + 1)
            ancestor = ancestor.parent
        return default_end_line

    if node.type not in _PROCEDURE_HEADERS or node.parent is None:
        return default_end_line

    siblings = node.parent.named_children
    current = next((index for index, child in enumerate(siblings) if child.id == node.id), None)
    if current is None:
        return default_end_line

    stop_types = {"section_header"} if node.type == "section_header" else _PROCEDURE_HEADERS
    for index in range(current + 1, len(siblings)):
        if siblings[index].type in stop_types:
            previous = siblings[index - 1]
            return max(default_end_line, previous.end_point[0] + 1)

    return max(default_end_line, node.parent.end_point[0] + 1)
