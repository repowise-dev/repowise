"""Module-level and symbol-level docstring extraction."""

from __future__ import annotations

import re
from collections.abc import Callable

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


def _elixir_attribute_doc(node: Node, src: str, names: tuple[str, ...]) -> str | None:
    """Return the string held by ``@doc`` / ``@moduledoc``, if *node* is one.

    A doc attribute is a ``unary_operator`` whose operator is ``@`` and whose
    operand is a call to ``doc``/``moduledoc`` with a single string argument.
    ``@doc false`` marks a function as undocumented and carries no string, so
    it yields nothing.
    """
    if node.type != "unary_operator":
        return None
    operator = node.child_by_field_name("operator")
    if operator is None or operator.type != "@":
        return None
    operand = node.child_by_field_name("operand")
    if operand is None or operand.type != "call":
        return None
    target = operand.child_by_field_name("target")
    if target is None or node_text(target, src).strip() not in names:
        return None
    arguments = next((c for c in operand.named_children if c.type == "arguments"), None)
    if arguments is None:
        return None
    for child in arguments.named_children:
        if child.type == "string":
            return clean_string_literal(node_text(child, src)) or None
    return None


def _fsharp_doc_text(xml_doc_nodes: list[Node], src: str) -> str | None:
    """Join a run of ``///`` lines and strip the XML the way C# does.

    F# borrows .NET's XML doc comments verbatim, tags and all, so the
    cleaner written for C# is the right one rather than a second copy.
    """
    lines = [node_text(node, src).strip().lstrip("/").strip() for node in xml_doc_nodes]
    joined = "\n".join(line for line in lines if line)
    if not joined:
        return None
    return _csharp_clean_xml_doc(joined) or None


def _fsharp_preceding_doc(node: Node, src: str) -> list[Node]:
    """The ``xml_doc`` run immediately before *node* among its siblings.

    Attributes and keywords may sit between the doc comment and what it
    documents -- an ``and`` clause of a ``let rec`` group is separated from
    its own doc by the ``and`` token -- so both are stepped over the way
    Rust's ``#[...]`` items are.
    """
    parent = node.parent
    if parent is None:
        return []
    siblings = list(parent.children)
    idx = next((i for i, sib in enumerate(siblings) if sib.id == node.id), -1)
    i = idx - 1
    while i >= 0 and (siblings[i].type == "attributes" or not siblings[i].is_named):
        i -= 1
    docs: list[Node] = []
    while i >= 0 and siblings[i].type == "xml_doc":
        docs.insert(0, siblings[i])
        i -= 1
    return docs


def extract_module_docstring(root: Node, src: str, lang: str) -> str | None:
    """Extract a module/file-level docstring or leading comment."""
    reader = _MODULE_DOC.get(lang)
    return reader(root, src) if reader is not None else None


def extract_symbol_docstring(def_node: Node, src: str, lang: str) -> str | None:
    """Extract the docstring from a symbol's body node."""
    reader = _SYMBOL_DOC.get(lang)
    return reader(def_node, src) if reader is not None else None


def _leading_block_doc(
    root: Node, src: str, comment_kinds: tuple[str, ...], skip_kinds: tuple[str, ...]
) -> str | None:
    """The first ``/** ... */`` among the file's leading comments.

    Children of *skip_kinds* (imports, package headers) are stepped over; any
    other node ends the search.
    """
    for child in root.children:
        if child.type in comment_kinds:
            text = node_text(child, src).strip()
            if text.startswith("/**"):
                return clean_jsdoc(text)
        elif child.type not in skip_kinds:
            break
    return None


def _python_module_doc(root: Node, src: str) -> str | None:
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
    return None


def _go_module_doc(root: Node, src: str) -> str | None:
    """The package comment: the ``//`` lines before ``package_clause``."""
    lines: list[str] = []
    for child in root.children:
        if child.type == "comment":
            lines.append(node_text(child, src).lstrip("/ ").strip())
        elif child.type == "package_clause":
            break
    return "\n".join(lines) if lines else None


def _fsharp_module_doc(root: Node, src: str) -> str | None:
    # The file's own doc sits above (or just inside) the `module` /
    # `namespace` header, which is itself the first node of the file.
    head = root
    while head.named_child_count and head.named_children[0].type in (
        "named_module",
        "namespace",
    ):
        head = head.named_children[0]
    docs: list[Node] = []
    for child in head.children:
        if child.type == "xml_doc":
            docs.append(child)
        elif docs or child.type not in ("module", "namespace", "long_identifier"):
            break
    return _fsharp_doc_text(docs, src) if docs else None


def _rust_module_doc(root: Node, src: str) -> str | None:
    """``//!`` inner doc lines, or a ``/*!`` inner block, at the top."""
    inner_lines: list[str] = []
    for child in root.children:
        if child.type == "line_comment":
            text = node_text(child, src).strip()
            if text.startswith("//!"):
                inner_lines.append(text[3:].strip())
                continue
        elif child.type == "block_comment":
            text = node_text(child, src).strip()
            if text.startswith("/*!"):
                inner = text[3:]
                if inner.endswith("*/"):
                    inner = inner[:-2]
                return inner.strip()
        else:
            break
    return "\n".join(inner_lines) if inner_lines else None


