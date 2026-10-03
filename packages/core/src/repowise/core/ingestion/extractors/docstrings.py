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
    elif lang in ("typescript", "javascript", "svelte", "vue"):
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
    elif lang == "fsharp":
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

    elif lang == "rust":
        # //! inner doc comments or /*! block inner doc comments at top
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
        if inner_lines:
            return "\n".join(inner_lines)
    elif lang in ("cpp", "c", "objectivec"):
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
    elif lang == "elixir":
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

    elif lang == "java":
        for child in root.children:
            if child.type in ("comment", "block_comment", "line_comment"):
                text = node_text(child, src).strip()
                if text.startswith("/**"):
                    return clean_jsdoc(text)
            elif child.type not in ("comment", "package_declaration", "import_declaration"):
                break

    elif lang == "luau":
        # --[[ block comment ]] or run of leading --- comments
        triple_dash_lines: list[str] = []
        for child in root.children:
            if child.type == "comment":
                text = node_text(child, src).strip()
                if text.startswith("--[["):
                    inner = text[4:]
                    if inner.endswith("]]"):
                        inner = inner[:-2]
                    return inner.strip()
                if text.startswith("---"):
                    triple_dash_lines.append(text.lstrip("- ").strip())
                    continue
                continue
            else:
                break
        if triple_dash_lines:
            return "\n".join(triple_dash_lines)

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

    elif lang in ("typescript", "javascript", "svelte", "vue"):
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

    elif lang == "fsharp":
        # `xml_doc` is a preceding SIBLING of the statement, and the captured
        # node is usually nested inside that statement (a binding's left-hand
        # side inside its `declaration_expression`, a type body inside its
        # `type_definition`). Climb only while the node is still the first
        # thing in its parent: the second clause of a `let rec f ... and g`
        # is not, and must not inherit the first clause's doc.
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

    elif lang == "rust":
        # /// doc comments or /** block doc comments before the item.
        # Attributes (#[...]) may appear between the doc comment and the
        # item, so we skip over attribute_item nodes when walking backward.
        parent = def_node.parent
        if parent is None:
            return None
        siblings = list(parent.children)
        idx = next((i for i, s in enumerate(siblings) if s.id == def_node.id), -1)
        if idx <= 0:
            return None
        # Walk backward, skipping attribute_item nodes
        i = idx - 1
        while i >= 0 and siblings[i].type == "attribute_item":
            i -= 1
        # Check for /** block doc comment first
        if i >= 0 and siblings[i].type == "block_comment":
            text = node_text(siblings[i], src).strip()
            if text.startswith("/**"):
                return clean_jsdoc(text)
        # Collect consecutive /// line doc comments
        lines: list[str] = []
        while i >= 0 and siblings[i].type == "line_comment":
            text = node_text(siblings[i], src).strip()
            if text.startswith("///"):
                lines.insert(0, text[3:].strip())
                i -= 1
            else:
                break
        return "\n".join(lines) if lines else None

    elif lang == "java":
        # /** Javadoc */ comment before the method/class
        return find_preceding_block_comment(def_node, src, "/**")

    elif lang in ("cpp", "c", "objectivec"):
        # Doxygen: /** ... */ block comment or /// line comments
        result = find_preceding_block_comment(def_node, src, "/**")
        if result:
            return result
        return _find_preceding_line_doc_comments(def_node, src, "///")

    elif lang == "kotlin":
        # KDoc: /** ... */ block comment before declaration
        return find_preceding_block_comment(def_node, src, "/**")

    elif lang == "elixir":
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

    elif lang == "luau":
        # Luau: --[[ block ]] or --- triple-dash lines preceding the symbol
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
            text = node_text(siblings[i], src).strip()
            if text.startswith("--[["):
                inner = text[4:]
                if inner.endswith("]]"):
                    inner = inner[:-2]
                return inner.strip()
            if text.startswith("---"):
                lines.insert(0, text.lstrip("- ").strip())
                i -= 1
            else:
                break
        return "\n".join(lines) if lines else None

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
