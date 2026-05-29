"""TypeScript / JavaScript import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def _extract_require_bindings(
    stmt_node: Node, src: str
) -> tuple[list[str], list[NamedBinding]] | None:
    """Bindings for a ``variable_declarator`` initialized by ``require(...)``.

    Handles ``const svc = require('./svc')`` (whole-module alias) and
    ``const { a, b: c } = require('./svc')`` (destructured). Returns None when
    the node is not a require() declarator so the caller falls through to the
    existing import/export handling.
    """
    if stmt_node.type != "variable_declarator":
        return None
    value = stmt_node.child_by_field_name("value")
    if value is None or value.type != "call_expression":
        return None
    fn = value.child_by_field_name("function")
    if fn is None or node_text(fn, src) != "require":
        return None

    name_node = stmt_node.child_by_field_name("name")
    if name_node is None:
        return [], []

    names: list[str] = []
    bindings: list[NamedBinding] = []

    if name_node.type == "identifier":
        local = node_text(name_node, src)
        names.append(local)
        bindings.append(
            NamedBinding(
                local_name=local,
                exported_name=None,
                source_file=None,
                is_module_alias=True,
            )
        )
    elif name_node.type == "object_pattern":
        for el in name_node.children:
            if el.type == "shorthand_property_identifier_pattern":
                local = node_text(el, src)
                names.append(local)
                bindings.append(
                    NamedBinding(local_name=local, exported_name=local, source_file=None)
                )
            elif el.type == "pair_pattern":
                key = el.child_by_field_name("key")
                val = el.child_by_field_name("value")
                if key is not None and val is not None:
                    exported = node_text(key, src)
                    local = node_text(val, src)
                    names.append(local)
                    bindings.append(
                        NamedBinding(local_name=local, exported_name=exported, source_file=None)
                    )

    return names, bindings


def extract_ts_js_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from TypeScript/JavaScript import and re-export statements.

    Handles both ``import ... from`` and barrel ``export ... from`` (re-export)
    statements — the query tags re-exports carrying a ``source`` as
    ``@import.statement`` so they flow through the same pipeline. For a
    re-export, ``imported_names`` carries the name as it exists in the *source*
    module (``export { A as B } from`` records ``A``), so the dead-code
    analyzer can match it to the re-exported symbol and an ``index.ts`` barrel
    no longer hides every component it forwards.
    """
    require_result = _extract_require_bindings(stmt_node, src)
    if require_result is not None:
        return require_result

    names: list[str] = []
    bindings: list[NamedBinding] = []

    is_reexport = stmt_node.type == "export_statement"

    for child in stmt_node.children:
        # --- Re-export (barrel) clauses: ``export { X } from`` / ``export *`` ---
        if is_reexport:
            if child.type == "export_clause":
                for spec in child.children:
                    if spec.type != "export_specifier":
                        continue
                    name_node = spec.child_by_field_name("name") or (
                        spec.children[0] if spec.children else None
                    )
                    alias_node = spec.child_by_field_name("alias")
                    if name_node:
                        exported = node_text(name_node, src)
                        local = node_text(alias_node, src) if alias_node else exported
                        names.append(exported)
                        bindings.append(
                            NamedBinding(
                                local_name=local, exported_name=exported, source_file=None
                            )
                        )
            elif child.type == "namespace_export":
                # ``export * as ns from "x"`` — forwards the whole module.
                names.append("*")
                bindings.append(
                    NamedBinding(local_name="*", exported_name=None, source_file=None)
                )
            continue

        if child.type != "import_clause":
            continue
        for sub in child.children:
            if sub.type == "identifier":
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
                            NamedBinding(
                                local_name=local, exported_name=exported, source_file=None
                            )
                        )
            elif sub.type == "namespace_import":
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

    # ``export * from "x"`` carries neither an export_clause nor a
    # namespace_export — just the source. Treat it as a wildcard so every
    # symbol the barrel forwards is reachable.
    if is_reexport and not names:
        names.append("*")
        bindings.append(NamedBinding(local_name="*", exported_name=None, source_file=None))

    return names, bindings