def _elixir_module_doc(root: Node, src: str) -> str | None:
    # @moduledoc sits inside the defmodule's do_block, not at file top.
    for child in root.children:
        if child.type != "call":
            continue
        block = next((c for c in child.named_children if c.type == "do_block"), None)
        if block is None:
            continue
        for statement in block.named_children:
            doc = _elixir_attribute_doc(statement, src, ("moduledoc",))
            if doc:
                return doc
        break
    return None


def _ruby_module_doc(root: Node, src: str) -> str | None:
    lines: list[str] = []
    for child in root.children:
        if child.type == "comment":
            lines.append(node_text(child, src).lstrip("# ").strip())
        else:
            break
    return "\n".join(lines) if lines else None


def _csharp_module_doc(root: Node, src: str) -> str | None:
    """A ``/**`` block, else a run of ``///`` lines, before the usings or namespace."""
    triple_slash_lines: list[str] = []
    for child in root.children:
        if child.type == "comment":
            text = node_text(child, src).strip()
            if text.startswith("/**"):
                return clean_jsdoc(text)
            # A plain // comment is skipped; the scan goes on for /// runs.
            if text.startswith("///"):
                triple_slash_lines.append(text.lstrip("/ ").strip())
        elif child.type not in (
            "comment",
            "using_directive",
            "global_using_directive",
            "extern_alias_directive",
        ):
            break
    if triple_slash_lines:
        return _csharp_clean_xml_doc("\n".join(triple_slash_lines))
    return None


def _swift_module_doc(root: Node, src: str) -> str | None:
    for child in root.children:
        if child.type == "comment":
            text = node_text(child, src).strip()
            if text.startswith("/**"):
                return clean_jsdoc(text)
            elif text.startswith("///"):
                return text.lstrip("/ ").strip()
        elif child.type not in ("comment", "import_declaration"):
            break
    return None


def _luau_module_doc(root: Node, src: str) -> str | None:
    """A ``--[[ ]]`` block, else a run of leading ``---`` lines."""
    triple_dash_lines: list[str] = []
    for child in root.children:
        if child.type != "comment":
            break
        text = node_text(child, src).strip()
        if text.startswith("--[["):
            inner = text[4:]
            if inner.endswith("]]"):
                inner = inner[:-2]
            return inner.strip()
        if text.startswith("---"):
            triple_dash_lines.append(text.lstrip("- ").strip())
    return "\n".join(triple_dash_lines) if triple_dash_lines else None


def _block_doc_reader(
    comment_kinds: tuple[str, ...], skip_kinds: tuple[str, ...]
) -> Callable[[Node, str], str | None]:
    return lambda root, src: _leading_block_doc(root, src, comment_kinds, skip_kinds)


_JS_LIKE = ("typescript", "javascript", "svelte", "vue", "astro")
_C_LIKE = ("cpp", "c", "objectivec")

_MODULE_DOC: dict[str, Callable[[Node, str], str | None]] = {
    "python": _python_module_doc,
    **dict.fromkeys(_JS_LIKE, _block_doc_reader(("comment",), ("comment",))),
    "go": _go_module_doc,
    "fsharp": _fsharp_module_doc,
    "rust": _rust_module_doc,
    # Doxygen: the first /** block before any declaration.
    **dict.fromkeys(
        _C_LIKE,
        _block_doc_reader(
            ("comment", "block_comment"),
            ("comment", "preproc_include", "preproc_ifdef", "preproc_ifndef"),
        ),
    ),
    "kotlin": _block_doc_reader(
        ("comment", "multiline_comment"), ("comment", "package_header", "import")
    ),
    "elixir": _elixir_module_doc,
    "ruby": _ruby_module_doc,
    "csharp": _csharp_module_doc,
    "swift": _swift_module_doc,
    "scala": _block_doc_reader(("comment",), ("comment", "import_declaration")),
    "php": _block_doc_reader(
        ("comment",),
        ("comment", "php_tag", "namespace_definition", "namespace_use_declaration"),
    ),
    "java": _block_doc_reader(
        ("comment", "block_comment", "line_comment"),
        ("comment", "package_declaration", "import_declaration"),
    ),
    "luau": _luau_module_doc,
}


def _python_symbol_doc(def_node: Node, src: str) -> str | None:
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


def _preceding_siblings(node: Node) -> tuple[list[Node], int]:
    """*node*'s parent's children and *node*'s index there (``-1`` when unparented)."""
    parent = node.parent
    if parent is None:
        return [], -1
    siblings = list(parent.children)
    return siblings, next((i for i, s in enumerate(siblings) if s.id == node.id), -1)


def _go_symbol_doc(def_node: Node, src: str) -> str | None:
    """The ``//`` lines right before the declaration."""
    siblings, idx = _preceding_siblings(def_node)
    if idx <= 0:
        return None
    lines: list[str] = []
    i = idx - 1
    while i >= 0 and siblings[i].type == "comment":
        lines.insert(0, node_text(siblings[i], src).lstrip("/ ").strip())
        i -= 1
    return "\n".join(lines) if lines else None


