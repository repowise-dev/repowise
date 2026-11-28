"""Lombok annotation-processor symbol synthesis.

Lombok rewrites the AST at compile time, materialising getters, setters,
constructors, builders and a logger field from short annotation markers.
Tree-sitter sees only the source the user wrote, so a Spring service
declared with ``@RequiredArgsConstructor`` has all injected fields read
as unused, every ``Foo.builder().bar(x).build()`` call against ``@Builder``
is unresolved, and every ``log.info(...)`` against ``@Slf4j`` looks like
an undefined identifier.

This module emits the symbols the Lombok annotation processor would
emit, driven entirely by the annotations the parser already captures
in ``Symbol.decorators`` for the host type. No filesystem coupling and
no second AST walk — Lombok only depends on the class's own annotations
and its declared fields.

Supported annotations:
  Type-level:
    @Getter / @Setter             — getX()/setX(T) per field
    @ToString / @EqualsAndHashCode — toString, equals, hashCode, canEqual
    @NoArgsConstructor             — Ctor()
    @RequiredArgsConstructor       — Ctor(final-and-non-null fields)
    @AllArgsConstructor            — Ctor(all fields)
    @Data                          — @Getter+@Setter+@ToString+@EqualsAndHashCode+@RAC
    @Value                         — @AllArgsConstructor+@Getter+@ToString+@EqualsAndHashCode
    @Builder / @SuperBuilder       — Foo.builder() static + inner FooBuilder
    @Slf4j / @Log4j2 / @Log / @JBossLog
    @XSlf4j / @CommonsLog / @Flogger — ``log`` field
    @UtilityClass                  — private ctor + ``log`` not added
    @With                          — withX(T) per field
    @Accessors(fluent, chain)      - accessor names and setter returns
  Field-level:
    @Getter / @Setter / @With      — same shape, per single field

Field-level annotations override type-level ones. Each member takes the
access Lombok's handlers give it: the ``AccessLevel`` its annotation sets,
else Lombok's default (public; private for the logger; package-private for
the constructor @Builder adds; protected for @SuperBuilder's; private for
a constructor with a static factory, an enum's and a @UtilityClass's).
``AccessLevel.NONE`` generates nothing, except on @Builder, where Lombok
rejects it and builds a public one.

Each member sits on the line javac's Lombok gives
it, so a call edge keys where a compiler-backed index puts it: a getter on
its field (or on the field's ``@Getter``), a setter on the ``@Setter`` /
``@Data`` that asked for it, a builder method on its field, and the rest
on the class annotation that generates them. Constructors stay on the class.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple

from ...models import FileInfo, Symbol
from ...symbol_identity import make_symbol_id
from ..helpers import node_text
from ..visibility import java_visibility
from ._helpers import build_synthetic_symbol

if TYPE_CHECKING:
    from tree_sitter import Node


# Annotations recognised on a class (or record / enum) declaration.
_TYPE_ANNOTATIONS_OF_INTEREST = frozenset(
    {
        "Data", "Value",
        "Getter", "Setter",
        "ToString", "EqualsAndHashCode",
        "NoArgsConstructor", "RequiredArgsConstructor", "AllArgsConstructor",
        "Builder", "SuperBuilder",
        "Slf4j", "Log4j2", "Log", "JBossLog",
        "XSlf4j", "CommonsLog", "Flogger",
        "UtilityClass",
        "With",
    }
)

_TYPE_LOGGER_ANNOTATIONS = {
    "Slf4j": "Logger",
    "XSlf4j": "XLogger",
    "Log4j2": "Logger",
    "Log": "Logger",
    "JBossLog": "Logger",
    "CommonsLog": "Log",
    "Flogger": "FluentLogger",
}


def _annotation_nodes(modifiers_node: Node, src: str) -> dict[str, Node]:
    """Map the bare name of every annotation on a modifiers node to its node.

    ``@lombok.Data`` / ``@Data`` / ``@Data(staticConstructor="of")`` all
    fold to ``"Data"``. The first annotation of a name wins.
    """
    found: dict[str, Node] = {}
    for child in modifiers_node.children:
        if child.type not in ("marker_annotation", "annotation"):
            continue
        for sub in child.children:
            if sub.type in ("identifier", "scoped_identifier"):
                found.setdefault(node_text(sub, src).strip().split(".")[-1], child)
                break
    return found


def _modifiers(decl_node: Node) -> Node | None:
    return next((c for c in decl_node.children if c.type == "modifiers"), None)


def _line(node: Node) -> int:
    return node.start_point[0] + 1


_TYPE_DECLARATIONS = frozenset(
    {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"}
)
_XARGS = frozenset({"NoArgsConstructor", "RequiredArgsConstructor", "AllArgsConstructor"})
# The Java modifiers Lombok writes for an AccessLevel (toJavacModifier):
# PACKAGE and MODULE write none, and NONE generates nothing.
_ACCESS_MODIFIERS: dict[str, tuple[str, ...] | None] = {
    "PUBLIC": ("public",), "PROTECTED": ("protected",), "PRIVATE": ("private",),
    "PACKAGE": (), "MODULE": (), "NONE": None,
}


def _argument(annotation: Node | None, src: str, key: str) -> Node | None:
    """The value given to *key* in *annotation*; a value written alone is ``value``."""
    arguments = annotation.child_by_field_name("arguments") if annotation is not None else None
    children = arguments.named_children if arguments is not None else []
    for child in (c for c in children if "comment" not in c.type):
        if child.type != "element_value_pair":
            if key == "value":
                return child
        elif _pair_key(child, src) == key:
            return child.child_by_field_name("value")
    return None


def _pair_key(pair: Node, src: str) -> str:
    name = pair.child_by_field_name("key")
    return node_text(name, src).strip() if name is not None else ""


def _level_modifiers(
    annotation: Node | None, src: str, key: str = "value", default: str = "PUBLIC"
) -> tuple[str, ...] | None:
    """The modifiers *annotation*'s AccessLevel writes, None for ``AccessLevel.NONE``.

    ``AccessLevel.PRIVATE``, ``lombok.AccessLevel.PRIVATE`` and a statically
    imported ``PRIVATE`` read alike; nothing written is *default*.
    """
    value = _argument(annotation, src, key)
    level = node_text(value, src).strip().rsplit(".", 1)[-1] if value is not None else default
    return _ACCESS_MODIFIERS.get(level, _ACCESS_MODIFIERS[default])


def _visibility(modifiers: tuple[str, ...] | None) -> str | None:
    return None if modifiers is None else java_visibility("", list(modifiers))


def _access(
    annotation: Node | None, src: str, key: str = "value", default: str = "PUBLIC"
) -> str | None:
    """The visibility *annotation* gives what it generates, None for ``AccessLevel.NONE``."""
    return _visibility(_level_modifiers(annotation, src, key, default))


def _static_name(annotation: Node | None, src: str, key: str) -> str:
    """The name a ``staticName`` / ``staticConstructor`` string gives, or ""."""
    value = _argument(annotation, src, key)
    if value is None or value.type != "string_literal":
        return ""
    return node_text(value, src).strip()[1:-1].strip()


def _accessors_flag(annotations: dict[str, Node], src: str, flag: str) -> bool | None:
    """An ``@Accessors`` flag as written, or None when it is not written."""
    accessors = annotations.get("Accessors")
    if accessors is None:
        return None
    found = re.search(flag + r"\s*=\s*(true|false)", node_text(accessors, src))
    return None if found is None else found.group(1) == "true"


def _accessors_style(
    field_ann: dict[str, Node], class_ann: dict[str, Node], src: str
) -> tuple[bool, bool]:
    """(fluent, chain): a field's ``@Accessors`` wins, and chain follows fluent unless written."""
    owner = field_ann if "Accessors" in field_ann else class_ann
    fluent = bool(_accessors_flag(owner, src, "fluent"))
    chain = _accessors_flag(owner, src, "chain")
    return fluent, fluent if chain is None else chain


