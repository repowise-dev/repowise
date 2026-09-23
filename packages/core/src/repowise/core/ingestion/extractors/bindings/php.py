"""PHP import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text

_NAME_NODES = frozenset({"name", "qualified_name"})

#: ``use function`` / ``use const`` import a function or constant, never a
#: class file, so they bind nothing the resolver could follow.
_NON_CLASS_KINDS = frozenset({"function", "const"})


def _clause(clause: Node, prefix: str, src: str) -> tuple[str, str] | None:
    """``(fully qualified name, local name)`` for one ``namespace_use_clause``."""
    target = alias = ""
    saw_as = False
    for sub in clause.children:
        if sub.type in _NON_CLASS_KINDS:
            return None
        if sub.type == "as":
            saw_as = True
        elif sub.type in _NAME_NODES:
            if saw_as:
                alias = node_text(sub, src)
            elif not target:
                target = node_text(sub, src).lstrip("\\")
    if not target:
        return None
    fqn = f"{prefix}\\{target}" if prefix else target
    return fqn, alias or fqn.rsplit("\\", 1)[-1]


def php_use_clauses(stmt_node: Node, src: str) -> list[tuple[str, str]]:
    """Every ``(fully qualified name, local name)`` one ``use`` declaration binds.

    Covers ``use A\\B;``, ``use A\\B as C, D\\E;`` and the grouped
    ``use A\\{B, C\\D as E};``, whose clauses carry only the part after the
    shared prefix. Function and constant imports are skipped.
    """
    prefix = ""
    out: list[tuple[str, str]] = []
    for child in stmt_node.children:
        if child.type in _NON_CLASS_KINDS:
            return []
        if child.type == "namespace_name":
            prefix = node_text(child, src).lstrip("\\")
        elif child.type == "namespace_use_clause":
            if (bound := _clause(child, "", src)) is not None:
                out.append(bound)
        elif child.type == "namespace_use_group":
            for clause in child.children:
                if clause.type == "namespace_use_clause" and (
                    bound := _clause(clause, prefix, src)
                ) is not None:
                    out.append(bound)
    return out


def extract_php_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from PHP use declarations."""
    clauses = php_use_clauses(stmt_node, src)
    return [local for _, local in clauses], [
        NamedBinding(local_name=local, exported_name=fqn, source_file=None)
        for fqn, local in clauses
    ]
