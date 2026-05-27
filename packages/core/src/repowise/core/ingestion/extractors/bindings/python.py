"""Python import-binding extraction."""

from __future__ import annotations

from tree_sitter import Node

from ...models import Import, NamedBinding
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
                    # ``imported_names`` must carry the *exported* name — the
                    # name as it exists in the target module — because that's
                    # what downstream matches against: the dead-code analyzer
                    # compares it to the source symbol / submodule name, and
                    # ``expand_bare_relative_imports`` uses it to locate the
                    # submodule file. The local alias is preserved on the
                    # binding (``local_name``) for call resolution. Recording
                    # the alias here instead made ``from . import levels as
                    # _levels`` resolve to a non-existent ``_levels`` module
                    # and hid every ``levels.py`` symbol from reachability.
                    names.append(exported)
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


def expand_bare_relative_imports(imports: list[Import]) -> list[Import]:
    """Split ``from . import a, b`` into one Import per imported submodule.

    Tree-sitter captures the module as a lone ``relative_import`` (``.`` or
    ``..``) with no module suffix, so the resolver receives ``module_path=".".``
    The relative-import branch of :func:`resolve_python_import` requires a
    non-empty module name and bails out — silently dropping every plugin /
    registry barrel that uses bare-submodule imports (``from . import npm,
    pypi, cargo, go, nuget`` in ``external_systems/__init__.py`` is the
    canonical example).

    Rewriting each name into its own ``.<name>`` (or ``..<name>``) Import lets
    the existing resolver locate the sibling submodule without any new
    language branches downstream.
    """
    out: list[Import] = []
    for imp in imports:
        stripped = imp.module_path.strip(".")
        is_bare = (
            imp.is_relative
            and stripped == ""
            and imp.module_path  # not the empty string
            and imp.imported_names
            and imp.imported_names != ["*"]
        )
        if not is_bare:
            out.append(imp)
            continue

        dots = imp.module_path  # e.g. ".", ".."
        # ``imported_names`` holds exported (original) submodule names, so key
        # the bindings by exported name — keeping the alias-preserving binding
        # for ``from . import sub as alias`` instead of dropping it.
        bindings_by_name = {(b.exported_name or b.local_name): b for b in imp.bindings}
        for name in imp.imported_names:
            binding = bindings_by_name.get(name) or NamedBinding(
                local_name=name, exported_name=name, source_file=None, is_module_alias=True
            )
            out.append(
                Import(
                    raw_statement=imp.raw_statement,
                    module_path=f"{dots}{name}",
                    imported_names=[name],
                    is_relative=True,
                    resolved_file=None,
                    bindings=[binding],
                )
            )
    return out