class _Field(NamedTuple):
    name: str
    type_text: str
    is_final: bool
    is_static: bool
    # Bare annotation name -> annotation node, for this field only.
    annotations: dict[str, Node]
    # Line of the declarator's name, where class-level getters sit.
    name_line: int
    # A final field with an initializer takes no constructor or builder slot.
    initialized: bool


def _field_infos(field_node: Node, src: str) -> list[_Field]:
    """One entry per declarator, so ``int x, y;`` yields both fields."""
    modifiers = _modifiers(field_node)
    kinds = {c.type for c in modifiers.children} if modifiers is not None else set()
    annotations = _annotation_nodes(modifiers, src) if modifiers is not None else {}
    type_node = field_node.child_by_field_name("type")
    type_text = node_text(type_node, src).strip() if type_node is not None else ""
    out: list[_Field] = []
    for child in field_node.children:
        if child.type != "variable_declarator":
            continue
        name_node = child.child_by_field_name("name")
        if name_node is None:
            continue
        out.append(_Field(
            node_text(name_node, src).strip(), type_text, "final" in kinds,
            "static" in kinds, annotations, _line(name_node),
            child.child_by_field_name("value") is not None,
        ))
    return out


def _class_fields(class_node: Node, src: str) -> list[_Field]:
    """Every field declared directly in a class or enum body."""
    body = class_node.child_by_field_name("body")
    if body is None:
        return []
    members = list(body.children)
    # An enum's fields sit after its constants, in one declarations node.
    for child in body.children:
        if child.type == "enum_body_declarations":
            members.extend(child.children)
    fields: list[_Field] = []
    for child in members:
        if child.type == "field_declaration":
            fields.extend(f for f in _field_infos(child, src) if not f.name.startswith("$"))
    return fields


