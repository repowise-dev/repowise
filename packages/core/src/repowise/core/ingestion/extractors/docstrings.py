"""Module-level and symbol-level docstring extraction."""

from __future__ import annotations

import re

from tree_sitter import Node

from .helpers import (
    clean_jsdoc,
    clean_string_literal,
    find_preceding_block_comment,
    find_preceding_jsdoc,
    node_text,
)


# C# XML doc comments use a small set of tags. We extract <summary> as the
# primary docstring text and drop the structural markup. The fragments are
# rarely strict XML (e.g. unclosed <see cref="..."/> in legacy code), so a
# real parser would refuse to load them — regex extraction is correct here.
_CSHARP_SUMMARY_RE = re.compile(r"<summary>\s*(.*?)\s*</summary>", re.DOTALL | re.IGNORECASE)
_CSHARP_TAG_RE = re.compile(r"<[^>]+>")
_CSHARP_INHERITDOC_RE = re.compile(r"<inheritdoc\s*/?>", re.IGNORECASE)


def _csharp_clean_xml_doc(text: str) -> str:
    """Strip XML scaffolding from a C# /// XML doc string.

    If a <summary> block is present, return its inner text (with tags
    removed). Otherwise return the input with all tags stripped. This
    mirrors the convention dotnet's documentation tooling applies when
    rendering Markdown.
    """
    if _CSHARP_INHERITDOC_RE.search(text):
        # Mark the doc so a future post-pass can resolve it from the parent.
        return "{inheritdoc}"
    summary_match = _CSHARP_SUMMARY_RE.search(text)
    body = summary_match.group(1) if summary_match else text
    body = _CSHARP_TAG_RE.sub("", body)
    return " ".join(body.split())


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
    elif lang in ("cpp", "c"):
        # Doxygen: first /** ... */ block comment before any declaration
        for child in root.children:
            if child.type in ("comment", "block_comment"):
                text = node_text(child, src).strip()
                if text.startswith("/**"):
                    return clean_jsdoc(text)
            elif child.type not in (
                "comment",
                "preproc_include",
                "preproc_ifdef",
                "preproc_ifndef",
            ):
                break
    elif lang == "kotlin":
        for child in root.children:
            if child.type in ("comment", "multiline_comment"):
                text = node_text(child, src).strip()
                if text.startswith("/**"):
                    return clean_jsdoc(text)
            elif child.type not in ("comment", "package_header", "import"):
                break
    elif lang == "ruby":
        lines: list[str] = []
        for child in root.children:
            if child.type == "comment":
                lines.append(node_text(child, src).lstrip("# ").strip())
            else:
                break
        return "\n".join(lines) if lines else None

    elif lang == "csharp":
        # Module-level XML doc: a run of `///` lines, optionally preceded by
        # a /** block. Either form may appear before any using directives or
        # the (file-scoped) namespace declaration.
        triple_slash_lines: list[str] = []
        for child in root.children:
            if child.type == "comment":
                text = node_text(child, src).strip()
                if text.startswith("/**"):
                    return clean_jsdoc(text)
                if text.startswith("///"):
                    triple_slash_lines.append(text.lstrip("/ ").strip())
                    continue
                # Plain // comment — ignore but keep scanning for /// runs.
                continue
            elif child.type not in (
                "comment",
                "using_directive",
                "global_using_directive",
                "extern_alias_directive",
            ):
                break
        if triple_slash_lines:
            return _csharp_clean_xml_doc("\n".join(triple_slash_lines))

    elif lang == "swift":
        for child in root.children:
            if child.type == "comment":
                text = node_text(child, src).strip()
                if text.startswith("/**"):
                    return clean_jsdoc(text)
                elif text.startswith("///"):
                    return text.lstrip("/ ").strip()
            elif child.type not in ("comment", "import_declaration"):
                break

    elif lang == "scala":
        for child in root.children:
            if child.type == "comment":
                text = node_text(child, src).strip()
                if text.startswith("/**"):
                    return clean_jsdoc(text)
            elif child.type not in ("comment", "import_declaration"):
                break

    elif lang == "php":
        for child in root.children:
            if child.type == "comment":
                text = node_text(child, src).strip()
                if text.startswith("/**"):
                    return clean_jsdoc(text)
            elif child.type not in (
                "comment",
                "php_tag",
                "namespace_definition",
                "namespace_use_declaration",
            ):
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

    elif lang in ("cpp", "c"):
        # Doxygen: /** ... */ block comment or /// line comments
        result = find_preceding_block_comment(def_node, src, "/**")
        if result:
            return result
        return _find_preceding_line_doc_comments(def_node, src, "///")

    elif lang == "kotlin":
        # KDoc: /** ... */ block comment before declaration
        return find_preceding_block_comment(def_node, src, "/**")

    elif lang == "ruby":
        # RDoc/YARD: # comment lines before method/class
        return _find_preceding_line_doc_comments(def_node, src, "#")

    elif lang == "csharp":
        # XML doc comments: /// lines or /** block. After collecting the raw
        # text, strip XML tags so callers see the human-readable summary.
        result = find_preceding_block_comment(def_node, src, "/**")
        if not result:
            result = _find_preceding_line_doc_comments(def_node, src, "///")
        if result:
            return _csharp_clean_xml_doc(result)
        return None

    elif lang == "swift":
        # Swift doc: /** block or /// lines
        result = find_preceding_block_comment(def_node, src, "/**")
        if result:
            return result
        return _find_preceding_line_doc_comments(def_node, src, "///")

    elif lang == "scala":
        # ScalaDoc: /** ... */
        return find_preceding_block_comment(def_node, src, "/**")

    elif lang == "php":
        # PHPDoc: /** ... */
        return find_preceding_block_comment(def_node, src, "/**")

    return None


def _find_preceding_line_doc_comments(node: Node, src: str, prefix: str) -> str | None:
    """Collect consecutive line comments with *prefix* (e.g. ``///``) before *node*."""
    parent = node.parent
    if parent is None:
        return None
    siblings = list(parent.children)
    idx = next((i for i, s in enumerate(siblings) if s.id == node.id), -1)
    if idx <= 0:
        return None
    lines: list[str] = []
    i = idx - 1
    while i >= 0 and siblings[i].type in ("comment", "line_comment"):
        text = node_text(siblings[i], src).strip()
        if text.startswith(prefix):
            lines.insert(0, text[len(prefix) :].strip())
            i -= 1
        else:
            break
    return "\n".join(lines) if lines else None
