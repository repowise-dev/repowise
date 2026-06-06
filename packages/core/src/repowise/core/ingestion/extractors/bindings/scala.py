"""Scala import-binding extraction and import-clause expansion."""

from __future__ import annotations

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def expand_scala_import_clauses(
    stmt_node: Node, src: str
) -> list[tuple[str, list[str]]]:
    """Expand one Scala ``import_declaration`` into (module_path, names) pairs.

    The grammar query captures only the first ``identifier`` child, so the
    parser reconstructs full dotted paths here. One declaration can carry
    several clauses; each yields one or more pairs:

    - ``import com.foo.Bar``            → ``[("com.foo.Bar", ["Bar"])]``
    - ``import com.foo._`` / ``.*``     → ``[("com.foo.*", ["*"])]``
      (Scala 3 ``given`` imports normalise to the same package wildcard —
      the file depends on that package's givens)
    - ``import com.foo.{A, B}``         → one pair per selected name
    - ``import com.foo.{A => B}``       → ``[("com.foo.A", ["B"])]``
    - ``import com.foo.{A => _, _}``    → hidden ``A`` skipped, wildcard kept
    - ``import a.B, c.D``               → one pair per clause
    """
    # Split the declaration's children into comma-separated clauses.
    clauses: list[tuple[list[str], Node | None]] = []
    segments: list[str] = []
    trailer: Node | None = None  # namespace_wildcard | namespace_selectors

    def _flush() -> None:
        nonlocal segments, trailer
        if segments or trailer is not None:
            clauses.append((segments, trailer))
        segments, trailer = [], None

    for child in stmt_node.children:
        if child.type == "identifier":
            segments.append(node_text(child, src))
        elif child.type in ("namespace_wildcard", "namespace_selectors"):
            trailer = child
        elif child.type == ",":
            _flush()
    _flush()

    results: list[tuple[str, list[str]]] = []
    for segs, trail in clauses:
        base = ".".join(segs)
        if trail is None:
            if base:
                results.append((base, [segs[-1]]))
        elif trail.type == "namespace_wildcard":
            if base:
                results.append((f"{base}.*", ["*"]))
        else:  # namespace_selectors: {A, B => C, _}
            for sel in trail.children:
                if sel.type == "identifier":
                    name = node_text(sel, src)
                    if base and name != "_":
                        results.append((f"{base}.{name}", [name]))
                elif sel.type == "arrow_renamed_identifier":
                    parts = node_text(sel, src).split("=>")
                    if len(parts) != 2 or not base:
                        continue
                    source, local = parts[0].strip(), parts[1].strip()
                    if local == "_":
                        continue  # hidden import: {A => _} excludes A
                    results.append((f"{base}.{source}", [local]))
                elif sel.type == "namespace_wildcard":
                    if base:
                        results.append((f"{base}.*", ["*"]))
    return results


def extract_scala_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Scala import declarations."""
    names: list[str] = []
    bindings: list[NamedBinding] = []

    full_text = node_text(stmt_node, src).strip()
    if full_text.startswith("import "):
        full_text = full_text[7:].strip()

    has_selectors = False
    for child in stmt_node.children:
        if child.type == "namespace_selectors":
            has_selectors = True
            for sel_child in child.children:
                if sel_child.type == "arrow_renamed_identifier":
                    parts = node_text(sel_child, src).split("=>")
                    if len(parts) == 2:
                        exported = parts[0].strip()
                        local = parts[1].strip()
                        names.append(local)
                        bindings.append(
                            NamedBinding(
                                local_name=local, exported_name=exported, source_file=None
                            )
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
        parts = full_text.split(".")
        local = parts[-1].strip()
        if local and local != "_":
            names.append(local)
            bindings.append(NamedBinding(local_name=local, exported_name=local, source_file=None))

    return names, bindings