def _declares_constructor(class_node: Node) -> bool:
    body = class_node.child_by_field_name("body")
    return body is not None and any(
        c.type == "constructor_declaration" for c in body.children
    )


def _pascal_case(name: str) -> str:
    stripped = name.lstrip("_")
    if not stripped:
        return ""
    return stripped[0].upper() + stripped[1:]


def _boolean_is_name(field: _Field) -> bool:
    """A primitive ``boolean isActive`` keeps its name as the getter."""
    return field.type_text == "boolean" and re.match(r"is[A-Z]", field.name) is not None


def _getter_name(field: _Field, fluent: bool) -> str:
    """``boolean active`` → ``isActive``; otherwise ``getX``."""
    if fluent or _boolean_is_name(field):
        return field.name
    pascal = _pascal_case(field.name)
    if not pascal:
        return ""
    return f"is{pascal}" if field.type_text == "boolean" else f"get{pascal}"


def _setter_name(field: _Field, fluent: bool) -> str:
    if fluent:
        return field.name
    if _boolean_is_name(field):
        return f"set{field.name[2:]}"
    return f"set{_pascal_case(field.name)}"


def _with_name(field_name: str) -> str:
    return f"with{_pascal_case(field_name)}"


def _add(out: list[Symbol], file_info: FileInfo, *, name: str, kind: str,
         parent: str, line: int, signature: str, visibility: str = "public") -> None:
    if not name:
        return
    out.append(build_synthetic_symbol(
        name=name,
        kind=kind,
        signature=signature,
        start_line=line,
        end_line=line,
        file_info=file_info,
        parent_name=parent,
        visibility=visibility,
    ))


def _ann_line(annotations: dict[str, Node], names: tuple[str, ...], default: int) -> int:
    """Line of the first of *names* present, else *default*."""
    for name in names:
        if name in annotations:
            return _line(annotations[name])
    return default


def _accessor_line(field: _Field, own: Node | None, class_line: int, fluent: bool) -> int:
    """On the field name for a fluent accessor (spelled like the field),
    else on the field annotation that asked for it, else on the class-level
    default."""
    if fluent:
        return field.name_line
    if own is not None:
        return _line(own)
    return class_line


