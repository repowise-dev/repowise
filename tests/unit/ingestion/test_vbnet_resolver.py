"""Unit tests for the VB.NET import resolver.

``Imports`` directives name namespaces. Resolution is stem-first (the last
segment, matching the class/module file), then dotted path-suffix; a miss
registers an external node (System.* lands there). These tests build small
repos on disk via ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.vbnet import resolve_vbnet_import


def _ctx_for(repo: Path) -> ResolverContext:
    """Build a ResolverContext rooted at *repo* with all .vb files indexed."""
    vb_files = [str(p.relative_to(repo)) for p in repo.rglob("*.vb")]
    stem_map: dict[str, list[str]] = {}
    for p in vb_files:
        stem = p.rsplit("/", 1)[-1][:-3].lower()
        stem_map.setdefault(stem, []).append(p)
    ctx = ResolverContext(
        path_set=frozenset(vb_files),
        stem_map=stem_map,
        graph=nx.MultiDiGraph(),
        repo_path=repo.resolve(),
    )
    return ctx


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_resolves_local_namespace_to_class_file(tmp_path: Path) -> None:
    """Imports MyApp.Models → MyApp/Models/Customer.vb declares namespace."""
    _write(
        tmp_path,
        "MyApp/Models/Customer.vb",
        "Namespace MyApp.Models\n    Public Class Customer\n    End Class\nEnd Namespace",
    )
    ctx = _ctx_for(tmp_path)
    resolved = resolve_vbnet_import("MyApp.Models", "MyApp/Data/Repo.vb", ctx)
    assert resolved == "MyApp/Models/Customer.vb"


def test_stem_match_picks_up_type_import(tmp_path: Path) -> None:
    """Imports MyApp.Models.Cart → Cart.vb (last dotted segment = class stem)."""
    _write(
        tmp_path,
        "MyApp/Models/Cart.vb",
        "Namespace MyApp.Models\n    Public Class Cart\n    End Class\nEnd Namespace",
    )
    ctx = _ctx_for(tmp_path)
    resolved = resolve_vbnet_import("MyApp.Models.Cart", "MyApp/Data/Repo.vb", ctx)
    assert resolved == "MyApp/Models/Cart.vb"


def test_dotted_path_suffix_fallback(tmp_path: Path) -> None:
    """Namespace dir chain matches the dotted path suffix when files are
    laid out under the root namespace (the standard SDK-style layout)."""
    _write(
        tmp_path,
        "Acme/Services/Impl.vb",
        "Namespace Acme.Services\n    Public Class Impl\n    End Class\nEnd Namespace",
    )
    ctx = _ctx_for(tmp_path)
    resolved = resolve_vbnet_import("Acme.Services", "Acme/Program.vb", ctx)
    # stem "impl" ≠ "services", but the acme/services dir chain matches
    assert resolved == "Acme/Services/Impl.vb"


def test_system_namespace_registers_external(tmp_path: Path) -> None:
    """System.Collections.Generic has no repo file → external node key."""
    _write(tmp_path, "Data/Repo.vb", "Namespace Data\n    Public Class Repo\n    End Class\nEnd Namespace")
    ctx = _ctx_for(tmp_path)
    resolved = resolve_vbnet_import("System.Collections.Generic", "Data/Repo.vb", ctx)
    assert resolved == "external:System.Collections.Generic"
    assert "external:System.Collections.Generic" in str(list(ctx.graph.nodes))
