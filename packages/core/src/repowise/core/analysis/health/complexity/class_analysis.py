"""Class-level analysis (LCOM4 / god-class).

``_collect_classes`` builds a ``ClassComplexity`` per class-like node for
languages that opt in (``class_kinds`` non-empty). ``_compute_lcom4`` is the
cohesion metric: connected components over the methods, where two methods are
linked if they share an instance member or one calls the other. The safety
valve returns ``1`` ("no signal") when no instance-member references are
detected, so an unmapped language never produces a false ``low_cohesion`` hit.

A member reference is explicit (``self.x`` / ``this.x``) or, for a language
whose map names its field declarations, implicit: a bare name that resolves to
a field declared in the class body or to a sibling method, and that no
parameter or local of the method rebinds. A C# ``partial`` class is never
scored, because its other parts live in files this pass does not see, and in
those languages only components that hold state count (``_stateful_groups``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from ....ingestion.python_overload import is_python_overload
from ....test_paths import is_test_related_path
from .ast_utils import _IDENTIFIER_SUFFIX, _find_name, is_function_node
from .languages import LanguageNodeMap, get_language_map
from .models import ClassComplexity, CohesionGroup, FunctionComplexity
from .nloc import CodeLineIndex

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tree_sitter import Node

_PROP_FIELD_NAMES = ("property", "attribute", "field", "name")
# ``expression`` is C#'s receiver field on ``member_access_expression`` (its
# ``this`` token is unnamed, so the positional fallback would pick the member).
_OBJECT_FIELD_NAMES = ("object", "value", "argument", "operand", "expression")
# Fields of a binding node that hold the bound name, in probe order.
_BINDING_NAME_FIELDS = ("name", "declarator", "left", "parameters")
# A method's signature children: names there declare, they do not reference.
_SIGNATURE_FIELDS = frozenset({"name", "declarator", "type", "returns"})
# Declaration words that make a member class-level rather than instance state.
_STATIC_WORDS = frozenset({"static", "const"})


class _ClassBody(NamedTuple):
    """The direct members of one class node."""

    methods: list[Node]
    field_decls: list[Node]
    nested: list[Node]  # nested classes


class _MemberRefs(NamedTuple):
    """Each method's member references, the methods the class body only
    declares (a C++ method defined out of line: a call target, never a field),
    and the indices of the methods that are contracts rather than behaviour
    (constructors and overrides)."""

    per_method: list[set[str]]
    out_of_line: frozenset[str]
    contracts: frozenset[int] = frozenset()


def cohesion_applies(file_path: str, language: str) -> bool:
    """Whether class cohesion findings and splits apply to *file_path*.

    Not in test files of implicit-receiver languages: a test class groups its
    tests by the fixtures they share (a date fixture pair, a datetime pair),
    which reads as a split but is how the tests are meant to be organised.
    """
    lmap = get_language_map(language)
    implicit = lmap is not None and bool(lmap.field_decl_kinds)
    return not (implicit and is_test_related_path(file_path, language))


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


def _text(node: Node) -> str:
    return node.text.decode("utf-8", errors="replace") if node.text is not None else ""


def _collect_class_body(
    class_node: Node, lmap: LanguageNodeMap, language: str, source: str
) -> _ClassBody:
    """Direct method, field-declaration and nested-class nodes of *class_node*.

    Stops at nested types (their members are not ours) and does not descend
    into a method body (nested local defs roll up into the method, mirroring
    ``_collect_function_nodes``). Python ``@overload`` stubs are dropped here
    so they never reach the method counts or LCOM4.
    """
    body = _ClassBody([], [], [])
    stack: list[Node] = list(class_node.children)
    while stack:
        node = stack.pop()
        if node.type in lmap.class_kinds:
            body.nested.append(node)
        elif node.type in lmap.function_kinds:
            if not is_function_node(node, lmap):
                continue  # a nested type the grammar misread: its members are not ours
            if language == "python" and is_python_overload(node, source):
                continue
            body.methods.append(node)
        elif node.type in lmap.field_decl_kinds:
            body.field_decls.append(node)
        elif node.type not in lmap.nested_type_kinds:
            stack.extend(node.children)
    return body


def _decl_words(node: Node) -> set[str]:
    """Keyword and modifier words directly on a declaration (``static``,
    ``partial``, ``override``, Kotlin ``val``)."""
    words: set[str] = set()
    for child in node.children:
        if (
            not child.is_named
            or "modifier" in child.type
            or child.type in ("storage_class_specifier", "binding_pattern_kind")
        ):
            words.update(_text(child).split())
    return words


def _next_in_chain(node: Node, lmap: LanguageNodeMap) -> Node | None:
    nxt = node.child_by_field_name("declarator") or node.child_by_field_name("name")
    if nxt is not None:
        return nxt
    return next(
        (
            c
            for c in node.named_children
            if c.type in lmap.identifier_kinds or c.type.endswith("declarator")
        ),
        None,
    )


def _unwrap_name(node: Node | None, lmap: LanguageNodeMap) -> tuple[Node | None, bool]:
    """Follow a declarator chain (``*p``, ``&r``, ``a[3]``, ``x = 1``, ``f()``)
    down to its name token. The flag says the chain declared a function."""
    is_function = False
    for _ in range(8):
        if node is None or node.type in lmap.identifier_kinds:
            return node, is_function
        is_function = is_function or node.type == "function_declarator"
        node = _next_in_chain(node, lmap)
    return None, is_function


def _variable_declarators(node: Node) -> list[Node]:
    """C# wraps declarators in a ``variable_declaration``; Kotlin's holds the
    name directly."""
    if node.type not in ("variable_declarator", "variable_declaration"):
        return []
    inner = [c for c in node.named_children if c.type == "variable_declarator"]
    return inner or [node]


def _declaration_targets(decl: Node, lmap: LanguageNodeMap) -> list[Node]:
    """The declarator nodes of one class-body declaration. A Kotlin
    constructor parameter is its own first name token (later ones are the
    type and default value)."""
    targets = decl.children_by_field_name("declarator") or decl.children_by_field_name("name")
    if not targets:
        targets = [d for child in decl.named_children for d in _variable_declarators(child)]
    return targets or [c for c in decl.named_children if c.type in lmap.identifier_kinds][:1]


def _declared_members(decl: Node, lmap: LanguageNodeMap) -> tuple[list[str], list[str]]:
    """``(fields, methods)`` one class-body declaration names.

    Static and const members are class-level, not instance state, and a Kotlin
    constructor parameter is a property only with ``val`` / ``var``. A C++
    declarator that is a function is a method declared here and defined out
    of line.
    """
    words = _decl_words(decl)
    if words & _STATIC_WORDS or (decl.type == "class_parameter" and not words & {"val", "var"}):
        return [], []
    fields: list[str] = []
    methods: list[str] = []
    for target in _declaration_targets(decl, lmap):
        name, is_function = _unwrap_name(target, lmap)
        if name is not None:
            (methods if is_function else fields).append(_text(name))
    return fields, methods


def _bound_names(node: Node, lmap: LanguageNodeMap) -> list[str]:
    """Names a parameter or local binding node introduces."""
    for field_name in _BINDING_NAME_FIELDS:
        targets = node.children_by_field_name(field_name)
        if targets:
            names = (_unwrap_name(t, lmap)[0] for t in targets)
            return [_text(n) for n in names if n is not None]
    named = node.named_children
    if not named:
        return [_text(node)]  # C#'s ``x => ...`` parameter is a bare token
    return [_text(c) for c in named if c.type in lmap.identifier_kinds]


def _receiver(node: Node) -> Node | None:
    """The receiver child of a member-access node."""
    for field_name in _OBJECT_FIELD_NAMES:
        obj = node.child_by_field_name(field_name)
        if obj is not None:
            return obj
    return next((c for c in node.children if c.is_named), None)


def _member(node: Node) -> Node | None:
    """The member-name child of a member-access node."""
    for field_name in _PROP_FIELD_NAMES:
        prop = node.child_by_field_name(field_name)
        if prop is not None:
            return prop
    named = [c for c in node.children if c.is_named]
    return next((c for c in reversed(named) if c.type.endswith(_IDENTIFIER_SUFFIX)), None)


def _self_member_name(node: Node, lmap: LanguageNodeMap) -> str | None:
    """Extract ``member`` from a ``self.member`` / ``this.member`` access.

    Returns the member name when the receiver token is one of the
    language's ``self_identifiers``; otherwise ``None`` (so ``other.x`` and
    ``a.b.c``'s outer hops are ignored — only direct instance access
    counts toward cohesion).
    """
    obj, prop = _receiver(node), _member(node)
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


def _foreign_member_id(node: Node, lmap: LanguageNodeMap) -> int | None:
    """The id of the member-name child of ``other.name``: that name belongs
    to another object, never to the enclosing class."""
    if node.type not in lmap.member_access_kinds:
        return None
    obj, prop = _receiver(node), _member(node)
    if obj is None or prop is None or obj.id == prop.id:
        return None  # a bare call (``id()``) has no receiver
    return prop.id


def _body_nodes(method_node: Node, lmap: LanguageNodeMap) -> Iterator[tuple[Node, bool]]:
    """Every node under *method_node* with whether it sits in the signature,
    skipping nested classes and the member side of ``other.name``."""
    stack: list[tuple[Node, bool]] = [
        (child, method_node.field_name_for_child(i) in _SIGNATURE_FIELDS)
        for i, child in enumerate(method_node.children)
    ]
    while stack:
        node, in_signature = stack.pop()
        if node.type in lmap.class_kinds:
            continue  # nested class has its own members
        yield node, in_signature
        skip = _foreign_member_id(node, lmap)
        stack.extend((child, in_signature) for child in node.children if child.id != skip)


def _collect_implicit_members(
    method_node: Node, lmap: LanguageNodeMap, declared: frozenset[str]
) -> set[str]:
    """Bare names in *method_node* that resolve to a declared member.

    A name counts when it is in *declared* (the class's fields and methods),
    is not the member side of ``other.name``, and no parameter or local
    anywhere in the method binds it. Shadowing is method-wide rather than
    scoped, which can only drop an edge, never invent one.
    """
    refs: set[str] = set()
    bound: set[str] = set()
    for node, in_signature in _body_nodes(method_node, lmap):
        if node.type in lmap.binding_kinds:
            bound.update(_bound_names(node, lmap))
        elif node.type in lmap.identifier_kinds and not in_signature:
            refs.add(_text(node))
    return (refs & declared) - bound


def _member_links(
    named_decls: list[tuple[Node, list[str]]],
    nested: list[Node],
    lmap: LanguageNodeMap,
    declared: frozenset[str],
) -> dict[str, set[str]]:
    """Members a reference to one member also reaches.

    A field initializer or computed property reaches what it reads. A nested
    class that reads several outer members ties them together: the outer
    methods that build or call it work on all of them, which no method body
    shows.
    """
    links: dict[str, set[str]] = {}
    for decl, fields in named_decls:
        reads = _collect_implicit_members(decl, lmap, declared) - set(fields)
        for name in fields if reads else ():
            links.setdefault(name, set()).update(reads)
    for inner in nested:
        reads = _collect_implicit_members(inner, lmap, declared)
        for name in reads:
            links.setdefault(name, set()).update(reads - {name})
    return links


def _method_refs(
    node: Node,
    explicit: set[str],
    lmap: LanguageNodeMap,
    declared: frozenset[str],
    links: dict[str, set[str]],
) -> set[str]:
    """One method's references: explicit, implicit, and what they link to."""
    if "static" in _decl_words(node):
        return set()
    members = explicit | _collect_implicit_members(node, lmap, declared)
    for name in [m for m in members if m in links]:
        members |= links[name]
    return members


def _class_member_refs(
    body: _ClassBody,
    method_fcs: list[FunctionComplexity],
    lmap: LanguageNodeMap,
    class_name: str,
) -> _MemberRefs:
    """Each method's member references.

    Explicit ``self.x`` references always count. Where the map names field
    declarations, a bare name that resolves to a declared field or method
    counts too, and a reference reaches whatever ``_member_links`` ties to
    it. A static method has no instance: a bare name in it (an object
    initializer in a factory) is never this object's state.
    """
    explicit = [_collect_self_members(node, lmap) for node in body.methods]
    if not lmap.field_decl_kinds:
        return _MemberRefs(explicit, frozenset())
    fields: set[str] = set()
    out_of_line: set[str] = set()
    named_decls: list[tuple[Node, list[str]]] = []
    for decl in body.field_decls:
        decl_fields, decl_methods = _declared_members(decl, lmap)
        fields.update(decl_fields)
        out_of_line.update(decl_methods)
        named_decls.append((decl, decl_fields))
    declared = frozenset(fields | out_of_line | {fc.name for fc in method_fcs})
    links = _member_links(named_decls, body.nested, lmap, declared)
    per_method = [
        _method_refs(node, members, lmap, declared, links)
        for node, members in zip(body.methods, explicit, strict=True)
    ]
    return _MemberRefs(
        per_method, frozenset(out_of_line), _contract_indices(body.methods, method_fcs, class_name)
    )


def _contract_indices(
    method_nodes: list[Node], method_fcs: list[FunctionComplexity], class_name: str
) -> frozenset[int]:
    """Indices of the constructors and overrides among *method_nodes*."""
    return frozenset(
        i
        for i, (node, fc) in enumerate(zip(method_nodes, method_fcs, strict=True))
        if fc.name == class_name or _is_override(node)
    )


def _is_override(method_node: Node) -> bool:
    """Java ``@Override``, C#/Kotlin ``override``, C++ ``override`` / ``final``."""
    if _decl_words(method_node) & {"@Override", "override"}:
        return True
    declarator = method_node.child_by_field_name("declarator")
    while declarator is not None and declarator.type != "function_declarator":
        declarator = declarator.child_by_field_name("declarator")  # ``T* f() override``
    return declarator is not None and any(
        c.type == "virtual_specifier" for c in declarator.children
    )


def _stateful_groups(
    indexed_groups: list[tuple[list[int], CohesionGroup]], contracts: frozenset[int]
) -> list[CohesionGroup]:
    """The components that are a responsibility of their own.

    Where member access is implicit the graph cannot see inherited members,
    an outer instance or a base constructor, so a method that touches no
    visible state is unplaced rather than a split. A component counts when it
    spans at least two fields (accessors over one field are how a data class
    exposes state) and is not made only of constructors and overrides. A
    constructor sets fields that properties or out-of-line methods this pass
    cannot place may read, and ``toString`` / ``Equals`` are contracts; neither
    is a class to extract.
    """
    return [
        group
        for idxs, group in indexed_groups
        if len(group.fields) >= 2 and not contracts.issuperset(idxs)
    ]


def _tcc(field_sets: list[set[str]]) -> float:
    """Tight Class Cohesion (Bieman-Kang): the fraction of method pairs that
    share at least one instance field, in ``[0, 1]``.

    *field_sets* is the per-method set of instance fields (method names already
    excluded). Returns ``1.0`` ("no signal") when there are fewer than two
    methods, so an unparsed/fieldless class never reads as low-cohesion — the
    same posture as the LCOM4 safety valve.
    """
    n = len(field_sets)
    if n < 2:
        return 1.0
    possible = n * (n - 1) // 2
    if possible == 0:
        return 1.0
    connected = 0
    for i in range(n):
        fi = field_sets[i]
        if not fi:
            continue
        for j in range(i + 1, n):
            if fi & field_sets[j]:
                connected += 1
    return connected / possible


def _member_buckets(
    members_per_method: list[set[str]],
    method_fcs: list[FunctionComplexity],
    merge_overloads: bool,
) -> dict[str, list[int]]:
    """Method indices by each member they reference. With *merge_overloads*
    (implicit receivers) every method also sits in its own name's bucket, so
    same-named methods are one node: a bare call cannot say which overload it
    reaches."""
    buckets: dict[str, list[int]] = {}
    for i, members in enumerate(members_per_method):
        for m in members:
            buckets.setdefault(m, []).append(i)
    if merge_overloads:
        for i, fc in enumerate(method_fcs):
            buckets.setdefault(fc.name, []).append(i)
    return buckets


def _component_roots(
    members_per_method: list[set[str]],
    method_fcs: list[FunctionComplexity],
    merge_overloads: bool,
) -> list[int]:
    """Union-find root of each method index.

    Methods join when they share a bucket (``_member_buckets``); the method
    that *defines* a referenced name (a callee) joins that bucket too, so
    call edges and shared-field edges are both captured in one pass.
    """
    parent = list(range(len(method_fcs)))

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
    for member, idxs in _member_buckets(members_per_method, method_fcs, merge_overloads).items():
        callee = name_to_idx.get(member)
        for other in idxs[1:] + ([callee] if callee is not None else []):
            _union(idxs[0], other)
    return [_find(i) for i in range(len(method_fcs))]


def _indexed_groups(
    roots: list[int],
    method_fcs: list[FunctionComplexity],
    members_per_method: list[set[str]],
    method_names: set[str],
) -> list[tuple[list[int], CohesionGroup]]:
    """The per-component membership behind the LCOM4 integer.

    Each group's methods (and the groups themselves) are ordered by source
    position, ``(start_line, name)`` rather than collection index, so the
    split reads top-to-bottom and is stable across runs. Each group keeps its
    method indices, which stay correct when method names repeat. Its fields
    are the members its methods reference that aren't method names.
    """
    by_root: dict[int, list[int]] = {}
    for i, root in enumerate(roots):
        by_root.setdefault(root, []).append(i)

    def _pos(i: int) -> tuple[int, str]:
        return (method_fcs[i].start_line, method_fcs[i].name)

    indexed: list[tuple[list[int], CohesionGroup]] = []
    for member_idxs in by_root.values():
        member_idxs.sort(key=_pos)
        group_fields: set[str] = set()
        for i in member_idxs:
            group_fields |= members_per_method[i] - method_names
        group = CohesionGroup(
            methods=[method_fcs[i].name for i in member_idxs], fields=sorted(group_fields)
        )
        indexed.append((member_idxs, group))
    indexed.sort(key=lambda pair: _pos(pair[0][0]))
    return indexed


def _compute_lcom4(
    method_nodes: list[Node],
    method_fcs: list[FunctionComplexity],
    lmap: LanguageNodeMap,
    refs: _MemberRefs | None = None,
) -> tuple[int, int, list[CohesionGroup], float]:
    """Return ``(lcom4, field_count, components, tcc)`` for a class.

    LCOM4 = number of connected components over the methods, where two
    methods are connected if they share an instance member or one calls
    the other (a call shows up as a reference to the callee's name).
    ``components`` is the per-component membership behind that integer:
    one ``CohesionGroup`` (methods + the fields they touch) per connected
    component, stable-ordered. It *is* the Extract Class split.

    **Safety valve:** if no instance-member references are detected at all
    (a pure-static class, or — importantly — a language whose
    member-access node type we have not mapped), return ``1`` rather than
    ``len(methods)``. This prevents ``low_cohesion`` from false-firing on
    an unverified language: a missing mapping yields "no signal", never a
    spurious high-LCOM hit. ``components`` is empty in every no-signal case
    (the integer carries no real split to expose).

    *refs* defaults to the explicit ``self.x`` references. Where the map
    names field declarations (implicit receivers), overloads are one node and
    only ``_stateful_groups`` count toward LCOM4.
    """
    if not method_nodes:
        return 1, 0, [], 1.0
    if refs is None:
        refs = _MemberRefs([_collect_self_members(n, lmap) for n in method_nodes], frozenset())
    members_per_method = refs.per_method
    method_names = {fc.name for fc in method_fcs} | refs.out_of_line
    all_members: set[str] = set().union(*members_per_method)
    field_count = len(all_members - method_names)

    # TCC over instance fields only (method-call edges don't count toward it),
    # so it complements LCOM4 rather than restating it.
    tcc = _tcc([members - method_names for members in members_per_method])

    if not all_members:
        return 1, field_count, [], tcc

    implicit = bool(lmap.field_decl_kinds)
    roots = _component_roots(members_per_method, method_fcs, implicit)
    indexed = _indexed_groups(roots, method_fcs, members_per_method, method_names)
    if implicit:
        groups = _stateful_groups(indexed, refs.contracts)
        return max(len(groups), 1), field_count, groups, tcc
    return len(indexed), field_count, [g for _, g in indexed], tcc


def _collect_classes(
    class_nodes: list[Node],
    lmap: LanguageNodeMap,
    source: bytes,
    fc_by_node_id: dict[int, FunctionComplexity],
    code_lines: CodeLineIndex,
    language: str,
) -> list[ClassComplexity]:
    """Build ``ClassComplexity`` for every class-like node in the file.

    *class_nodes* are every named ``class_kinds`` node in the file, nested
    classes included, in the order ``file_scan`` found them.
    """
    if not lmap.class_kinds:
        return []
    source_str = source.decode("utf-8", errors="replace")
    classes: list[ClassComplexity] = []
    for class_node in class_nodes:
        body = _collect_class_body(class_node, lmap, language, source_str)
        method_fcs = [fc_by_node_id[m.id] for m in body.methods if m.id in fc_by_node_id]
        # Keep nodes and FCs aligned (a method missing from the function
        # pass — unusual — drops out of both).
        body = body._replace(methods=[m for m in body.methods if m.id in fc_by_node_id])
        if "partial" in _decl_words(class_node):
            lcom4, field_count, components, tcc = 1, 0, [], 1.0
        else:
            refs = _class_member_refs(body, method_fcs, lmap, _class_name(class_node))
            lcom4, field_count, components, tcc = _compute_lcom4(
                body.methods, method_fcs, lmap, refs
            )
        classes.append(
            ClassComplexity(
                name=_class_name(class_node),
                start_line=class_node.start_point[0] + 1,
                end_line=class_node.end_point[0] + 1,
                method_count=len(method_fcs),
                total_nloc=code_lines.count(class_node, source),
                methods=method_fcs,
                lcom4=lcom4,
                max_method_ccn=max((fc.ccn for fc in method_fcs), default=0),
                field_count=field_count,
                components=components,
                tcc=tcc,
            )
        )
    return classes
