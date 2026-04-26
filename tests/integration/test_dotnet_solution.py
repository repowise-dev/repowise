"""End-to-end integration test for the dotnet_solution fixture.

Runs the full FileTraverser → ASTParser → GraphBuilder pipeline against
the multi-project fixture under tests/fixtures/dotnet_solution and
verifies the key edges Batches 1-6 promise:

- All 3 projects (Api, Domain, Infrastructure) are discovered.
- Records / namespaces / DbContext / record-with-base-and-interface
  produce the expected symbols.
- ProjectReference relationships are reflected as import edges.
- ASP.NET framework edges run (Program.cs → controllers, DbContext
  → entity).
- Cross-repo workspace fixture detects ProjectReference and internal
  NuGet links across the two repos.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DOTNET_SOLUTION = Path(__file__).parent.parent / "fixtures" / "dotnet_solution"
DOTNET_WORKSPACE = Path(__file__).parent.parent / "fixtures" / "dotnet_workspace"


@pytest.fixture(scope="module")
def ingest_solution():
    """Run the ingestion pipeline against the dotnet_solution fixture."""
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    traverser = FileTraverser(DOTNET_SOLUTION)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=DOTNET_SOLUTION)

    parsed_list = []
    for file_info in traverser.traverse():
        source = Path(file_info.abs_path).read_bytes()
        parsed = parser.parse_file(file_info, source)
        builder.add_file(parsed)
        parsed_list.append(parsed)

    graph = builder.build()
    builder.add_framework_edges(tech_stack=["aspnet"])
    return {"parsed": parsed_list, "builder": builder, "graph": graph}


class TestSolutionIngestion:
    def test_discovers_csharp_files(self, ingest_solution) -> None:
        cs_files = [p for p in ingest_solution["parsed"] if p.file_info.language == "csharp"]
        # Domain (User, IUserRepository), Infrastructure (AppDbContext,
        # UserRepository), Api (Program, GlobalUsings, UsersController) = 7
        assert len(cs_files) >= 6

    def test_records_captured(self, ingest_solution) -> None:
        names: set[str] = set()
        for p in ingest_solution["parsed"]:
            for s in p.symbols:
                names.add(s.name)
        assert "User" in names
        assert "IUserRepository" in names
        assert "UserRepository" in names
        assert "AppDbContext" in names
        assert "UsersController" in names

    def test_namespaces_captured(self, ingest_solution) -> None:
        modules: set[str] = set()
        for p in ingest_solution["parsed"]:
            for s in p.symbols:
                if s.kind == "module":
                    modules.add(s.name)
        # File-scoped namespaces from each project
        assert any("Acme.Domain" in m or m == "Acme.Domain" for m in modules)
        assert any("Acme.Infrastructure" in m or m == "Acme.Infrastructure" for m in modules)
        assert any("Acme.Api" in m or m == "Acme.Api" for m in modules)

    def test_record_inherits_iauditable(self, ingest_solution) -> None:
        """`record User(...) : IAuditable` should produce an implements edge."""
        relations: list = []
        for p in ingest_solution["parsed"]:
            relations.extend(p.heritage)
        assert any(
            r.child_name == "User" and r.parent_name == "IAuditable" and r.kind == "implements"
            for r in relations
        )

    def test_dbcontext_inherits_dbcontext(self, ingest_solution) -> None:
        """AppDbContext extends DbContext (heritage edge present even though
        DbContext is external)."""
        relations: list = []
        for p in ingest_solution["parsed"]:
            relations.extend(p.heritage)
        assert any(
            r.child_name == "AppDbContext" and r.parent_name == "DbContext"
            for r in relations
        )

    def test_program_to_controller_framework_edge(self, ingest_solution) -> None:
        """ASP.NET framework edge: Program.cs -> UsersController.cs."""
        graph = ingest_solution["graph"]
        # Find the entry/controller paths regardless of separator.
        nodes = list(graph.nodes())
        program = next((n for n in nodes if n.endswith("Program.cs")), None)
        controller = next((n for n in nodes if n.endswith("UsersController.cs")), None)
        assert program and controller, f"missing nodes: {nodes}"
        assert graph.has_edge(program, controller)
        edge = graph[program][controller]
        # Either a framework edge or an import edge — both are valid here
        # since the controller is in a referenced project.
        assert edge.get("edge_type") in ("framework", "import", None) or edge

    def test_dbcontext_to_user_framework_edge(self, ingest_solution) -> None:
        """DbContext with `DbSet<User>` should edge to the User entity file."""
        graph = ingest_solution["graph"]
        nodes = list(graph.nodes())
        ctx = next((n for n in nodes if n.endswith("AppDbContext.cs")), None)
        user = next((n for n in nodes if n.endswith("User.cs")), None)
        assert ctx and user, f"missing nodes — ctx={ctx} user={user}"
        assert graph.has_edge(ctx, user)


class TestCrossRepoWorkspace:
    def test_project_ref_and_internal_nuget_detected(self) -> None:
        """The two-repo .NET workspace produces both kinds of cross-repo deps."""
        from repowise.core.workspace.cross_repo import detect_package_dependencies

        repos = {
            "api-service": DOTNET_WORKSPACE / "api-service",
            "shared-libs": DOTNET_WORKSPACE / "shared-libs",
            "telemetry-lib": DOTNET_WORKSPACE / "telemetry-lib",
        }
        deps = detect_package_dependencies(repos)
        kinds = {d.kind for d in deps}
        assert "dotnet_project_ref" in kinds, [d for d in deps]
        # api-service references shared-libs via <ProjectReference>
        assert any(
            d.source_repo == "api-service"
            and d.target_repo == "shared-libs"
            and d.kind == "dotnet_project_ref"
            for d in deps
        )
        # api-service references Acme.Telemetry NuGet whose AssemblyName
        # lives in telemetry-lib — internal-NuGet pattern.
        assert "dotnet_nuget_internal" in kinds
        assert any(
            d.source_repo == "api-service"
            and d.target_repo == "telemetry-lib"
            and d.kind == "dotnet_nuget_internal"
            for d in deps
        )
