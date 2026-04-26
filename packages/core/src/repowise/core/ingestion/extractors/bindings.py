"""Per-language import binding extraction."""

from __future__ import annotations

from pathlib import PurePosixPath

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
    if lang in ("cpp", "c"):
        return extract_cpp_bindings(stmt_node, src)
    if lang == "kotlin":
        return extract_kotlin_bindings(stmt_node, src)
    if lang == "ruby":
        return extract_ruby_bindings(stmt_node, src)
    if lang == "csharp":
        return extract_csharp_bindings(stmt_node, src)
    if lang == "swift":
        return extract_swift_bindings(stmt_node, src)
    if lang == "scala":
        return extract_scala_bindings(stmt_node, src)
    if lang == "php":
        return extract_php_bindings(stmt_node, src)
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


def extract_kotlin_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Kotlin import declarations."""
    for child in stmt_node.children:
        if child.type == "qualified_identifier":
            full = node_text(child, src)
            parts = full.split(".")
            local = parts[-1]
            if local == "*":
                return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]
            return [local], [NamedBinding(local_name=local, exported_name=local, source_file=None)]
    return [], []


def extract_ruby_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Ruby require/require_relative calls."""
    method_node = None
    for child in stmt_node.children:
        if child.type == "identifier":
            method_node = child
            break
    method_name = node_text(method_node, src) if method_node else ""
    if method_name not in ("require", "require_relative"):
        return [], []

    for child in stmt_node.children:
        if child.type == "argument_list":
            for arg in child.children:
                if arg.type == "string":
                    for sub in arg.children:
                        if sub.type == "string_content":
                            path = node_text(sub, src)
                            stem = PurePosixPath(path).stem
                            return [stem], [
                                NamedBinding(
                                    local_name=stem,
                                    exported_name=None,
                                    source_file=path,
                                    is_module_alias=True,
                                )
                            ]
    return [], []


def extract_csharp_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from C# using directives.

    Recognises:
        using Foo;                          -> NamedBinding(local="Foo", exported="Foo")
        using Foo.Bar.Baz;                  -> NamedBinding(local="Baz", exported="Foo.Bar.Baz")
        using Alias = Foo.Bar.Type;         -> NamedBinding(local="Alias", exported=..., is_module_alias=True)
        global using Foo;                   -> NamedBinding(..., is_global=True)
        using static Foo.Bar;               -> NamedBinding(..., is_static_import=True)
        global using static Foo.Bar;        -> both flags True

    The ``global`` and ``static`` modifiers appear as keyword child nodes
    of ``using_directive`` in tree-sitter-c-sharp >= 0.23.
    """
    alias: str | None = None
    namespace = ""
    is_global = False
    is_static = False
    saw_equals = False
    pending_identifier: str | None = None  # the identifier seen before `=`, if any

    for child in stmt_node.children:
        # Modifier keywords / structural tokens.
        if not child.is_named:
            tok = node_text(child, src)
            if tok == "global":
                is_global = True
                continue
            if tok == "static":
                is_static = True
                continue
            if tok == "=":
                saw_equals = True
                if pending_identifier is not None:
                    alias = pending_identifier
                    pending_identifier = None
                continue
            continue
        # name_equals appears in some grammar versions as a wrapper for
        # `(identifier) =`. Older 0.23 grammars emit the identifier as a
        # bare sibling and the `=` as an anonymous token — handled above.
        if child.type == "name_equals":
            for sub in child.children:
                if sub.type == "identifier":
                    alias = node_text(sub, src)
            continue
        if child.type == "identifier":
            text = node_text(child, src)
            if saw_equals:
                # Identifier on the right of `=` is the alias target.
                namespace = text
            else:
                # Could be the alias (if `=` follows) or a single-name namespace.
                # Stash it and decide once we know.
                pending_identifier = text
            continue
        if child.type == "qualified_name":
            namespace = node_text(child, src)

    # If we never saw `=`, the pending identifier (if any) is the namespace.
    if pending_identifier is not None and not namespace:
        namespace = pending_identifier

    if not namespace:
        return [], []
    local = alias if alias else namespace.rsplit(".", 1)[-1]
    return [local], [
        NamedBinding(
            local_name=local,
            exported_name=namespace,
            source_file=None,
            is_module_alias=alias is not None,
            is_global=is_global,
            is_static_import=is_static,
        )
    ]


def extract_swift_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Swift import declarations."""
    for child in stmt_node.children:
        if child.type == "identifier":
            full = node_text(child, src)
            local = full.split(".")[-1]
            return [local], [
                NamedBinding(
                    local_name=local,
                    exported_name=None,
                    source_file=None,
                    is_module_alias=True,
                )
            ]
    return [], []


def extract_scala_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Scala import declarations."""
    names: list[str] = []
    bindings: list[NamedBinding] = []

    # Get full import text, strip "import " prefix
    full_text = node_text(stmt_node, src).strip()
    if full_text.startswith("import "):
        full_text = full_text[7:].strip()

    # Check for selectors: import pkg.{A, B => C}
    has_selectors = False
    for child in stmt_node.children:
        if child.type == "namespace_selectors":
            has_selectors = True
            for sel_child in child.children:
                if sel_child.type == "arrow_renamed_identifier":
                    # B => C
                    parts = node_text(sel_child, src).split("=>")
                    if len(parts) == 2:
                        exported = parts[0].strip()
                        local = parts[1].strip()
                        names.append(local)
                        bindings.append(
                            NamedBinding(local_name=local, exported_name=exported, source_file=None)
                        )
                elif sel_child.type == "identifier":
                    local = node_text(sel_child, src)
                    names.append(local)
                    bindings.append(
                        NamedBinding(local_name=local, exported_name=local, source_file=None)
                    )
        elif child.type == "namespace_wildcard":
            has_selectors = True
            names.append("*")
            bindings.append(NamedBinding(local_name="*", exported_name=None, source_file=None))

    if not has_selectors:
        # Simple import: import pkg.ClassName — extract last segment
        parts = full_text.split(".")
        local = parts[-1].strip()
        if local and local != "_":
            names.append(local)
            bindings.append(NamedBinding(local_name=local, exported_name=local, source_file=None))

    return names, bindings


def extract_php_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from PHP use declarations."""
    names: list[str] = []
    bindings: list[NamedBinding] = []

    for child in stmt_node.children:
        if child.type == "namespace_use_clause":
            qualified = ""
            alias = None
            saw_as = False
            for sub in child.children:
                if sub.type == "qualified_name":
                    qualified = node_text(sub, src)
                elif sub.type == "as":
                    saw_as = True
                elif sub.type == "name" and saw_as:
                    alias = node_text(sub, src)

            if not qualified:
                continue

            # Get the last segment of the namespace path
            local = qualified.rsplit("\\", 1)[-1] if "\\" in qualified else qualified
            effective_local = alias if alias else local
            names.append(effective_local)
            bindings.append(
                NamedBinding(local_name=effective_local, exported_name=qualified, source_file=None)
            )

    return names, bindings


def extract_cpp_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from C/C++ ``#include`` directives."""
    for child in stmt_node.children:
        if child.type in ("system_lib_string", "string_literal"):
            raw = node_text(child, src).strip().strip('<>"')
            if raw:
                stem = PurePosixPath(raw).stem
                return [stem], [
                    NamedBinding(
                        local_name=stem,
                        exported_name=None,
                        source_file=raw,
                        is_module_alias=True,
                    )
                ]
    return [], []