def _fsharp_symbol_doc(def_node: Node, src: str) -> str | None:
    """The ``xml_doc`` run before the statement holding *def_node*.

    The captured node is usually nested inside that statement (a binding's
    left-hand side inside its ``declaration_expression``, a type body inside
    its ``type_definition``). Climb only while the node is still the first
    thing in its parent: the second clause of a ``let rec f ... and g`` is not,
    and must not inherit the first clause's doc.
    """
    node: Node | None = def_node
    for _ in range(4):
        if node is None:
            break
        docs = _fsharp_preceding_doc(node, src)
        if docs:
            return _fsharp_doc_text(docs, src)
        parent = node.parent
        if parent is None:
            break
        leading = [
            sib
            for sib in parent.named_children
            if sib.id != node.id and sib.start_byte < node.start_byte
        ]
        if any(sib.type not in ("attributes", "xml_doc") for sib in leading):
            break
        node = parent
    return None


def _rust_symbol_doc(def_node: Node, src: str) -> str | None:
    """A ``/**`` block, else ``///`` lines, before the item, over ``#[...]`` attributes."""
    siblings, idx = _preceding_siblings(def_node)
    if idx <= 0:
        return None
    i = idx - 1
    while i >= 0 and siblings[i].type == "attribute_item":
        i -= 1
    if i >= 0 and siblings[i].type == "block_comment":
        text = node_text(siblings[i], src).strip()
        if text.startswith("/**"):
            return clean_jsdoc(text)
    lines: list[str] = []
    while i >= 0 and siblings[i].type == "line_comment":
        text = node_text(siblings[i], src).strip()
        if not text.startswith("///"):
            break
        lines.insert(0, text[3:].strip())
        i -= 1
    return "\n".join(lines) if lines else None


def _block_or_line_doc(def_node: Node, src: str) -> str | None:
    """A ``/**`` block, else ``///`` lines (Doxygen, Swift)."""
    result = find_preceding_block_comment(def_node, src, "/**")
    if result:
        return result
    return _find_preceding_line_doc_comments(def_node, src, "///")


def _javadoc(def_node: Node, src: str) -> str | None:
    """A ``/** ... */`` block before the declaration (Javadoc, KDoc, ScalaDoc, PHPDoc)."""
    return find_preceding_block_comment(def_node, src, "/**")


def _elixir_symbol_doc(def_node: Node, src: str) -> str | None:
    # @doc is a preceding sibling, with no AST link to the def it
    # documents. Other attributes (@impl, @spec) may sit between the two,
    # so walk back over attributes and stop at the first real statement.
    parent = def_node.parent
    if parent is None:
        return None
    siblings = list(parent.named_children)
    index = next((i for i, s in enumerate(siblings) if s.id == def_node.id), -1)
    for previous in reversed(siblings[:index]):
        if previous.type != "unary_operator":
            return None
        doc = _elixir_attribute_doc(previous, src, ("doc",))
        if doc:
            return doc
    return None


def _ruby_symbol_doc(def_node: Node, src: str) -> str | None:
    """RDoc / YARD: the ``#`` lines before the method or class."""
    return _find_preceding_line_doc_comments(def_node, src, "#")


def _csharp_symbol_doc(def_node: Node, src: str) -> str | None:
    """A ``/**`` block or ``///`` lines, with the XML stripped to the summary."""
    result = find_preceding_block_comment(def_node, src, "/**")
    if not result:
        result = _find_preceding_line_doc_comments(def_node, src, "///")
    if result:
        return _csharp_clean_xml_doc(result)
    return None


def _luau_symbol_doc(def_node: Node, src: str) -> str | None:
    """A ``--[[ ]]`` block, else ``---`` lines, before the symbol."""
    siblings, idx = _preceding_siblings(def_node)
    if idx <= 0:
        return None
    lines: list[str] = []
    i = idx - 1
    while i >= 0 and siblings[i].type == "comment":
        text = node_text(siblings[i], src).strip()
        if text.startswith("--[["):
            inner = text[4:]
            if inner.endswith("]]"):
                inner = inner[:-2]
            return inner.strip()
        if not text.startswith("---"):
            break
        lines.insert(0, text.lstrip("- ").strip())
        i -= 1
    return "\n".join(lines) if lines else None


_SYMBOL_DOC: dict[str, Callable[[Node, str], str | None]] = {
    "python": _python_symbol_doc,
    **dict.fromkeys(_JS_LIKE, find_preceding_jsdoc),
    "go": _go_symbol_doc,
    "fsharp": _fsharp_symbol_doc,
    "rust": _rust_symbol_doc,
    "java": _javadoc,
    "apex": _javadoc,  # ApexDoc
    **dict.fromkeys(_C_LIKE, _block_or_line_doc),
    "kotlin": _javadoc,
    "elixir": _elixir_symbol_doc,
    "ruby": _ruby_symbol_doc,
    "csharp": _csharp_symbol_doc,
    "swift": _block_or_line_doc,
    "scala": _javadoc,
    "php": _javadoc,
    "luau": _luau_symbol_doc,
}


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
