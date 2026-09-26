"""F# binding, nesting and owner-name helpers."""

from __future__ import annotations

from tree_sitter import Node

from ..extractors import node_text

# The two nodes fsharp.scm captures as @symbol.def for a `let` binding. One
# `function_or_value_defn` holds every clause of a `let rec f ... and g ...`
# group, so a clause is delimited by the next one of these, not by the node.
_FSHARP_BINDING_LEFT = ("function_declaration_left", "value_declaration_left")

# A binding whose ancestors include one of these sits inside another
# binding's body, so it is a local, not a member of the module or type.
_FSHARP_BODY_OWNERS = (
    "function_or_value_defn",
    "method_or_prop_defn",
    "fun_expression",
    # A top-level `do` block is executed code, not a declaration site.
    "do",
    "do_expression",
)

# Type shapes whose `type_name` child names the enclosing type of a member.
_FSHARP_TYPE_OWNERS = (
    "anon_type_defn",
    "record_type_defn",
    "union_type_defn",
    "enum_type_defn",
    "delegate_type_defn",
    "type_abbrev_defn",
    "type_extension",
)


def _fsharp_binding_is_nested(left_node: Node) -> bool:
    """True when a ``let`` binding is local to another binding's body.

    A nested ``let`` has the same node shape as a top-level one -- the
    grammar distinguishes them only by what encloses them -- so the generic
    callable-ancestor filter cannot see it: that filter looks for an
    ancestor whose *own* type is a captured callable, and the captured node
    here is the binding's left-hand side, never an ancestor of anything.
    A ``let`` directly inside a type body is deliberately not nested: it is
    that type's field.
    """
    ancestor = left_node.parent  # the binding's own function_or_value_defn
    ancestor = ancestor.parent if ancestor is not None else None
    while ancestor is not None:
        if ancestor.type in _FSHARP_BODY_OWNERS:
            return True
        ancestor = ancestor.parent
    return False


def _fsharp_binding_end_line(left_node: Node) -> int:
    """Last line of the clause introduced by *left_node*, 1-indexed.

    Walks forward to the next clause's left-hand side rather than taking the
    left node's next sibling, because a return-type annotation
    (``let f x : int = ...``) sits between the left node and the body.
    """
    parent = left_node.parent
    if parent is None:
        return left_node.end_point[0] + 1
    end = left_node.end_point[0] + 1
    seen = False
    for child in parent.named_children:
        if child.id == left_node.id:
            seen = True
            continue
        if not seen:
            continue
        if child.type in _FSHARP_BINDING_LEFT or child.type == "xml_doc":
            # A doc comment inside the defn belongs to the clause after it.
            break
        end = max(end, child.end_point[0] + 1)
    return end


def _fsharp_binding_has_params(left_node: Node) -> bool:
    """True when a value_declaration_left actually binds a function.

    let total (xs: int list) : int = ... has parameters, but the return
    type makes the grammar read the whole head as one value pattern: the
    name and every parameter pattern become siblings inside a single
    identifier_pattern. A plain let x: int = 5 leaves that node with
    the name alone, which is what separates the two.
    """
    holder = next(
        (c for c in left_node.named_children if c.type == "identifier_pattern"), None
    )
    return holder is not None and holder.named_child_count > 1


def _fsharp_parent_is_type(def_node: Node) -> bool:
    """True when the innermost owner enclosing an F# symbol is a type rather
    than a nested module.

    A ``let`` inside ``module Inner = ...`` is still a plain function, not a
    method -- only a type body turns a binding into a member. The generic
    function-to-method promotion in parser.py has no notion of "module", so
    it needs this to tell the two owners apart before promoting.
    """
    ancestor = def_node.parent
    while ancestor is not None:
        if ancestor.type in _FSHARP_TYPE_OWNERS:
            return True
        if ancestor.type == "module_defn":
            return False
        ancestor = ancestor.parent
    return False


def _fsharp_owner_name(owner: Node, src: str) -> str | None:
    """The declared name of one F# type or nested-module node."""
    if owner.type == "module_defn":
        ident = next((c for c in owner.named_children if c.type == "identifier"), None)
        return node_text(ident, src).strip() if ident is not None else None
    name_node = owner.child_by_field_name("type_name")
    if name_node is None:
        holder = next((c for c in owner.named_children if c.type == "type_name"), None)
        name_node = holder.child_by_field_name("type_name") if holder else None
    if name_node is None:
        return None
    if name_node.type == "long_identifier":
        idents = [c for c in name_node.named_children if c.type == "identifier"]
        name_node = idents[-1] if idents else name_node
    return node_text(name_node, src).strip() or None


def _fsharp_parent_name(def_node: Node, src: str) -> str | None:
    """Return the dotted owner path of an F# symbol.

    No F# type node carries a ``name`` field -- the name lives on a
    ``type_name`` child, tagged with a ``type_name`` field of its own -- so
    the generic nesting walk in ``_find_parent`` finds nothing to read.

    Every enclosing nested module joins the path, not just the nearest
    owner: one file may hold ``module A`` and ``module B`` each declaring a
    ``type Dup`` with a ``Go`` member, and a symbol id is path plus parent
    plus name, so a nearest-owner parent would give both members one id.
    A file-level ``module``/``namespace`` header is skipped, since it is the
    whole file and distinguishes nothing within it.
    """
    parts: list[str] = []
    ancestor = def_node.parent
    while ancestor is not None:
        if ancestor.type in _FSHARP_TYPE_OWNERS or ancestor.type == "module_defn":
            name = _fsharp_owner_name(ancestor, src)
            if name:
                parts.append(name)
        ancestor = ancestor.parent
    return ".".join(reversed(parts)) or None
