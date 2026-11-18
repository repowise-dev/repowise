"""Perl import-binding extraction: the subs a ``use`` brings into the importer's scope.

``use Foo;`` imports Foo's default ``@EXPORT``, which is not known here, so it
counts as a wildcard. ``use Foo ()`` and ``require Foo`` load Foo and import
nothing. ``use Foo qw(a b)`` / ``use Foo 'a', 'b'`` import exactly those subs;
an export tag (``:all``) or pattern (``/^re/``) counts as a wildcard again.
Variables (``$x``, ``@y``, ``%z``) bind no sub and are skipped.
"""

from __future__ import annotations

import re

from tree_sitter import Node

from ...models import NamedBinding

_WILDCARD = ["*"]
_STRING_KINDS = frozenset({"string_literal", "interpolated_string_literal", "quoted_word_list"})
# The module name and an optional version (``use Foo 1.2 qw(x)``) are not imports.
_NOT_ARGUMENTS = frozenset({"package", "number", "version"})
_SUB_NAME = re.compile(r"^&?([A-Za-z_]\w*)$")


def _text(node: Node) -> str:
    return node.text.decode("utf-8", "replace") if node.text else ""


def _words(node: Node) -> list[str]:
    if node.type in _STRING_KINDS:
        return "".join(_text(c) for c in node.named_children if c.type == "string_content").split()
    return [word for child in node.named_children for word in _words(child)]


def perl_imported_names(stmt_node: Node) -> list[str]:
    """The subs a ``use`` / ``require`` statement imports; ``["*"]`` for its default exports."""
    if stmt_node.type != "use_statement":
        return []
    arguments = [c for c in stmt_node.named_children if c.type not in _NOT_ARGUMENTS]
    if not arguments:
        return list(_WILDCARD)
    words = [word for argument in arguments for word in _words(argument)]
    if any(word.startswith((":", "/", "!")) for word in words):
        return list(_WILDCARD)
    return list(dict.fromkeys(m.group(1) for word in words if (m := _SUB_NAME.match(word))))


def extract_perl_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """``(imported_names, bindings)`` for one ``use`` or ``require`` statement."""
    names = perl_imported_names(stmt_node)
    if names == _WILDCARD:
        return names, []
    return names, [NamedBinding(local_name=n, exported_name=n, source_file=None) for n in names]