def _class_level(
    annotations: dict[str, Node], explicit: str, implied_by: tuple[str, ...], src: str
) -> bool:
    """An explicit class ``@Getter`` / ``@Setter`` overrides what @Data or @Value imply."""
    if explicit in annotations:
        return _access(annotations[explicit], src) is not None
    return any(name in annotations for name in implied_by)


def _wanted(own: Node | None, class_level: bool, field: _Field, src: str) -> bool:
    """A field annotation decides alone; a class one skips static fields."""
    if own is not None:
        return _access(own, src) is not None
    return class_level and not field.is_static


def _accessor_access(own: Node | None, class_annotation: Node | None, src: str) -> str:
    """A field's own annotation decides, else the class's; @Data and @Value give public."""
    return _access(own if own is not None else class_annotation, src) or "public"


def _emit_accessors(
    fields: list[_Field], annotations: dict[str, Node], src: str,
    file_info: FileInfo, class_name: str, line: int, out: list[Symbol],
) -> None:
    """Per-field getters, setters and withers; a final field gets no setter."""
    class_get = _class_level(annotations, "Getter", ("Data", "Value"), src)
    class_set = _class_level(annotations, "Setter", ("Data",), src)
    class_with = _class_level(annotations, "With", (), src)
    set_line = _ann_line(annotations, ("Setter", "Data"), line)
    with_line = _ann_line(annotations, ("With",), line)
    for field in fields:
        fluent, chain = _accessors_style(field.annotations, annotations, src)
        own = field.annotations.get("Getter")
        if _wanted(own, class_get, field, src):
            name = _getter_name(field, fluent)
            _add(out, file_info, name=name, kind="method", parent=class_name,
                 line=_accessor_line(field, own, field.name_line, fluent),
                 signature=f"{name}() -> {field.type_text or 'Object'}",
                 visibility=_accessor_access(own, annotations.get("Getter"), src))
        own = field.annotations.get("Setter")
        if not field.is_final and _wanted(own, class_set, field, src):
            _emit_setter(field, own, set_line, (fluent, chain), file_info, class_name, out,
                         _accessor_access(own, annotations.get("Setter"), src))
        own = field.annotations.get("With")
        if _wanted(own, class_with, field, src):
            name = _with_name(field.name)
            _add(out, file_info, name=name, kind="method", parent=class_name,
                 line=_accessor_line(field, own, with_line, False),
                 signature=f"{name}({field.type_text or 'Object'} {field.name}) -> {class_name}",
                 visibility=_accessor_access(own, annotations.get("With"), src))


def _emit_setter(
    field: _Field, own: Node | None, class_line: int, style: tuple[bool, bool],
    file_info: FileInfo, class_name: str, out: list[Symbol], visibility: str,
) -> None:
    fluent, chain = style
    name = _setter_name(field, fluent)
    # A chained setter returns its object, so a call chain can go on.
    returns = class_name if chain else "void"
    _add(out, file_info, name=name, kind="method", parent=class_name,
         line=_accessor_line(field, own, class_line, fluent),
         signature=f"{name}({field.type_text or 'Object'} {field.name}) -> {returns}",
         visibility=visibility)


def _emit_builder(
    fields: list[_Field], line: int, file_info: FileInfo, class_name: str,
    out: list[Symbol], *, scope: str, self_typed: bool = False, visibility: str = "public",
) -> None:
    """``Foo.builder()``, the inner ``FooBuilder`` and one method per field.

    A ``@SuperBuilder`` builder is generic in the builder it returns, as
    Lombok writes it, so a subclass builder's chain keeps its own type
    through a method it inherits. *visibility* is that of ``builder()`` and
    the builder class; the builder's methods take it too, but public for
    protected, and its ``toString`` stays public.
    """
    inner = "public" if visibility == "protected" else visibility
    builder_class = f"{class_name}Builder"
    header = builder_class
    returns = builder_class
    if self_typed:
        header = f"{builder_class}<C extends {class_name}, B extends {builder_class}<C, B>>"
        returns = "B"
    builder = build_synthetic_symbol(
        name=builder_class, kind="class",
        signature=f"class {header}",
        start_line=line, end_line=line,
        file_info=file_info, parent_name=class_name, visibility=visibility,
    )
    # A member type's id names every enclosing type, as a written one's does.
    builder.id = make_symbol_id(file_info.path, scope, builder_class)
    out.append(builder)
    _add(out, file_info, name="builder", kind="method",
         parent=class_name, line=line,
         signature=f"builder() -> {builder_class}", visibility=visibility)
    for field in fields:
        if field.is_static or (field.is_final and field.initialized):
            continue
        _add(out, file_info, name=field.name, kind="method",
             parent=builder_class, line=field.name_line,
             signature=f"{field.name}({field.type_text or 'Object'} {field.name}) -> {returns}",
             visibility=inner)
    _add(out, file_info, name="build", kind="method",
         parent=builder_class, line=line,
         signature=f"build() -> {class_name}", visibility=inner)
    _add(out, file_info, name="toString", kind="method",
         parent=builder_class, line=line,
         signature="toString() -> String")


