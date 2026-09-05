"""VB.NET same-namespace + project-import implicit reference resolution.

Background
----------
VB.NET has C#'s same-namespace rule and one more that matters more: the
namespace a file belongs to is usually never written down. Most .vb files
declare no ``Namespace`` block at all and sit directly in the project's
``<RootNamespace>``, so a whole project can be one implicit namespace with
zero ``Imports`` lines between its files. Project-level
``<Import Include="X"/>`` items do for VB what ``global using`` does for C#.

This is the VB.NET binding of the shared implicit-scope scan
(:mod:`.scope_scan`), alongside the C# one in :mod:`.csharp_same_namespace`.
It reuses that module's BCL skip list, since both languages reference the
same framework, and reads namespaces through the dotnet resolver's VB
scanner rather than any C# syntax.

Known ceiling: VB is case-insensitive, so ``Dim x As order`` referring to
``Order`` is not matched. That is a missed edge, never a wrong one.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .csharp_same_namespace import _BCL_COMMON_TYPES
from .scope_scan import FileScope, ScopeTier, emit_scope_edges

if TYPE_CHECKING:
    import networkx as nx

    from ..resolvers.dotnet.index import DotNetProjectIndex

# ``Imports Foo.Bar`` / ``Imports Global.Foo.Bar`` (not the aliased form).
# ``[^\W\d]`` rather than ``[A-Za-z_]``: VB.NET identifiers are Unicode.
_IMPORTS_NS_RE = re.compile(
    r"^\s*Imports\s+(?:Global\.)?([^\W\d][\w.]*)\s*$", re.IGNORECASE | re.MULTILINE
)

# ``Imports Alias = Foo.Bar.Baz`` — the alias name shadows bare identifiers.
_IMPORTS_ALIAS_RE = re.compile(
    r"^\s*Imports\s+([^\W\d]\w*)\s*=", re.IGNORECASE | re.MULTILINE
)

# VB intrinsic type names and the ``My`` root, on top of the shared BCL set.
_VB_INTRINSICS = frozenset({
    "Integer", "Long", "Short", "UInteger", "ULong", "UShort", "Date",
    "Variant", "Nothing", "Me", "MyBase", "MyClass", "My",
})

_SKIP_NAMES = _BCL_COMMON_TYPES | _VB_INTRINSICS

_SAME_NAMESPACE_HINT = "same_namespace"
_PROJECT_IMPORT_HINT = "global_using"

# The shared scan looks for a capitalised ASCII name. VB.NET estates are
# routinely written in a script with no case at all (Chinese, Japanese), where
# that pattern matches nothing, so accept any identifier that does not start
# with a lowercase letter. Precision still comes from the tier lookup, which
# only answers for types declared in the file's own namespace.
_VB_TYPE_IDENT_RE = re.compile(r"\b(?![a-z])[^\W\d]\w*\b")


def resolve_vbnet_same_namespace_refs(
    graph: nx.DiGraph,
    dotnet_index: DotNetProjectIndex | None,
    vb_texts: dict[str, str],
    repo_path: Path | None,
) -> int:
    """Emit same-namespace / project-import ``imports`` edges for VB.NET files.

    *vb_texts* maps repo-relative path → source text. Returns the number of
    edges added.
    """
    from ..resolvers.dotnet.namespace_map import scan_vb_declarations, vb_namespace_key

    def _root_for(path: str) -> str:
        if dotnet_index is None or repo_path is None:
            return ""
        return dotnet_index.vb_root_namespaces.get((repo_path / path).resolve(), "")

    # path → (own namespace keys, declarations), scanned once and reused.
    scanned: dict[str, tuple[list[str], list]] = {}
    ns_types: dict[str, dict[str, list[str]]] = {}
    for path in sorted(vb_texts):
        declared, decls = scan_vb_declarations(vb_texts[path])
        root = _root_for(path)
        own = list(dict.fromkeys(vb_namespace_key(ns, root) for ns in declared))
        if not own and root:
            own = [root]
        scanned[path] = (own, decls)
        for decl in decls:
            bucket = ns_types.setdefault(vb_namespace_key(decl.namespace, root), {})
            files = bucket.setdefault(decl.name, [])
            if path not in files:
                files.append(path)

    def _declarers(namespaces: list[str], ident: str) -> set[str]:
        declaring: set[str] = set()
        for ns in namespaces:
            declaring.update(ns_types.get(ns, {}).get(ident, ()))
        return declaring

    def plan(path: str, text: str) -> FileScope | None:
        own_namespaces = [ns for ns in scanned[path][0] if ns]
        explicit_ns = [m.group(1) for m in _IMPORTS_NS_RE.finditer(text)]

        project_ns: list[str] = []
        if dotnet_index is not None and repo_path is not None:
            vbproj = dotnet_index.file_to_project.get((repo_path / path).resolve())
            if vbproj is not None:
                project_ns = sorted(
                    ns
                    for ns in dotnet_index.globals_for_project(vbproj)
                    if ns in ns_types and ns not in own_namespaces
                )

        if not own_namespaces and not project_ns:
            return None

        # An explicit Imports already resolved through the normal import path,
        # so it shadows the project tier only; the file's own namespace is the
        # closer scope and keeps priority.
        explicit_types: set[str] = set()
        for ns in explicit_ns:
            explicit_types.update(ns_types.get(ns, ()))

        tiers = []
        if own_namespaces:
            tiers.append(
                ScopeTier(
                    hint=_SAME_NAMESPACE_HINT,
                    lookup=lambda ident: _declarers(own_namespaces, ident),
                )
            )
        if project_ns:
            tiers.append(
                ScopeTier(
                    hint=_PROJECT_IMPORT_HINT,
                    lookup=lambda ident: (
                        set() if ident in explicit_types else _declarers(project_ns, ident)
                    ),
                )
            )
        return FileScope(
            tiers=tuple(tiers),
            shadowed=frozenset(m.group(1) for m in _IMPORTS_ALIAS_RE.finditer(text)),
        )

    return emit_scope_edges(
        graph,
        ((path, vb_texts[path]) for path in sorted(vb_texts)),
        plan,
        skip_names=_SKIP_NAMES,
        ident_re=_VB_TYPE_IDENT_RE,
    )
