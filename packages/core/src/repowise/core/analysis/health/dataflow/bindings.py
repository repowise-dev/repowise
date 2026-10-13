"""The names a node binds and the names a file imports, read off the tree.

``bound_names`` / ``binding_target`` read an assignment or declaration's plain
names (not ``a.b`` or ``a[i]``); ``imported_names`` the names a file's
top-level imports bring in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap

_MEMBER_KINDS = frozenset(
    {"attribute", "member_expression", "field_expression", "selector_expression", "field_access"}
)
# Write targets that reach through a name instead of rebinding it.
_REACH_KINDS = _MEMBER_KINDS | {
    "subscript",
    "subscript_expression",
    "index_expression",
    "array_access",
    "pointer_expression",
}
# Binding forms outside ``lmap.assignment_kinds`` / ``local_decl_kinds``: the
# declarator a declaration statement wraps, and module-level constants.
_DECLARATOR_KINDS = frozenset(
    {
        "variable_declarator",
        "init_declarator",
        "var_spec",
        "const_spec",
        "let_declaration",
        "short_var_declaration",
        "const_item",
        "static_item",
    }
)
_TARGET_FIELDS = ("left", "name", "pattern", "declarator")
# Namespaces every file can call without importing them.
_BUILTIN_NAMESPACES = frozenset(
    {"Math", "JSON", "Object", "Array", "Number", "String", "Date", "Promise", "Reflect",
     "Symbol", "BigInt", "Intl", "Integer", "Long", "Double", "Float", "Boolean", "Character"}
)  # fmt: skip


def bound_names(target: Node) -> list[str]:
    """The plain names a binding target binds (not ``a.b`` or ``a[i]``)."""
    out: list[str] = []
    stack = [target]
    while stack:
        node = stack.pop()
        if node.type in _REACH_KINDS or _is_deref(node):
            continue
        if _is_name(node):
            out.append(_text(node))
            continue
        # A declarator's own target only: its type and initializer bind nothing.
        inner = _field(node, _TARGET_FIELDS)
        stack.extend([inner] if inner is not None else node.named_children)
    return out


def binding_target(node: Node, lmap: LanguageNodeMap) -> Node | None:
    """The target of *node* when it binds names, else ``None``."""
    binders = lmap.assignment_kinds | lmap.local_decl_kinds | _DECLARATOR_KINDS
    return _field(node, _TARGET_FIELDS) if node.type in binders else None


def imported_names(root: Node, *, namespaces: bool = True) -> frozenset[str]:
    """Names a file's top-level imports bring in, plus built-in namespaces
    unless *namespaces* is False.

    Every name leaf of an import counts (``os`` and ``path`` of ``os.path``),
    and a quoted path counts by its last segment (Go ``"net/http"`` is
    ``http``).
    """
    names = set(_BUILTIN_NAMESPACES) if namespaces else set()
    for node in root.named_children:
        if "import" not in node.type and node.type != "use_declaration":
            continue
        stack = [node]
        while stack:
            leaf = stack.pop()
            if leaf.named_children:
                stack.extend(leaf.named_children)
                continue
            text = _text(leaf).strip("\"'`")
            names.add(text.rsplit("/", 1)[-1])
    return frozenset(names)


def _field(node: Node, fields: tuple[str, ...]) -> Node | None:
    return next((c for f in fields if (c := node.child_by_field_name(f)) is not None), None)


def _is_deref(node: Node) -> bool:
    return node.type == "unary_expression" and bool(node.children) and node.children[0].type == "*"


def _is_name(node: Node) -> bool:
    return not node.named_children and "identifier" in node.type


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", "replace")
