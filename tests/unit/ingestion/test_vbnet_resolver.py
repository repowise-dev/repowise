"""Unit tests for the VB.NET import resolver.

``Imports`` directives name namespaces. Resolution goes through the shared
DotNetProjectIndex: namespace map first (RootNamespace aware, ranked by
project), then the type map, then a stem / dotted-path-suffix fallback for
repos with no .vbproj; a miss registers an external node (System.* lands
there). These tests build small repos on disk via ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.vbnet import resolve_vbnet_import


def _ctx_for(repo: Path) -> ResolverContext:
    """Build a ResolverContext rooted at *repo* with all .vb files indexed."""
    # Posix separators: that is what the traverser puts in ``path_set``, so a
    # Windows-native string would never match a resolved candidate.
    vb_files = [p.relative_to(repo).as_posix() for p in repo.rglob("*.vb")]
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


def test_type_import_resolves_via_type_map(tmp_path: Path) -> None:
    """Imports MyApp.Models.Cart names a type, not a namespace."""
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


_VBPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <RootNamespace>{root}</RootNamespace>
  </PropertyGroup>
  <ItemGroup>
{items}  </ItemGroup>
</Project>
"""


def _vbproj(root: str, *, refs: tuple[str, ...] = (), packages: tuple[str, ...] = ()) -> str:
    items = "".join(f'    <ProjectReference Include="{r}" />\n' for r in refs)
    items += "".join(f'    <PackageReference Include="{p}" />\n' for p in packages)
    return _VBPROJ.format(root=root, items=items)


def test_root_namespace_prefixes_declared_namespace(tmp_path: Path) -> None:
    """<RootNamespace>Acme</RootNamespace> plus ``Namespace Data`` is Acme.Data."""
    _write(tmp_path, "Core/Core.vbproj", _vbproj("Acme"))
    _write(
        tmp_path,
        "Core/Data/Repo.vb",
        "Namespace Data\n    Public Class Repo\n    End Class\nEnd Namespace",
    )
    _write(tmp_path, "Core/Program.vb", "Public Module Program\nEnd Module")
    ctx = _ctx_for(tmp_path)
    assert resolve_vbnet_import("Acme.Data", "Core/Program.vb", ctx) == "Core/Data/Repo.vb"


def test_namespace_less_file_lands_in_root_namespace(tmp_path: Path) -> None:
    """A .vb file with no Namespace block belongs to the project's root."""
    _write(tmp_path, "Core/Core.vbproj", _vbproj("Acme"))
    _write(tmp_path, "Core/Widget.vb", "Public Class Widget\nEnd Class")
    _write(tmp_path, "App/App.vbproj", _vbproj("App"))
    _write(tmp_path, "App/Program.vb", "Public Module Program\nEnd Module")
    ctx = _ctx_for(tmp_path)
    assert resolve_vbnet_import("Acme", "App/Program.vb", ctx) == "Core/Widget.vb"


def test_same_project_wins_over_a_referenced_one(tmp_path: Path) -> None:
    """Two projects under one root namespace both declare Acme.Shared.Models,
    so the key really is ambiguous and project rank is what decides it."""
    _write(tmp_path, "Lib/Lib.vbproj", _vbproj("Acme"))
    _write(
        tmp_path,
        "Lib/Models.vb",
        "Namespace Shared.Models\n    Public Class Far\n    End Class\nEnd Namespace",
    )
    _write(tmp_path, "App/App.vbproj", _vbproj("Acme", refs=("../Lib/Lib.vbproj",)))
    _write(
        tmp_path,
        "App/Models.vb",
        "Namespace Shared.Models\n    Public Class Near\n    End Class\nEnd Namespace",
    )
    _write(tmp_path, "App/Program.vb", "Public Module Program\nEnd Module")
    ctx = _ctx_for(tmp_path)
    resolved = resolve_vbnet_import("Acme.Shared.Models", "App/Program.vb", ctx)
    assert resolved == "App/Models.vb"


def test_declared_namespace_is_not_reachable_without_the_root(tmp_path: Path) -> None:
    """``Namespace Shared.Models`` inside RootNamespace Lib is only reachable
    as ``Lib.Shared.Models``. An unrelated project importing the bare name
    must get no edge rather than a wrong one."""
    _write(tmp_path, "Lib/Lib.vbproj", _vbproj("Lib"))
    _write(
        tmp_path,
        "Lib/Models.vb",
        "Namespace Shared.Models\n    Public Class Far\n    End Class\nEnd Namespace",
    )
    _write(tmp_path, "App/App.vbproj", _vbproj("App"))
    _write(tmp_path, "App/Program.vb", "Public Module Program\nEnd Module")
    ctx = _ctx_for(tmp_path)
    resolved = resolve_vbnet_import("Shared.Models", "App/Program.vb", ctx)
    assert resolved == "external:Shared.Models"
    assert resolve_vbnet_import("Lib.Shared.Models", "App/Program.vb", ctx) == "Lib/Models.vb"


def test_global_namespace_declaration_escapes_the_root(tmp_path: Path) -> None:
    """``Namespace Global.Shared.Models`` opts out of RootNamespace, so the
    bare name is the one that resolves and the prefixed one does not."""
    _write(tmp_path, "Lib/Lib.vbproj", _vbproj("Lib"))
    _write(
        tmp_path,
        "Lib/Models.vb",
        "Namespace Global.Shared.Models\n    Public Class Far\n    End Class\nEnd Namespace",
    )
    _write(tmp_path, "App/App.vbproj", _vbproj("App"))
    _write(tmp_path, "App/Program.vb", "Public Module Program\nEnd Module")
    ctx = _ctx_for(tmp_path)
    assert resolve_vbnet_import("Shared.Models", "App/Program.vb", ctx) == "Lib/Models.vb"


def test_package_reference_becomes_a_nuget_node(tmp_path: Path) -> None:
    """An Imports covered by a PackageReference is external NuGet, not a miss."""
    _write(tmp_path, "App/App.vbproj", _vbproj("App", packages=("Newtonsoft.Json",)))
    _write(tmp_path, "App/Program.vb", "Public Module Program\nEnd Module")
    ctx = _ctx_for(tmp_path)
    resolved = resolve_vbnet_import("Newtonsoft.Json.Linq", "App/Program.vb", ctx)
    assert resolved == "external:nuget:Newtonsoft.Json.Linq"


def test_global_prefix_is_stripped(tmp_path: Path) -> None:
    """Imports Global.MyApp.Models is the same target as Imports MyApp.Models."""
    _write(
        tmp_path,
        "MyApp/Models/Customer.vb",
        "Namespace MyApp.Models\n    Public Class Customer\n    End Class\nEnd Namespace",
    )
    ctx = _ctx_for(tmp_path)
    resolved = resolve_vbnet_import("Global.MyApp.Models", "MyApp/Data/Repo.vb", ctx)
    assert resolved == "MyApp/Models/Customer.vb"
