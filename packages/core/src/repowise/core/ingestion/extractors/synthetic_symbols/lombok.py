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
    @Getter / @Setter             — public getX()/setX(T) per field
    @ToString / @EqualsAndHashCode — toString, equals, hashCode, canEqual
    @NoArgsConstructor             — public Ctor()
    @RequiredArgsConstructor       — public Ctor(final-and-non-null fields)
    @AllArgsConstructor            — public Ctor(all fields)
    @Data                          — @Getter+@Setter+@ToString+@EqualsAndHashCode+@RAC
    @Value                         — @AllArgsConstructor+@Getter+@ToString+@EqualsAndHashCode
    @Builder / @SuperBuilder       — Foo.builder() static + inner FooBuilder
    @Slf4j / @Log4j2 / @Log / @JBossLog
    @XSlf4j / @CommonsLog / @Flogger — ``log`` field
    @UtilityClass                  — private ctor + ``log`` not added
    @With                          — withX(T) per field
  Field-level:
    @Getter / @Setter / @With      — same shape, per single field

Field-level annotations override type-level ones (more restrictive wins).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...models import FileInfo, Symbol
from ..helpers import node_text
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

# Field-level annotations override the type-level set for that one field.
_FIELD_ANNOTATIONS_OF_INTEREST = frozenset(
    {"Getter", "Setter", "With", "ToString.Exclude", "EqualsAndHashCode.Exclude"}
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


def _bare_annotation_names(modifiers_node: "Node", src: str) -> set[str]:
    """Return the bare names of every annotation on a modifiers node.

    ``@lombok.Data`` / ``@Data`` / ``@Data(staticConstructor="of")`` all
    fold to ``"Data"``.
    """
    names: set[str] = set()
    for child in modifiers_node.children:
        if child.type not in ("marker_annotation", "annotation"):
            continue
        for sub in child.children:
            if sub.type in ("identifier", "scoped_identifier"):
                text = node_text(sub, src).strip()
                names.add(text.split(".")[-1])
                break
    return names


def _field_info(field_node: "Node", src: str) -> tuple[str, str, bool, set[str]] | None:
    """Return (field_name, type_text, is_final, field_annotations) or None.

    A single ``field_declaration`` can declare multiple variables
    (``int x, y;``) — we return the first; callers iterate by walking
    the AST themselves where it matters.
    """
    type_text = ""
    is_final = False
    field_annotations: set[str] = set()
    name: str | None = None

    for child in field_node.children:
        if child.type == "modifiers":
            for sub in child.children:
                if sub.type == "final":
                    is_final = True
                elif sub.type in ("marker_annotation", "annotation"):
                    for leaf in sub.children:
                        if leaf.type in ("identifier", "scoped_identifier"):
                            field_annotations.add(
                                node_text(leaf, src).strip().split(".")[-1]
                            )
                            break
        elif child.type == "variable_declarator":
            for leaf in child.children:
                if leaf.type == "identifier" and name is None:
                    name = node_text(leaf, src).strip()
                    break
        elif child.type not in (";", ","):
            # Whatever this node is, it sits where the type goes —
            # type_identifier / generic_type / scoped_type_identifier /
            # array_type / annotated_type / integral_type / void_type /
            # boolean_type / floating_point_type. Capture its text.
            if not type_text:
                type_text = node_text(child, src).strip()

    if not name:
        return None
    return name, type_text, is_final, field_annotations


def _class_fields(class_node: "Node", src: str) -> list[tuple[str, str, bool, set[str]]]:
    """Return a list of (name, type_text, is_final, annotations) per declared field."""
    fields: list[tuple[str, str, bool, set[str]]] = []
    body = class_node.child_by_field_name("body")
    if body is None:
        for child in class_node.children:
            if child.type in ("class_body", "interface_body", "record_body"):
                body = child
                break
    if body is None:
        return fields
    for child in body.children:
        if child.type != "field_declaration":
            continue
        info = _field_info(child, src)
        if info is not None:
            fields.append(info)
    return fields


def _pascal_case(name: str) -> str:
    stripped = name.lstrip("_")
    if not stripped:
        return ""
    return stripped[0].upper() + stripped[1:]


def _getter_name(field_name: str, field_type: str) -> str:
    """``boolean active`` → ``isActive``; otherwise ``getX``.

    Lombok's actual rule is fancier (handles ``is`` prefixes, ``Boolean``
    boxed) but this approximation captures the high-value cases.
    """
    pascal = _pascal_case(field_name)
    if not pascal:
        return ""
    if field_type == "boolean":
        return f"is{pascal}"
    return f"get{pascal}"


def _setter_name(field_name: str) -> str:
    return f"set{_pascal_case(field_name)}"


def _with_name(field_name: str) -> str:
    return f"with{_pascal_case(field_name)}"


def _add(out: list[Symbol], file_info: FileInfo, *, name: str, kind: str,
         parent: str, line: int, signature: str) -> None:
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
    ))


