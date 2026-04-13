"""Module-level and symbol-level docstring extraction."""

from __future__ import annotations

from tree_sitter import Node

from .helpers import (
    clean_jsdoc,
    clean_string_literal,
    find_preceding_block_comment,
    find_preceding_jsdoc,
    node_text,
)


def extract_module_docstring(root: Node, src: str, lang: str) -> str | None:
    """Extract a module/file-level docstring or leading comment."""
    if lang == "python":
        for child in root.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type == "string":
                        return clean_string_literal(node_text(sub, src))
                break
            elif child.type not in (
                "comment",
                "newline",
                "import_statement",
                "import_from_statement",
                "future_import_statement",
            ):
                break
    elif lang in ("typescript", "javascript"):
        # Look for leading /** ... */ comment
        for child in root.children:
            if child.type == "comment":
                text = node_text(child, src).strip()
                if text.startswith("/**"):
                    return clean_jsdoc(text)
            elif child.type not in ("comment",):
                break
    elif lang == "go":
        # Package comment is a series of // lines before package_clause
        lines: list[str] = []
        for child in root.children:
            if child.type == "comment":
                lines.append(node_text(child, src).lstrip("/ ").strip())
            elif child.type == "package_clause":
                break
        return "\n".join(lines) if lines else None
    elif lang == "rust":
        # //! inner doc comments or /// outer doc comments at top
        for child in root.children:
            if child.type in ("line_comment", "block_comment"):
                text = node_text(child, src).strip()
                if text.startswith("//!") or text.startswith("/*!"):
                    return text.lstrip("/!* ").strip()
            else:
                break
    return None


def extract_symbol_docstring(def_node: Node, src: str, lang: str) -> str | None:
    """Extract the docstring from a symbol's body node."""
    if lang == "python":
        body = def_node.child_by_field_name("body")
        if body is None:
            return None
        for child in body.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type == "string":
                        return clean_string_literal(node_text(sub, src))
                return None
            elif child.type not in ("comment", "newline"):
                return None
        return None

    elif lang in ("typescript", "javascript"):
        return find_preceding_jsdoc(def_node, src)

    elif lang == "go":
        # Leading // comment lines before the function
        parent = def_node.parent
        if parent is None:
            return None
        siblings = list(parent.children)
        idx = next((i for i, s in enumerate(siblings) if s.id == def_node.id), -1)
        if idx <= 0:
            return None
        lines: list[str] = []
        i = idx - 1
        while i >= 0 and siblings[i].type == "comment":
            lines.insert(0, node_text(siblings[i], src).lstrip("/ ").strip())
            i -= 1
        return "\n".join(lines) if lines else None

    elif lang == "rust":
        # /// doc comments before the item
        parent = def_node.parent
        if parent is None:
            return None
        siblings = list(parent.children)
        idx = next((i for i, s in enumerate(siblings) if s.id == def_node.id), -1)
        if idx <= 0:
            return None
        lines: list[str] = []
        i = idx - 1
        while i >= 0 and siblings[i].type in ("line_comment", "block_comment"):
            text = node_text(siblings[i], src).strip()
            if text.startswith("///"):
                lines.insert(0, text.lstrip("/ ").strip())
                i -= 1
            else:
                break
        return "\n".join(lines) if lines else None

    elif lang == "java":
        # /** Javadoc */ comment before the method/class
        return find_preceding_block_comment(def_node, src, "/**")

    return None
