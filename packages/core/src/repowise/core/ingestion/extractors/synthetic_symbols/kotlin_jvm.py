"""Kotlin compile-time-synthesised symbols.

The Kotlin compiler injects names that never appear in source:
  - ``data class Point(val x: Int, val y: Int)``
    → ``component1()``, ``component2()``, ``copy()``,
       ``equals``, ``hashCode``, ``toString``
  - ``enum class Color { RED, GREEN }``
    → ``values()``, ``valueOf(String)``
  - ``object Foo { ... }`` / ``companion object { ... }``
    → static ``INSTANCE`` field at JVM level (Java code accesses it
       as ``Foo.INSTANCE``); for companion objects, host-class methods
       referenced through ``Foo.Companion`` resolve via the same name.

This provider emits those names so cross-language code (Java ↔ Kotlin)
and same-language destructuring / copy / valueOf calls resolve to real
graph nodes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...models import FileInfo, Symbol
from ..helpers import node_text
from ._helpers import build_synthetic_symbol

if TYPE_CHECKING:
    from tree_sitter import Node


def _is_data_class(class_node: "Node") -> bool:
    for child in class_node.children:
        if child.type != "modifiers":
            continue
        for sub in child.children:
            if sub.type != "class_modifier":
                continue
            for leaf in sub.children:
                if leaf.type == "data":
                    return True
    return False


def _is_enum_class(class_node: "Node") -> bool:
    for child in class_node.children:
        if child.type != "modifiers":
            continue
        for sub in child.children:
            if sub.type != "class_modifier":
                continue
            for leaf in sub.children:
                if leaf.type == "enum":
                    return True
    return False


def _primary_components(class_node: "Node", src: str) -> list[tuple[str, str]]:
    """Return [(name, type_text), ...] for primary-ctor parameters."""
    out: list[tuple[str, str]] = []
    for child in class_node.children:
        if child.type != "primary_constructor":
            continue
        for sub in child.children:
            if sub.type != "class_parameters":
                continue
            for param in sub.children:
                if param.type != "class_parameter":
                    continue
                name: str | None = None
                type_text = ""
                for leaf in param.children:
                    if leaf.type == "identifier" and name is None:
                        name = node_text(leaf, src).strip()
                    elif leaf.type in ("user_type", "nullable_type", "function_type"):
                        type_text = node_text(leaf, src).strip()
                if name:
                    out.append((name, type_text))
    return out


def kotlin_synthetic_symbols(
    root: "Node", src: str, file_info: FileInfo
) -> list[Symbol]:
    """Emit data-class / enum / object synthesised symbols."""
    out: list[Symbol] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)

        if node.type == "class_declaration":
            name_node = node.child_by_field_name("name") or next(
                (c for c in node.children if c.type == "identifier"),
                None,
            )
            if name_node is None:
                continue
            class_name = node_text(name_node, src).strip()
            if not class_name:
                continue
            line = node.start_point[0] + 1

            if _is_data_class(node):
                components = _primary_components(node, src)
                for i, (cname, ctype) in enumerate(components, start=1):
                    out.append(build_synthetic_symbol(
                        name=f"component{i}", kind="method",
                        signature=f"public {ctype or 'Any'} component{i}()",
                        start_line=line, end_line=line,
                        file_info=file_info, parent_name=class_name,
                    ))
                # copy(...) — same params as primary ctor.
                copy_params = ", ".join(
                    f"{n}: {t or 'Any'}" for n, t in components
                )
                out.append(build_synthetic_symbol(
                    name="copy", kind="method",
                    signature=f"public {class_name} copy({copy_params})",
                    start_line=line, end_line=line,
                    file_info=file_info, parent_name=class_name,
                ))
                for nm in ("equals", "hashCode", "toString"):
                    out.append(build_synthetic_symbol(
                        name=nm, kind="method",
                        signature=f"public {nm}()",
                        start_line=line, end_line=line,
                        file_info=file_info, parent_name=class_name,
                    ))

            if _is_enum_class(node):
                out.append(build_synthetic_symbol(
                    name="values", kind="method",
                    signature=f"public static {class_name}[] values()",
                    start_line=line, end_line=line,
                    file_info=file_info, parent_name=class_name,
                ))
                out.append(build_synthetic_symbol(
                    name="valueOf", kind="method",
                    signature=f"public static {class_name} valueOf(String)",
                    start_line=line, end_line=line,
                    file_info=file_info, parent_name=class_name,
                ))

        elif node.type == "object_declaration":
            name_node = node.child_by_field_name("name") or next(
                (c for c in node.children if c.type == "identifier"),
                None,
            )
            if name_node is None:
                continue
            object_name = node_text(name_node, src).strip()
            if not object_name:
                continue
            line = node.start_point[0] + 1
            # Kotlin compiles `object Foo { ... }` to a class with a
            # static ``INSTANCE`` field. Java callers reach members via
            # ``Foo.INSTANCE.method()``; this surfaces the field so the
            # access is resolvable.
            out.append(build_synthetic_symbol(
                name="INSTANCE", kind="variable",
                signature=f"public static final {object_name} INSTANCE",
                start_line=line, end_line=line,
                file_info=file_info, parent_name=object_name,
            ))

    return out