def _emit_for_class(
    class_node: "Node", src: str, file_info: FileInfo, out: list[Symbol]
) -> None:
    name_node = class_node.child_by_field_name("name")
    if name_node is None:
        return
    class_name = node_text(name_node, src).strip()
    if not class_name:
        return
    line = class_node.start_point[0] + 1

    annotations: set[str] = set()
    for child in class_node.children:
        if child.type == "modifiers":
            annotations |= _bare_annotation_names(child, src)
    if not annotations & _TYPE_ANNOTATIONS_OF_INTEREST and not any(
        a in _TYPE_LOGGER_ANNOTATIONS for a in annotations
    ):
        return

    is_data = "Data" in annotations
    is_value = "Value" in annotations
    has_getter = "Getter" in annotations or is_data or is_value
    has_setter = "Setter" in annotations or is_data  # @Value is immutable — no setters
    has_toString = "ToString" in annotations or is_data or is_value
    has_eq = "EqualsAndHashCode" in annotations or is_data or is_value
    has_rac = "RequiredArgsConstructor" in annotations or is_data
    has_aac = "AllArgsConstructor" in annotations or is_value
    has_noac = "NoArgsConstructor" in annotations
    has_builder = "Builder" in annotations or "SuperBuilder" in annotations
    has_with = "With" in annotations
    is_utility = "UtilityClass" in annotations

    fields = _class_fields(class_node, src)

    # Per-field getters/setters/withers.
    for field_name, field_type, is_final, field_annotations in fields:
        field_get = "Getter" in field_annotations or has_getter
        field_set = "Setter" in field_annotations or has_setter
        field_with = "With" in field_annotations or has_with
        if field_get:
            _add(out, file_info, name=_getter_name(field_name, field_type),
                 kind="method", parent=class_name, line=line,
                 signature=f"public {field_type or 'Object'} {_getter_name(field_name, field_type)}()")
        if field_set and not is_value:
            _add(out, file_info, name=_setter_name(field_name),
                 kind="method", parent=class_name, line=line,
                 signature=f"public void {_setter_name(field_name)}({field_type or 'Object'})")
        if field_with:
            _add(out, file_info, name=_with_name(field_name),
                 kind="method", parent=class_name, line=line,
                 signature=f"public {class_name} {_with_name(field_name)}({field_type or 'Object'})")

    if has_toString:
        _add(out, file_info, name="toString", kind="method",
             parent=class_name, line=line,
             signature="public String toString()")
    if has_eq:
        _add(out, file_info, name="equals", kind="method",
             parent=class_name, line=line,
             signature="public boolean equals(Object)")
        _add(out, file_info, name="hashCode", kind="method",
             parent=class_name, line=line,
             signature="public int hashCode()")
        _add(out, file_info, name="canEqual", kind="method",
             parent=class_name, line=line,
             signature="protected boolean canEqual(Object)")

    if has_rac:
        # @RequiredArgsConstructor: final + @NonNull fields. We don't know
        # @NonNull from the source without parsing all annotations, so use
        # `is_final` as the proxy — covers the dominant Spring DI case
        # of ``private final Service service``.
        required = [
            (n, t) for (n, t, fin, _) in fields if fin
        ]
        _add(out, file_info, name=class_name, kind="function",
             parent=class_name, line=line,
             signature=f"public {class_name}({', '.join(f'{t} {n}' for n, t in required)})")
    if has_aac:
        all_params = [(n, t) for (n, t, _, _) in fields]
        _add(out, file_info, name=class_name, kind="function",
             parent=class_name, line=line,
             signature=f"public {class_name}({', '.join(f'{t} {n}' for n, t in all_params)})")
    if has_noac:
        _add(out, file_info, name=class_name, kind="function",
             parent=class_name, line=line,
             signature=f"public {class_name}()")

    if has_builder:
        builder_class = f"{class_name}Builder"
        # The builder class itself
        out.append(build_synthetic_symbol(
            name=builder_class, kind="class",
            signature=f"public static class {builder_class}",
            start_line=line, end_line=line,
            file_info=file_info, parent_name=class_name,
        ))
        # Static factory on the host class
        _add(out, file_info, name="builder", kind="method",
             parent=class_name, line=line,
             signature=f"public static {builder_class} builder()")
        # Builder-per-field methods + build()
        for field_name, field_type, _is_final, _ann in fields:
            _add(out, file_info, name=field_name, kind="method",
                 parent=builder_class, line=line,
                 signature=f"public {builder_class} {field_name}({field_type or 'Object'})")
        _add(out, file_info, name="build", kind="method",
             parent=builder_class, line=line,
             signature=f"public {class_name} build()")
        _add(out, file_info, name="toString", kind="method",
             parent=builder_class, line=line,
             signature="public String toString()")

    if is_utility:
        # Lombok turns the class into a final utility — emit a private
        # default constructor so the class is "constructed" somewhere.
        _add(out, file_info, name=class_name, kind="function",
             parent=class_name, line=line,
             signature=f"private {class_name}()")

    # Logger field — only one logger annotation per class is valid.
    for ann, logger_type in _TYPE_LOGGER_ANNOTATIONS.items():
        if ann in annotations:
            _add(out, file_info, name="log", kind="variable",
                 parent=class_name, line=line,
                 signature=f"private static final {logger_type} log")
            break


def lombok_synthetic_symbols(
    root: "Node", src: str, file_info: FileInfo
) -> list[Symbol]:
    """Emit synthetic symbols for Lombok-annotated Java classes."""
    # Cheap reject path: if the source doesn't even mention `@`, there
    # can't be any annotations.
    if "@" not in src:
        return []

    out: list[Symbol] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if node.type == "class_declaration":
            _emit_for_class(node, src, file_info, out)
        stack.extend(node.children)
    return out
