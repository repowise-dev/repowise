"""Unit tests for ASP.NET framework edges and .NET dynamic hints.

These tests cover Batch 4 of the C# Full-tier upgrade:

- ``_add_aspnet_edges`` in ``framework_edges.py`` — Program.cs/Startup.cs
  to controllers, minimal-API handler resolution, UseMiddleware<T>(),
  and DbContext → DbSet<T> entity wiring.
- ``DotNetDynamicHints`` in ``dynamic_hints/dotnet.py`` — DI container
  registrations, reflection, and InternalsVisibleTo discovery.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.dynamic_hints.dotnet import DotNetDynamicHints
from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.models import FileInfo, ParsedFile, Symbol
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext


# ---------------------------------------------------------------------------
# Framework edges
# ---------------------------------------------------------------------------


def _file_info(path: str, abs_path: str, language: str = "csharp") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=abs_path,
        language=language,
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=path.endswith("Program.cs"),
    )


def _build_parsed_files(repo: Path) -> dict[str, ParsedFile]:
    """Parse every .cs file under *repo* and return a {rel_path: ParsedFile} map."""
    parser = ASTParser()
    out: dict[str, ParsedFile] = {}
    for cs in repo.rglob("*.cs"):
        rel = cs.resolve().relative_to(repo.resolve()).as_posix()
        fi = _file_info(rel, str(cs.resolve()))
        result = parser.parse_file(fi, cs.read_bytes())
        out[rel] = result
    return out


def _ctx(repo: Path, parsed: dict[str, ParsedFile]) -> ResolverContext:
    path_set = set(parsed.keys())
    stem_map: dict[str, list[str]] = {}
    for p in path_set:
        stem = Path(p).stem.lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=path_set, stem_map=stem_map, graph=nx.DiGraph(), repo_path=repo
    )


class TestAspNetEdges:
    def test_program_to_controllers(self, tmp_path: Path) -> None:
        (tmp_path / "Program.cs").write_text(
            """using Microsoft.AspNetCore.Builder;
var app = WebApplication.CreateBuilder(args).Build();
app.MapControllers();
app.Run();
"""
        )
        (tmp_path / "Controllers").mkdir()
        (tmp_path / "Controllers" / "UsersController.cs").write_text(
            """using Microsoft.AspNetCore.Mvc;
namespace Acme.Api;
[ApiController]
[Route("api/users")]
public class UsersController : ControllerBase {
    [HttpGet] public IActionResult Get() => Ok();
}
"""
        )
        parsed = _build_parsed_files(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)

        count = add_framework_edges(graph, parsed, ctx, tech_stack=["aspnet"])
        assert count >= 1
        assert graph.has_edge("Program.cs", "Controllers/UsersController.cs")
        edge = graph["Program.cs"]["Controllers/UsersController.cs"]
        assert edge["edge_type"] == "framework"

    def test_dbcontext_to_dbset_entity(self, tmp_path: Path) -> None:
        (tmp_path / "Domain").mkdir()
        (tmp_path / "Infra").mkdir()
        (tmp_path / "Domain" / "User.cs").write_text(
            "namespace Acme.Domain;\npublic class User { public int Id; }\n"
        )
        (tmp_path / "Infra" / "AppDbContext.cs").write_text(
            """using Microsoft.EntityFrameworkCore;
namespace Acme.Infra;
public class AppDbContext : DbContext {
    public DbSet<User> Users { get; set; }
}
"""
        )
        # Need a Program.cs or any ASP.NET signal to enable the path; use the
        # Microsoft.EntityFrameworkCore using directive as the fallback signal.
        (tmp_path / "Program.cs").write_text(
            "using Microsoft.AspNetCore.Builder;\nvar app = 1;\n"
        )
        parsed = _build_parsed_files(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)

        add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert graph.has_edge("Infra/AppDbContext.cs", "Domain/User.cs")

    def test_no_edges_when_no_aspnet_signal(self, tmp_path: Path) -> None:
        (tmp_path / "Plain.cs").write_text(
            "namespace Plain;\npublic class Foo {}\n"
        )
        parsed = _build_parsed_files(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)

        count = add_framework_edges(graph, parsed, ctx, tech_stack=[])
        # Conftest detection runs unconditionally but should produce 0
        # for a single C# file with no test conventions.
        assert count == 0


# ---------------------------------------------------------------------------
# Dynamic hints
# ---------------------------------------------------------------------------


class TestDotNetDynamicHints:
    def test_di_registration_emits_interface_to_impl(self, tmp_path: Path) -> None:
        (tmp_path / "IUserService.cs").write_text(
            "namespace Acme;\npublic interface IUserService { }\n"
        )
        (tmp_path / "UserService.cs").write_text(
            "namespace Acme;\npublic class UserService : IUserService { }\n"
        )
        (tmp_path / "Program.cs").write_text(
            """var builder = WebApplication.CreateBuilder(args);
builder.Services.AddScoped<IUserService, UserService>();
"""
        )
        edges = DotNetDynamicHints().extract(tmp_path)
        targets = {(e.source, e.target) for e in edges}
        # Program.cs registers it (registration site -> impl)
        assert ("Program.cs", "UserService.cs") in targets
        # Interface file is recorded as having a real implementation
        assert ("IUserService.cs", "UserService.cs") in targets

    def test_activator_create_instance_typeof(self, tmp_path: Path) -> None:
        (tmp_path / "Plugin.cs").write_text(
            "namespace Acme;\npublic class Plugin { }\n"
        )
        (tmp_path / "Loader.cs").write_text(
            "namespace Acme;\npublic class Loader { void Load() { var p = Activator.CreateInstance(typeof(Plugin)); } }\n"
        )
        edges = DotNetDynamicHints().extract(tmp_path)
        assert any(e.source == "Loader.cs" and e.target == "Plugin.cs" for e in edges)

    def test_type_gettype_string_form(self, tmp_path: Path) -> None:
        (tmp_path / "Worker.cs").write_text(
            "namespace Acme;\npublic class Worker { }\n"
        )
        (tmp_path / "Boot.cs").write_text(
            'namespace Acme;\npublic class Boot { void Go() { var t = Type.GetType("Acme.Worker"); } }\n'
        )
        edges = DotNetDynamicHints().extract(tmp_path)
        assert any(e.source == "Boot.cs" and e.target == "Worker.cs" for e in edges)

    def test_internals_visible_to_emits_friend_edge(self, tmp_path: Path) -> None:
        (tmp_path / "AssemblyInfo.cs").write_text(
            '[assembly: InternalsVisibleTo("Acme.Tests")]\n'
        )
        edges = DotNetDynamicHints().extract(tmp_path)
        assert any(
            e.source == "AssemblyInfo.cs" and e.target == "external:friend:Acme.Tests"
            for e in edges
        )

    def test_skips_bin_obj_directories(self, tmp_path: Path) -> None:
        (tmp_path / "obj" / "Debug").mkdir(parents=True)
        (tmp_path / "obj" / "Debug" / "Generated.cs").write_text(
            'public class Junk { void X() { Activator.CreateInstance(typeof(Plugin)); } }\n'
        )
        (tmp_path / "Plugin.cs").write_text("public class Plugin { }\n")
        edges = DotNetDynamicHints().extract(tmp_path)
        # No edge should originate from obj/...
        assert not any("obj/" in e.source for e in edges)
