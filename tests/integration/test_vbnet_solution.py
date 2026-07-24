"""End-to-end integration test for the vbnet_solution fixture.

Runs the full FileTraverser -> ASTParser -> GraphBuilder pipeline against
the multi-project fixture under tests/fixtures/vbnet_solution and
verifies the key VB.NET-specific behaviors documented in
docs/architecture/vbnet-support.md:

- All 3 projects (Api, Domain, Infrastructure) are discovered via .vbproj.
- Classes / interfaces / namespaces produce the expected symbols, with
  RootNamespace prepended even for files with no explicit Namespace block
  (D4).
- Inherits/Implements heritage relations are extracted via the regex
  fallback (D3) despite the grammar's ERROR-node recovery.
- ProjectReference relationships resolve Imports across projects.
- ASP.NET framework edges run (Program.vb -> controller, DbContext ->
  entity), using VB-syntax detectors (D5).
"""

from __future__ import annotations

from pathlib import Path

import pytest

VBNET_SOLUTION = Path(__file__).parent.parent / "fixtures" / "vbnet_solution"


@pytest.fixture(scope="module")
def ingest_solution():
    """Run the ingestion pipeline against the vbnet_solution fixture."""
    from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

    traverser = FileTraverser(VBNET_SOLUTION)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=VBNET_SOLUTION)

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
    def test_discovers_vbnet_files(self, ingest_solution) -> None:
        vb_files = [p for p in ingest_solution["parsed"] if p.file_info.language == "vbnet"]
        # Domain (User, IUserRepository), Infrastructure (AppDbContext,
        # UserRepository), Api (Program, UsersController) = 6
        assert len(vb_files) >= 6

    def test_symbols_captured(self, ingest_solution) -> None:
        names: set[str] = set()
        for p in ingest_solution["parsed"]:
            for s in p.symbols:
                names.add(s.name)
        assert "User" in names
        assert "IUserRepository" in names
        assert "UserRepository" in names
        assert "AppDbContext" in names
        assert "UsersController" in names

    def test_root_namespace_prepended_without_explicit_block(self, ingest_solution) -> None:
        """User.vb and IUserRepository.vb declare no `Namespace` block —
        RootNamespace alone must make `Imports Acme.Domain` resolve to one
        of them (D4). A bare `Imports X` names a namespace, not a specific
        type, so either target is valid evidence the prepending worked —
        only *neither* resolving would mean RootNamespace wasn't applied.
        """
        graph = ingest_solution["graph"]
        nodes = list(graph.nodes())
        controller = next((n for n in nodes if n.endswith("UsersController.vb")), None)
        user = next((n for n in nodes if n.endswith("User.vb")), None)
        repo_iface = next((n for n in nodes if n.endswith("IUserRepository.vb")), None)
        assert controller and user and repo_iface, f"missing nodes: {nodes}"
        assert graph.has_edge(controller, user) or graph.has_edge(controller, repo_iface)

    def test_class_implements_interface(self, ingest_solution) -> None:
        """`Class User ... Implements IAuditable` produces an implements edge."""
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
            r.child_name == "AppDbContext" and r.parent_name == "DbContext" and r.kind == "extends"
            for r in relations
        )

    def test_user_repository_implements_interface(self, ingest_solution) -> None:
        relations: list = []
        for p in ingest_solution["parsed"]:
            relations.extend(p.heritage)
        assert any(
            r.child_name == "UserRepository"
            and r.parent_name == "IUserRepository"
            and r.kind == "implements"
            for r in relations
        )

    def test_program_to_controller_framework_edge(self, ingest_solution) -> None:
        """ASP.NET framework edge: Program.vb -> UsersController.vb."""
        graph = ingest_solution["graph"]
        nodes = list(graph.nodes())
        program = next((n for n in nodes if n.endswith("Program.vb")), None)
        controller = next((n for n in nodes if n.endswith("UsersController.vb")), None)
        assert program and controller, f"missing nodes: {nodes}"
        assert graph.has_edge(program, controller)

    def test_dbcontext_to_user_framework_edge(self, ingest_solution) -> None:
        """DbContext with `DbSet(Of User)` should edge to the User entity file."""
        graph = ingest_solution["graph"]
        nodes = list(graph.nodes())
        ctx = next((n for n in nodes if n.endswith("AppDbContext.vb")), None)
        user = next((n for n in nodes if n.endswith("User.vb")), None)
        assert ctx and user, f"missing nodes — ctx={ctx} user={user}"
        assert graph.has_edge(ctx, user)

    def test_di_registration_resolves(self, ingest_solution) -> None:
        """`AddScoped(Of IUserRepository, UserRepository)()` wires interface
        to implementation via the dynamic-hints extractor (D5)."""
        graph = ingest_solution["graph"]
        nodes = list(graph.nodes())
        program = next((n for n in nodes if n.endswith("Program.vb")), None)
        repo_impl = next((n for n in nodes if n.endswith("UserRepository.vb")), None)
        assert program and repo_impl, f"missing nodes: {nodes}"
        assert graph.has_edge(program, repo_impl)
