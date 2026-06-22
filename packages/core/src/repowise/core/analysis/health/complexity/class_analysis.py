"""Class-level analysis (LCOM4 / god-class).

``_collect_classes`` builds a ``ClassComplexity`` per class-like node for
languages that opt in (``class_kinds`` non-empty). ``_compute_lcom4`` is the
cohesion metric: connected components over the methods, where two methods are
linked if they share an instance member or one calls the other. The safety
valve returns ``1`` ("no signal") when no instance-member references are
detected, so an unmapped language never produces a false ``low_cohesion`` hit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .ast_utils import _IDENTIFIER_SUFFIX, _find_name
from .languages import LanguageNodeMap
from .models import ClassComplexity, FunctionComplexity
from .nloc import _count_nloc

if TYPE_CHECKING:
    from tree_sitter import Node

_PROP_FIELD_NAMES = ("property", "attribute", "field", "name")
# ``expression`` is C#'s receiver field on ``member_access_expression`` (its
# ``this`` token is unnamed, so the positional fallback would pick the member).
_OBJECT_FIELD_NAMES = ("object", "value", "argument", "operand", "expression")


def _class_name(node: Node) -> str:
    """Best-effort class/impl name.

    Tries the ``name`` field (most class grammars), then ``type`` (Rust's
    ``impl T`` exposes the implemented type there), then the generic
    identifier scan.
    """
    for field_name in ("name", "type"):
        child = node.child_by_field_name(field_name)
        if child is not None and child.text is not None:
            return child.text.decode("utf-8", errors="replace")
    return _find_name(node)


def _collect_class_nodes(root: Node, lmap: LanguageNodeMap) -> list[Node]:
    """All class-like grouping nodes in the file (pre-order).

    Descends through the whole tree so nested classes are found too;
    each becomes its own ``ClassComplexity``.
    """
    out: list[Node] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        # ``is_named`` filters out keyword tokens that share a type name
        # with an expression node (e.g. the ``class`` keyword vs a
        # ``class`` expression in tree-sitter-typescript).
        if node.type in lmap.class_kinds and node.is_named:
            out.append(node)
        for child in node.children:
            stack.append(child)
    return out


def _collect_class_methods(class_node: Node, lmap: LanguageNodeMap) -> list[Node]:
    """Direct method nodes of *class_node*.

    Stops at nested classes (their methods belong to the inner class) and
    does not descend into a method body (nested local defs roll up into
    the method, mirroring ``_collect_function_nodes``).
    """
    methods: list[Node] = []
    stack: list[Node] = list(class_node.children)
    while stack:
        node = stack.pop()
        if node.type in lmap.class_kinds:
            continue  # nested class — its methods are not ours
        if node.type in lmap.function_kinds:
            methods.append(node)
            continue  # don't descend into the method body
        for child in node.children:
            stack.append(child)
    return methods


def _self_member_name(node: Node, lmap: LanguageNodeMap) -> str | None:
    """Extract ``member`` from a ``self.member`` / ``this.member`` access.

    Returns the member name when the receiver token is one of the
    language's ``self_identifiers``; otherwise ``None`` (so ``other.x`` and
    ``a.b.c``'s outer hops are ignored — only direct instance access
    counts toward cohesion).
    """
    obj: Node | None = None
    for field_name in _OBJECT_FIELD_NAMES:
        obj = node.child_by_field_name(field_name)
        if obj is not None:
            break
    if obj is None:
        obj = next((c for c in node.children if c.is_named), None)

    prop: Node | None = None
    for field_name in _PROP_FIELD_NAMES:
        prop = node.child_by_field_name(field_name)
        if prop is not None:
            break
    if prop is None:
        named = [c for c in node.children if c.is_named]
        prop = next(
            (c for c in reversed(named) if c.type.endswith(_IDENTIFIER_SUFFIX)),
            None,
        )

    if obj is None or prop is None or obj is prop:
        return None
    if obj.text is None or prop.text is None:
        return None
    if obj.text.decode("utf-8", errors="replace") not in lmap.self_identifiers:
        return None
    return prop.text.decode("utf-8", errors="replace")


def _collect_self_members(method_node: Node, lmap: LanguageNodeMap) -> set[str]:
    """Set of instance-member names referenced by *method_node*.

    Walks the method body (descending through nested functions/lambdas,
    which close over the same instance) but stops at nested class
    definitions. Both field reads and method calls reduce to a member
    name here — both are evidence two methods touch the same thing.
    """
    members: set[str] = set()
    if not lmap.self_identifiers or not lmap.member_access_kinds:
        return members
    stack: list[Node] = list(method_node.children)
    while stack:
        node = stack.pop()
        if node.type in lmap.class_kinds:
            continue  # nested class has its own self
        if node.type in lmap.member_access_kinds:
            name = _self_member_name(node, lmap)
            if name:
                members.add(name)
        for child in node.children:
            stack.append(child)
    return members


def _compute_lcom4(
    method_nodes: list[Node],
    method_fcs: list[FunctionComplexity],
    lmap: LanguageNodeMap,
) -> tuple[int, int]:
    """Return ``(lcom4, field_count)`` for a class.

    LCOM4 = number of connected components over the methods, where two
    methods are connected if they share an instance member or one calls
    the other (a call shows up as a reference to the callee's name).

    **Safety valve:** if no instance-member references are detected at all
    (a pure-static class, or — importantly — a language whose
    member-access node type we have not mapped), return ``1`` rather than
    ``len(methods)``. This prevents ``low_cohesion`` from false-firing on
    an unverified language: a missing mapping yields "no signal", never a
    spurious high-LCOM hit.
    """
    n = len(method_nodes)
    if n == 0:
        return 1, 0

    members_per_method: list[set[str]] = [
        _collect_self_members(node, lmap) for node in method_nodes
    ]
    total_refs = sum(len(m) for m in members_per_method)
    method_names = {fc.name for fc in method_fcs}
    all_members: set[str] = set().union(*members_per_method) if members_per_method else set()
    field_count = len(all_members - method_names)

    if total_refs == 0:
        return 1, field_count

    # Union-find over method indices.
    parent = list(range(n))

    def _find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb

    name_to_idx = {fc.name: i for i, fc in enumerate(method_fcs)}
    # Bucket method indices by each member they reference; the method that
    # *defines* that name (a callee) joins the bucket too, so call edges and
    # shared-field edges are both captured in one pass.
    buckets: dict[str, list[int]] = {}
    for i, members in enumerate(members_per_method):
        for m in members:
            buckets.setdefault(m, []).append(i)
    for member, idxs in buckets.items():
        group = list(idxs)
        callee = name_to_idx.get(member)
        if callee is not None:
            group.append(callee)
        first = group[0]
        for other in group[1:]:
            _union(first, other)

    components = {_find(i) for i in range(n)}
    return len(components), field_count


def _collect_classes(
    root: Node,
    lmap: LanguageNodeMap,
    source: bytes,
    fc_by_node_id: dict[int, FunctionComplexity],
) -> list[ClassComplexity]:
    """Build ``ClassComplexity`` for every class-like node in the file."""
    if not lmap.class_kinds:
        return []
    classes: list[ClassComplexity] = []
    for class_node in _collect_class_nodes(root, lmap):
        method_nodes = _collect_class_methods(class_node, lmap)
        method_fcs = [fc_by_node_id[m.id] for m in method_nodes if m.id in fc_by_node_id]
        # Keep nodes and FCs aligned (a method missing from the function
        # pass — unusual — drops out of both).
        aligned_nodes = [m for m in method_nodes if m.id in fc_by_node_id]
        lcom4, field_count = _compute_lcom4(aligned_nodes, method_fcs, lmap)
        classes.append(
            ClassComplexity(
                name=_class_name(class_node),
                start_line=class_node.start_point[0] + 1,
                end_line=class_node.end_point[0] + 1,
                method_count=len(method_fcs),
                total_nloc=_count_nloc(class_node, source),
                methods=method_fcs,
                lcom4=lcom4,
                max_method_ccn=max((fc.ccn for fc in method_fcs), default=0),
                field_count=field_count,
            )
        )
    return classes