def _type_scope(class_node: Node, src: str) -> str:
    """``Outer::Inner`` for *class_node* and every type declared around it."""
    names: list[str] = []
    node: Node | None = class_node
    while node is not None:
        if node.type in _TYPE_DECLARATIONS:
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                names.append(node_text(name_node, src).strip())
        node = node.parent
    return "::".join(reversed(names))


def _emit_for_class(
    class_node: Node, src: str, file_info: FileInfo, out: list[Symbol]
) -> None:
    name_node = class_node.child_by_field_name("name")
    if name_node is None:
        return
    class_name = node_text(name_node, src).strip()
    if not class_name:
        return
    line = _line(class_node)

    modifiers = _modifiers(class_node)
    annotations = _annotation_nodes(modifiers, src) if modifiers is not None else {}
    fields = _lombok_fields(class_node, annotations, src)
    # Field-level accessors need no class annotation.
    _emit_accessors(fields, annotations, src, file_info, class_name, line, out)
    if not annotations.keys() & _TYPE_ANNOTATIONS_OF_INTEREST:
        return

    _emit_object_methods(annotations, file_info, class_name, line, out,
                         needs_can_equal=_needs_can_equal(class_node, annotations, src))
    _emit_constructors(class_node, annotations, fields, src, file_info, class_name, line, out)
    if "Builder" in annotations or "SuperBuilder" in annotations:
        # Lombok rejects @Builder(access = AccessLevel.NONE) and builds a public builder.
        _emit_builder(fields, _ann_line(annotations, ("Builder", "SuperBuilder"), line),
                      file_info, class_name, out, scope=_type_scope(class_node, src),
                      self_typed="SuperBuilder" in annotations,
                      visibility=_access(annotations.get("Builder"), src, "access") or "public")
    _emit_logger(annotations, src, file_info, class_name, out)


def _lombok_fields(class_node: Node, annotations: dict[str, Node], src: str) -> list[_Field]:
    """The fields of *class_node* as Lombok leaves them before it generates members."""
    fields = _class_fields(class_node, src)
    if "UtilityClass" in annotations:
        # Lombok makes every field of a @UtilityClass static before the rest run.
        fields = [f._replace(is_static=True) for f in fields]
    if _makes_final(annotations, src):
        # Every field that is not static or @NonFinal is then final.
        fields = [
            f if f.is_static or "NonFinal" in f.annotations else f._replace(is_final=True)
            for f in fields
        ]
    return fields


def _emit_logger(
    annotations: dict[str, Node], src: str, file_info: FileInfo, class_name: str,
    out: list[Symbol],
) -> None:
    """The ``log`` field, private unless its ``access`` says otherwise."""
    # Only one logger annotation per class is valid.
    ann = next((a for a in _TYPE_LOGGER_ANNOTATIONS if a in annotations), None)
    modifiers = _level_modifiers(annotations.get(ann), src, "access", "PRIVATE") if ann else None
    if ann is None or modifiers is None:
        return
    _add(out, file_info, name="log", kind="variable",
         parent=class_name, line=_line(annotations[ann]),
         signature=" ".join((*modifiers, "static", "final", _TYPE_LOGGER_ANNOTATIONS[ann], "log")),
         visibility=_visibility(modifiers) or "private")


