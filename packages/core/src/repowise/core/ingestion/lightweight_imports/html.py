"""``<script src>`` / ``<link href>`` extraction for plain HTML.

These two attributes are the only import system HTML has, and they are
file-level: a page depends on a script or a stylesheet, never on a symbol
inside one. So this yields ``Import`` entries and no symbols, which is exactly
what the no-``LanguageConfig`` path in ``parse_file`` expects.

Unlike its sibling modules this one is not a regex. ``tree-sitter-html`` is
already a dependency — Vue's locator in ``sfc_source`` uses it — so the real
grammar is free here, and it reads unquoted (``src=app.js``) and multi-line
tags that an attribute regex gets wrong. It also knows what a comment is,
though that mattered less than expected: exactly 1 of 777 local references in
the validation corpus sat inside one.

Only ``<script src>`` and ``<link href>`` are captured. ``<a href>`` is
navigation between pages rather than a dependency, and ``<img src>`` is an
asset with no code in it; minting edges for either would inflate the graph
without saying anything about how the code fits together.

Template dialects are the known ceiling. ``{% extends "base.html" %}``,
``{{ template "x" }}`` and ``<%= render %>`` are ordinary text to an HTML
grammar, so a Django or Hugo template parses cleanly and yields nothing here.
That is a property of the tier, not a bug in it — see the ``html`` spec.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Import

if TYPE_CHECKING:
    from tree_sitter import Node

# tag name -> the attribute on it that names a dependency.
_REF_ATTRS: dict[str, str] = {"script": "src", "link": "href"}

# A reference that leaves the repository: protocol-relative (//cdn...),
# absolute URLs, inline data, in-page anchors, and the non-fetching schemes.
_EXTERNAL_PREFIXES = ("//", "http://", "https://", "data:", "#", "mailto:", "javascript:")


def _attributes(start_tag: Node) -> dict[str, str]:
    """Map attribute name (lowercased) to value for one start tag.

    Handles both quoted and bare values: tree-sitter-html nests the former in a
    ``quoted_attribute_value`` wrapper and leaves the latter as a direct
    ``attribute_value`` child.
    """
    attrs: dict[str, str] = {}
    for attr in start_tag.children:
        if attr.type != "attribute":
            continue
        name = value = None
        for child in attr.children:
            if child.type == "attribute_name":
                name = child.text
            elif child.type == "attribute_value":
                value = child.text
            elif child.type == "quoted_attribute_value":
                inner = next((c for c in child.children if c.type == "attribute_value"), None)
                # An empty value ("") has no inner node at all.
                value = inner.text if inner is not None else b""
        if name is not None and value is not None:
            attrs[name.decode("utf-8", "replace").lower()] = value.decode("utf-8", "replace")
    return attrs


def _start_tag(node: Node) -> Node | None:
    return next((c for c in node.children if c.type == "start_tag"), None)


def _walk(node: Node, out: list[tuple[str, str]]) -> None:
    # <script> gets its own node type; <link> is a plain element.
    if node.type in ("script_element", "element"):
        start = _start_tag(node)
        if start is not None:
            name_node = next((c for c in start.children if c.type == "tag_name"), None)
            if name_node is not None and name_node.text is not None:
                tag = name_node.text.decode("utf-8", "replace").lower()
                attr = _REF_ATTRS.get(tag)
                if attr is not None:
                    value = _attributes(start).get(attr, "").strip()
                    if value:
                        out.append((tag, value))
    for child in node.children:
        _walk(child, out)


def extract_html_imports(text: str) -> list[Import]:
    """Return one ``Import`` per local ``<script src>`` / ``<link href>``."""
    from ..sfc_source import _grammar

    grammar = _grammar("tree_sitter_html")
    if grammar is None:
        return []

    try:
        from tree_sitter import Parser

        tree = Parser(grammar).parse(text.encode("utf-8"))
    except Exception:
        # A file we cannot parse contributes no edges rather than wrong ones.
        return []

    refs: list[tuple[str, str]] = []
    _walk(tree.root_node, refs)

    imports: list[Import] = []
    seen: set[str] = set()
    for tag, value in refs:
        # An external reference is dropped here rather than passed to the
        # resolver: it names a CDN, not a file in this repository, and the
        # resolver's job is repo-relative paths.
        if value.startswith(_EXTERNAL_PREFIXES):
            continue
        # A dialect left its own syntax in the attribute ({{ url_for(...) }},
        # {% static %}, <%= asset_path %>) — not a static path.
        if any(sigil in value for sigil in ("{{", "{%", "<%", "${")):
            continue
        if value in seen:
            continue
        seen.add(value)
        imports.append(
            Import(
                raw_statement=f'<{tag} {_REF_ATTRS[tag]}="{value}">',
                module_path=value,
                imported_names=[],
                # Root-relative (/static/app.js) is resolved against the repo
                # root, not the importing document, so it is not "relative".
                is_relative=not value.startswith("/"),
                resolved_file=None,
            )
        )
    return imports
