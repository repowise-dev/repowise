"""Python import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_python_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Python import/import_from statements."""
    names: list[str] = []
    bindings: list[NamedBinding] = []
    is_from_import = stmt_node.type == "import_from_statement"
    # For absolute `from foo.bar import X`, tree-sitter places the module
    # `foo.bar` as the first top-level `dotted_name` child — we must skip it.
    # For relative `from .bar import X`, the module is wrapped in a
    # `relative_import` node, so every top-level `dotted_name` is an
    # imported name and nothing should be skipped.
    has_relative_module = any(c.type == "relative_import" for c in stmt_node.children)
    skip_first_dotted = is_from_import and not has_relative_module
    first_dotted_seen = False

    for child in stmt_node.children:
        if child.type == "wildcard_import":
            return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]

        if child.type == "relative_import":
            continue

        if child.type == "aliased_import":
            name_node = child.child_by_field_name("name") or (
                child.children[0] if child.children else None
            )
            alias_node = child.child_by_field_name("alias")
            if name_node:
                exported = node_text(name_node, src)
                local = node_text(alias_node, src) if alias_node else exported
                if is_from_import:
                    names.append(local)
                    bindings.append(
                        NamedBinding(local_name=local, exported_name=exported, source_file=None)
                    )
                else:
                    bare = exported.split(".")[-1]
                    local = node_text(alias_node, src) if alias_node else bare
                    names.append(local)
                    bindings.append(
                        NamedBinding(
                            local_name=local,
                            exported_name=None,
                            source_file=None,
                            is_module_alias=True,
                        )
                    )

        elif child.type == "dotted_name":
            text = node_text(child, src)
            bare = text.split(".")[-1]
            if skip_first_dotted and not first_dotted_seen:
                first_dotted_seen = True
                continue
            names.append(bare)
            if is_from_import:
                bindings.append(NamedBinding(local_name=bare, exported_name=bare, source_file=None))
            else:
                bindings.append(
                    NamedBinding(
                        local_name=bare,
                        exported_name=None,
                        source_file=None,
                        is_module_alias=True,
                    )
                )

    return names, bindings