def _needs_can_equal(class_node: Node, annotations: dict[str, Node], src: str) -> bool:
    """Lombok adds ``canEqual`` unless the class is final and extends nothing
    but Object; @Value makes a class final unless it is @NonFinal."""
    modifiers = _modifiers(class_node)
    written_final = modifiers is not None and any(c.type == "final" for c in modifiers.children)
    final = written_final or ("Value" in annotations and "NonFinal" not in annotations)
    superclass = class_node.child_by_field_name("superclass")
    extends = superclass is not None and node_text(superclass, src).split()[-1] not in (
        "Object", "java.lang.Object",
    )
    return not final or extends


def _emit_object_methods(
    annotations: dict[str, Node], file_info: FileInfo, class_name: str, line: int,
    out: list[Symbol], *, needs_can_equal: bool,
) -> None:
    """toString, equals, hashCode and canEqual, on the annotation that asks for them.

    ``canEqual`` is protected, and only where *needs_can_equal*.
    """
    implied = "Data" in annotations or "Value" in annotations
    if "ToString" in annotations or implied:
        _add(out, file_info, name="toString", kind="method", parent=class_name,
             line=_ann_line(annotations, ("ToString", "Data", "Value"), line),
             signature="toString() -> String")
    if "EqualsAndHashCode" in annotations or implied:
        eq_line = _ann_line(annotations, ("EqualsAndHashCode", "Data", "Value"), line)
        methods = [
            ("equals", "equals(Object o) -> boolean", "public"),
            ("hashCode", "hashCode() -> int", "public"),
        ]
        if needs_can_equal:
            methods.append(("canEqual", "canEqual(Object other) -> boolean", "protected"))
        for name, signature, visibility in methods:
            _add(out, file_info, name=name, kind="method", parent=class_name,
                 line=eq_line, signature=signature, visibility=visibility)


# Each constructor annotation, the one that implies it when it is absent, and
# the parameters it takes.
_CONSTRUCTOR_ANNOTATIONS = (
    ("RequiredArgsConstructor", "Data", "required"),
    ("AllArgsConstructor", "Value", "all"),
)
# Each annotation that makes its constructor whether or not the class declares
# one, and the parameters it takes.
_EXPLICIT_CONSTRUCTORS = (
    *((explicit, params) for explicit, _, params in _CONSTRUCTOR_ANNOTATIONS),
    ("NoArgsConstructor", "none"), ("SuperBuilder", "builder"), ("UtilityClass", "none"),
)


def _makes_final(annotations: dict[str, Node], src: str) -> bool:
    """Whether the class makes its fields final: @Value does, unless a written
    @FieldDefaults takes its place, which does where it sets ``makeFinal = true``."""
    if "FieldDefaults" in annotations:
        value = _argument(annotations["FieldDefaults"], src, "makeFinal")
        return value is not None and node_text(value, src).strip() == "true"
    return "Value" in annotations


def _type_parameters(class_node: Node) -> list[str]:
    """The names of the type parameters of a class (``T`` of ``class P<T>``)."""
    params = class_node.child_by_field_name("type_parameters")
    names = (
        next((c for c in param.named_children if c.type == "type_identifier"), None)
        for param in (params.named_children if params is not None else [])
    )
    return [name.text.decode() for name in names if name is not None and name.text is not None]


def _constructors_asked(
    class_node: Node, annotations: dict[str, Node]
) -> list[tuple[str, str, bool]]:
    """``(annotation, parameters, constructor)`` of each constructor the annotations
    generate; *constructor* is False where only its static factory is."""
    asked = [(name, params, True) for name, params in _EXPLICIT_CONSTRUCTORS if name in annotations]
    if annotations.keys() & _XARGS:
        return asked
    # @SuperBuilder's constructor is made first, so it counts as declared.
    free = not _declares_constructor(class_node) and "SuperBuilder" not in annotations
    if "Builder" in annotations:
        # @Builder's replaces the constructor of @Data and @Value.
        return [*asked, ("Builder", "all", True)] if free else asked
    # A declared constructor leaves @Data and @Value only their static factory.
    return asked + [(implying, params, free) for _, implying, params in _CONSTRUCTOR_ANNOTATIONS
                    if implying in annotations]


