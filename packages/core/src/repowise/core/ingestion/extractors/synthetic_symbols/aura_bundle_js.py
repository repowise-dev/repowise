"""The handler functions of an Aura bundle's controller, helper and renderer.

An Aura bundle's JavaScript is one parenthesised object literal::

    ({
        doInit : function(component, event, helper) { ... },
        save : function(component, event, helper) { ... },
    })

The JavaScript query has no symbol for a ``key: function`` pair (a general
object literal's values are data, not declarations), so every handler in the
file is invisible: markup's ``{!c.doInit}``, a controller's ``helper.load(...)``
and a ``component.get("c.save")`` call have no symbol to land on or start
from. This provider mints one function symbol per function-valued key of that
top-level object, and only in files named ``<bundle>Controller.js``,
``<bundle>Helper.js`` or ``<bundle>Renderer.js`` inside ``aura/<bundle>/``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...languages.specs.aura import aura_bundle
from ...models import FileInfo, Symbol
from ..helpers import node_text
from ._helpers import build_synthetic_symbol

if TYPE_CHECKING:
    from tree_sitter import Node

AURA_JS_ROLES = ("Controller", "Helper", "Renderer")
_FUNCTION_VALUES = frozenset({"function_expression", "function", "arrow_function"})


def aura_js_role(path: str) -> str | None:
    """``Controller`` / ``Helper`` / ``Renderer`` for an Aura bundle JS file, else None."""
    bundle = aura_bundle(path)
    if bundle is None:
        return None
    _bundle_dir, name = bundle
    # The platform pairs these files case-insensitively (NPSP ships
    # HH_AutoComplete/HH_AutocompleteHelper.js).
    base = path.rsplit("/", 1)[-1].lower()
    for role in AURA_JS_ROLES:
        if base == f"{name}{role}.js".lower():
            return role
    return None


def _bundle_object(root: Node) -> Node | None:
    """The object literal of a ``({ ... })`` program, else None."""
    for stmt in root.named_children:
        if stmt.type != "expression_statement" or not stmt.named_children:
            continue
        expr = stmt.named_children[0]
        while expr.type == "parenthesized_expression" and expr.named_children:
            expr = expr.named_children[0]
        if expr.type == "object":
            return expr
    return None


def aura_bundle_js_symbols(root: Node, src: str, file_info: FileInfo) -> list[Symbol]:
    """One function symbol per handler of an Aura controller, helper or renderer."""
    if file_info.language != "javascript" or aura_js_role(file_info.path) is None:
        return []
    obj = _bundle_object(root)
    if obj is None:
        return []
    out: list[Symbol] = []
    for pair in obj.named_children:
        if pair.type != "pair":
            continue
        key = pair.child_by_field_name("key")
        value = pair.child_by_field_name("value")
        if key is None or value is None or value.type not in _FUNCTION_VALUES:
            continue
        name = node_text(key, src).strip().strip("'\"")
        if not name.isidentifier():
            continue
        params = value.child_by_field_name("parameters")
        out.append(
            build_synthetic_symbol(
                name=name,
                kind="function",
                signature=f"{name}: function{node_text(params, src) if params else '()'}",
                start_line=pair.start_point[0] + 1,
                end_line=pair.end_point[0] + 1,
                file_info=file_info,
                parent_name=None,
            )
        )
    return out
