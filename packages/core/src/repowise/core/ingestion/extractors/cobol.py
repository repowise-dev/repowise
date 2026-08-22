"""COBOL naming and procedure-range helpers for the declarative parser."""

from __future__ import annotations

import re

from tree_sitter import Node

_SECTION_SUFFIX = re.compile(r"\s+SECTION\s*\.\s*$", re.IGNORECASE)
_PARAGRAPH_SUFFIX = re.compile(r"\s*\.\s*$")
_PROGRAM_ID_IN_ERROR = re.compile(r"\bPROGRAM-ID\s*\.\s*([A-Z0-9][A-Z0-9-]*)", re.IGNORECASE)
_PROCEDURE_HEADERS = frozenset({"section_header", "paragraph_header"})


def normalize_cobol_symbol_name(raw_name: str, node_type: str) -> str:
    """Return COBOL's case-insensitive identifier in a stable graph form."""
    name = raw_name.strip()
    if node_type == "program_definition":
        recovered = _PROGRAM_ID_IN_ERROR.search(name)
        if recovered:
            name = recovered.group(1)
    elif node_type == "section_header":
        name = _SECTION_SUFFIX.sub("", name)
    elif node_type == "paragraph_header":
        name = _PARAGRAPH_SUFFIX.sub("", name)
    return name.upper()


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
