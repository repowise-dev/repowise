"""Per-language import binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ..models import NamedBinding
from .helpers import node_text


def extract_import_bindings(
    stmt_node: Node, src: str, lang: str
) -> tuple[list[str], list[NamedBinding]]:
    """Extract imported names and structured bindings from an import statement.

    Returns (imported_names, bindings) where imported_names is the backward-
    compatible list of local names and bindings carries alias/source detail.
    """
    if lang == "python":
        return extract_python_bindings(stmt_node, src)
    if lang in ("typescript", "javascript"):
        return extract_ts_js_bindings(stmt_node, src)
    if lang == "go":
        return extract_go_bindings(stmt_node, src)
    if lang == "rust":
        return extract_rust_bindings(stmt_node, src)
    if lang == "java":
        return extract_java_bindings(stmt_node, src)
    return [], []


def extract_python_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Python import/import_from statements."""
    names: list[str] = []
    bindings: list[NamedBinding] = []
    is_from_import = stmt_node.type == "import_from_statement"
    first_dotted_seen = False

    for child in stmt_node.children:
        if child.type == "wildcard_import":
            return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]

        if child.type == "aliased_import":
            name_node = child.child_by_field_name("name") or (
                child.children[0] if child.children else None
            )
            alias_node = child.child_by_field_name("alias")
            if name_node:
                exported = node_text(name_node, src)
                local = node_text(alias_node, src) if alias_node else exported
                if is_from_import:
                    # from X import Y as Z
                    names.append(local)
                    bindings.append(
                        NamedBinding(local_name=local, exported_name=exported, source_file=None)
                    )
                else:
                    # import X.Y as Z — module alias
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
            if is_from_import and not first_dotted_seen:
                # First dotted_name in from-import is the module path — skip
                first_dotted_seen = True
                continue
            names.append(bare)
            if is_from_import:
                bindings.append(NamedBinding(local_name=bare, exported_name=bare, source_file=None))
            else:
                # import X.Y.Z — module alias
                bindings.append(
                    NamedBinding(
                        local_name=bare,
                        exported_name=None,
                        source_file=None,
                        is_module_alias=True,
                    )
                )

    return names, bindings


def extract_ts_js_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from TypeScript/JavaScript import statements."""
    names: list[str] = []
    bindings: list[NamedBinding] = []

    for child in stmt_node.children:
        if child.type != "import_clause":
            continue
        for sub in child.children:
            if sub.type == "identifier":
                # default import: import React from 'react'
                local = node_text(sub, src)
                names.append(local)
                bindings.append(
                    NamedBinding(local_name=local, exported_name="default", source_file=None)
                )
            elif sub.type == "named_imports":
                for spec in sub.children:
                    if spec.type != "import_specifier":
                        continue
                    name_node = spec.child_by_field_name("name") or (
                        spec.children[0] if spec.children else None
                    )
                    alias_node = spec.child_by_field_name("alias")
                    if name_node:
                        exported = node_text(name_node, src)
                        local = node_text(alias_node, src) if alias_node else exported
                        names.append(local)
                        bindings.append(
                            NamedBinding(local_name=local, exported_name=exported, source_file=None)
                        )
            elif sub.type == "namespace_import":
                # import * as ns from 'mod'
                ns_name = None
                for ns_child in sub.children:
                    if ns_child.type == "identifier":
                        ns_name = node_text(ns_child, src)
                if ns_name:
                    names.append(ns_name)
                    bindings.append(
                        NamedBinding(
                            local_name=ns_name,
                            exported_name=None,
                            source_file=None,
                            is_module_alias=True,
                        )
                    )
                else:
                    names.append("*")
                    bindings.append(
                        NamedBinding(local_name="*", exported_name=None, source_file=None)
                    )

    return names, bindings


def extract_go_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Go import specs."""
    # Go import_spec: optional alias identifier + string literal path
    alias_node = stmt_node.child_by_field_name("name")
    path_node = stmt_node.child_by_field_name("path")

    if path_node is None:
        # Fallback: find the first string literal child
        for child in stmt_node.children:
            if child.type == "interpreted_string_literal":
                path_node = child
                break
    if path_node is None:
        return [], []

    path_text = node_text(path_node, src).strip("\"'` ")
    default_name = path_text.rsplit("/", 1)[-1]

    if alias_node:
        alias = node_text(alias_node, src)
        if alias == ".":
            return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]
        if alias == "_":
            return [], []
        return [alias], [
            NamedBinding(
                local_name=alias, exported_name=None, source_file=None, is_module_alias=True
            )
        ]

    return [default_name], [
        NamedBinding(
            local_name=default_name,
            exported_name=None,
            source_file=None,
            is_module_alias=True,
        )
    ]


def extract_rust_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Rust use declarations."""
    arg_node = stmt_node.child_by_field_name("argument")
    if arg_node is None:
        # Fallback: first meaningful child
        for child in stmt_node.children:
            if child.type not in ("use", ";", "pub", "visibility_modifier"):
                arg_node = child
                break
    if arg_node is None:
        return [], []

    names: list[str] = []
    bindings: list[NamedBinding] = []
    _parse_rust_use_tree(arg_node, src, names, bindings, depth=0)
    return names, bindings


def _parse_rust_use_tree(
    node: Node,
    src: str,
    names: list[str],
    bindings: list[NamedBinding],
    depth: int,
) -> None:
    """Recursively parse a Rust use-tree into named bindings."""
    if depth > 10:
        return

    if node.type == "use_as_clause":
        path_child = node.child_by_field_name("path") or (
            node.children[0] if node.children else None
        )
        alias_child = node.child_by_field_name("alias") or (
            node.children[-1] if len(node.children) >= 2 else None
        )
        if path_child and alias_child and path_child != alias_child:
            exported = node_text(path_child, src).rsplit("::", 1)[-1]
            local = node_text(alias_child, src)
            names.append(local)
            bindings.append(
                NamedBinding(local_name=local, exported_name=exported, source_file=None)
            )
        return

    if node.type == "use_wildcard":
        names.append("*")
        bindings.append(NamedBinding(local_name="*", exported_name=None, source_file=None))
        return

    if node.type == "use_list":
        for child in node.children:
            if child.type in ("{", "}", ","):
                continue
            _parse_rust_use_tree(child, src, names, bindings, depth + 1)
        return

    if node.type == "scoped_use_list":
        # e.g., std::collections::{HashMap, BTreeMap}
        for child in node.children:
            if child.type == "use_list":
                _parse_rust_use_tree(child, src, names, bindings, depth + 1)
        return

    # scoped_identifier or identifier — bare name, last segment
    text = node_text(node, src)
    bare = text.rsplit("::", 1)[-1]
    if bare and bare != "*":
        names.append(bare)
        bindings.append(NamedBinding(local_name=bare, exported_name=bare, source_file=None))


def extract_java_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Java import declarations."""
    # Java: import com.example.Foo; -> local_name="Foo"
    for child in stmt_node.children:
        if child.type == "scoped_identifier":
            full = node_text(child, src)
            local = full.rsplit(".", 1)[-1]
            if local == "*":
                return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]
            return [local], [NamedBinding(local_name=local, exported_name=local, source_file=None)]
        if child.type == "asterisk":
            return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]
    return [], []
