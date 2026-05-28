"""Annotation-processor generated symbol stubs for Java code generators
that follow a predictable name-mangling pattern:

  - MapStruct      ``@Mapper interface XMapper {}``       → ``XMapperImpl``
  - AutoValue      ``@AutoValue class Foo {}``            → ``AutoValue_Foo``
  - Immutables     ``@Value.Immutable class Foo {}``      → ``ImmutableFoo``

The actual generated classes live under ``target/generated-sources/`` or
``build/generated/`` and are typically excluded from the index. By
emitting the symbol stubs here we keep call sites like
``Mappers.getMapper(Foo.class)`` (which resolves to ``FooImpl``),
``AutoValue_Person.builder()``, and ``ImmutablePerson.of(...)``
linked into the graph without parsing the generated source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...models import FileInfo, Symbol
from ..helpers import node_text
from ._helpers import build_synthetic_symbol

if TYPE_CHECKING:
    from tree_sitter import Node


def _has_marker_annotation(node: "Node", marker: str, src: str) -> bool:
    """Walk the modifiers block looking for an annotation by bare name.

    Handles ``@Mapper``, ``@AutoValue``, and ``@Value.Immutable`` —
    the last is a nested annotation expressed as a single
    ``identifier``-or-``scoped_identifier`` node.
    """
    for child in node.children:
        if child.type != "modifiers":
            continue
        for sub in child.children:
            if sub.type not in ("marker_annotation", "annotation"):
                continue
            for leaf in sub.children:
                if leaf.type in ("identifier", "scoped_identifier"):
                    text = node_text(leaf, src).strip()
                    if marker in (text, text.split(".")[-1]):
                        return True
                    break
    return False


def jvm_codegen_synthetic_symbols(
    root: "Node", src: str, file_info: FileInfo
) -> list[Symbol]:
    """Emit ``XMapperImpl`` / ``AutoValue_X`` / ``ImmutableX`` symbol stubs."""
    # Cheap reject path — no annotations means no generated names.
    if "@" not in src:
        return []

    out: list[Symbol] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type not in ("class_declaration", "interface_declaration"):
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        type_name = node_text(name_node, src).strip()
        if not type_name:
            continue
        line = node.start_point[0] + 1

        if _has_marker_annotation(node, "Mapper", src):
            # ``XMapper`` → ``XMapperImpl`` (file-level, since the generated
            # class lives in its own file).
            impl_name = f"{type_name}Impl"
            out.append(build_synthetic_symbol(
                name=impl_name, kind="class",
                signature=f"public class {impl_name} implements {type_name}",
                start_line=line, end_line=line,
                file_info=file_info, parent_name=None,
            ))
        if _has_marker_annotation(node, "AutoValue", src):
            out.append(build_synthetic_symbol(
                name=f"AutoValue_{type_name}", kind="class",
                signature=f"public class AutoValue_{type_name} extends {type_name}",
                start_line=line, end_line=line,
                file_info=file_info, parent_name=None,
            ))
        if (_has_marker_annotation(node, "Immutable", src)
                or _has_marker_annotation(node, "Value.Immutable", src)):
            out.append(build_synthetic_symbol(
                name=f"Immutable{type_name}", kind="class",
                signature=f"public class Immutable{type_name} extends {type_name}",
                start_line=line, end_line=line,
                file_info=file_info, parent_name=None,
            ))

    return out