def _constructor_access(
    name: str, annotation: Node, src: str, is_enum: bool
) -> tuple[str | None, str, str]:
    """(the constructor's visibility, its static factory's name or "", the factory's).

    The first is None where nothing is generated. Lombok writes PRIVATE on a
    constructor that has a static factory and on every enum constructor; the
    factory keeps the annotation's access.
    """
    fixed = {"UtilityClass": "PRIVATE", "Builder": "PACKAGE", "SuperBuilder": "PROTECTED"}
    if name in fixed:
        return _visibility(_ACCESS_MODIFIERS[fixed[name]]), "", ""
    access: str | None
    if name in ("Data", "Value"):
        access, factory = "public", _static_name(annotation, src, "staticConstructor")
    else:
        access = _access(annotation, src, "access")
        factory = _static_name(annotation, src, "staticName")
    if access is None:
        return None, "", ""
    own = _visibility(_ACCESS_MODIFIERS["PRIVATE"]) if factory or is_enum else access
    return own, factory, access


def _constructor_params(
    group: str, fields: list[_Field], class_node: Node, class_name: str
) -> str:
    """The parameters of a generated constructor that takes *group*."""
    if group == "builder":
        arguments = ", ".join([*_type_parameters(class_node), "?", "?"])
        return f"{class_name}Builder<{arguments}> b"
    slots = [f for f in fields if not f.is_static and not (f.is_final and f.initialized)]
    # @NonNull is not read, so final stands in for "required": the
    # dominant Spring DI shape is ``private final Service service``.
    taken = {"required": [f for f in slots if f.is_final], "all": slots, "none": []}[group]
    return ", ".join(f"{f.type_text} {f.name}" for f in taken)


def _emit_constructors(
    class_node: Node, annotations: dict[str, Node], fields: list[_Field], src: str,
    file_info: FileInfo, class_name: str, line: int, out: list[Symbol],
) -> None:
    """Generated constructors, on the class line where an index keys them, and
    the static factory a ``staticName`` asks for."""
    is_enum = class_node.type == "enum_declaration"
    for name, group, constructor in _constructors_asked(class_node, annotations):
        own, factory, factory_access = _constructor_access(name, annotations[name], src, is_enum)
        if own is None:
            continue
        params = _constructor_params(group, fields, class_node, class_name)
        if constructor:
            # A method, as the Java extractor makes a written constructor.
            _add(out, file_info, name=class_name, kind="method", parent=class_name, line=line,
                 signature=f"{class_name}({params})", visibility=own)
        if factory:
            _add(out, file_info, name=factory, kind="method", parent=class_name, line=line,
                 signature=f"{factory}({params}) -> {class_name}", visibility=factory_access)


def lombok_synthetic_symbols(
    root: Node, src: str, file_info: FileInfo
) -> list[Symbol]:
    """Emit synthetic symbols for Lombok-annotated Java classes and enums."""
    # Lombok annotations need a ``lombok`` import or a qualified name, so a
    # same-named annotation from another library generates nothing.
    if "lombok" not in src:
        return []

    out: list[Symbol] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type in ("class_declaration", "enum_declaration"):
            _emit_for_class(node, src, file_info, out)
        stack.extend(node.children)
    return out


def superbuilder_parent(class_node: Node, src: str, parent: str) -> str | None:
    """The builder ``@SuperBuilder`` on *class_node* extends: its superclass *parent*'s.

    Lombok requires the superclass to carry ``@SuperBuilder`` too, so its
    builder exists wherever the class compiles.
    """
    if "lombok" not in src:
        return None
    modifiers = _modifiers(class_node)
    if modifiers is None or "SuperBuilder" not in _annotation_nodes(modifiers, src):
        return None
    return f"{parent}Builder"
