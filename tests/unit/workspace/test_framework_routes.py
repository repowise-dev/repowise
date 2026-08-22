"""One recognition of a framework's routes, two consumers.

``ingestion.framework_edges`` and ``workspace.extractors.http`` each used to
match ASP.NET's ``.MapGet(...)`` and Express's ``router.get(...)`` with their own
regex, so a fix landed on one side only. The claims under test are that both now
read the same match, that neither consumer's output collapsed into the other's,
and that ``MapGroup`` — which neither copy recognised — now stitches a prefix.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import networkx as nx

from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.framework_routes import (
    HTTP_METHODS,
    aspnet_groups,
    aspnet_routes,
    express_routes,
)
from repowise.core.ingestion.models import FileInfo, Symbol
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.workspace.contracts import bind_symbol_ids
from repowise.core.workspace.extractors.http_extractor import HttpExtractor

from ._repo_index import make_repo_index

# A minimal API written the way the shapes actually co-occur: a nested MapGroup
# chain, a handler named by type and method, and an inline lambda that names
# nothing. Both consumers below read this one text.
PROGRAM_CS = """\
using Microsoft.AspNetCore.Builder;

var app = WebApplication.Create(args);

var api = app.MapGroup("/api");
var orders = api.MapGroup(@"/orders");

orders.MapGet("/{id}", OrderHandlers.GetOrder);
orders.MapPost("", OrderHandlers.CreateOrder);
app.MapDelete($"/tenants/{tenant}/cache", () => Results.NoContent());
app.MapControllers();
"""

HANDLERS_CS = """\
namespace Acme.Api;

public static class OrderHandlers
{
    public static IResult GetOrder(int id) => Results.Ok(id);
    public static IResult CreateOrder(OrderRequest body) => Results.Ok();
}
"""


# ---------------------------------------------------------------------------
# The shared recognition
# ---------------------------------------------------------------------------


class TestAspNetRecognition:
    def test_every_map_shape_is_matched_once(self) -> None:
        routes = list(aspnet_routes(PROGRAM_CS))
        assert [(r.verb, r.path, r.receiver, r.handler) for r in routes] == [
            ("GET", "/{id}", "orders", "OrderHandlers.GetOrder"),
            ("POST", "", "orders", "OrderHandlers.CreateOrder"),
            ("DELETE", "/tenants/{tenant}/cache", "app", None),
        ]

    def test_map_controllers_is_not_a_route(self) -> None:
        # `Map(Get|Post|...)` must not swallow the discovery anchor.
        assert not list(aspnet_routes("app.MapControllers();"))

    def test_verbatim_and_interpolated_paths_are_read(self) -> None:
        # Neither Map matcher this replaced handled either prefix; the handling
        # comes from the C# consumer dialect, which did.
        paths = [r.path for r in aspnet_routes('a.MapGet(@"/v", H); a.MapPut($"/w", H);')]
        assert paths == ["/v", "/w"]

    def test_groups_carry_their_parent(self) -> None:
        assert [(g.var, g.parent, g.prefix) for g in aspnet_groups(PROGRAM_CS)] == [
            ("api", "app", "/api"),
            ("orders", "api", "/orders"),
        ]


class TestExpressRecognition:
    def test_receiver_verb_and_path(self) -> None:
        routes = list(express_routes("router.get('/api/users', auth, listUsers);"))
        assert [(r.verb, r.path, r.receiver) for r in routes] == [
            ("GET", "/api/users", "router")
        ]

    def test_middleware_verbs_are_yielded_but_are_not_http_methods(self) -> None:
        # The graph consumer scans `use`/`all` for handler functions; the
        # contract consumer drops them against HTTP_METHODS.
        verbs = {r.verb for r in express_routes("app.use('/api', router); app.all('/x', h);")}
        assert verbs == {"USE", "ALL"}
        assert not verbs & HTTP_METHODS

    def test_a_decorator_is_not_a_route(self) -> None:
        assert not list(express_routes("@app.get('/items')"))

    def test_a_call_with_no_path_still_reports_its_paren(self) -> None:
        (route,) = list(express_routes("app.use(router);"))
        assert route.path is None
        assert route.paren_offset == len("app.use")


# ---------------------------------------------------------------------------
# Consumer 1 — graph edges
# ---------------------------------------------------------------------------


def _parsed(repo: Path) -> dict[str, Any]:
    parser = ASTParser()
    out: dict[str, Any] = {}
    for cs in repo.rglob("*.cs"):
        rel = cs.resolve().relative_to(repo.resolve()).as_posix()
        fi = FileInfo(
            path=rel,
            abs_path=str(cs.resolve()),
            language="csharp",
            size_bytes=cs.stat().st_size,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=rel.endswith("Program.cs"),
        )
        out[rel] = parser.parse_file(fi, cs.read_bytes())
    return out


class TestGraphConsumer:
    def test_minimal_api_handler_links_the_entry_point_to_its_file(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "Program.cs").write_text(PROGRAM_CS, encoding="utf-8")
        (tmp_path / "OrderHandlers.cs").write_text(HANDLERS_CS, encoding="utf-8")
        parsed = _parsed(tmp_path)
        graph = nx.DiGraph()
        for path in parsed:
            graph.add_node(path)
        ctx = ResolverContext(
            path_set=set(parsed),
            stem_map={Path(p).stem.lower(): [p] for p in parsed},
            graph=graph,
            repo_path=tmp_path,
        )
        add_framework_edges(graph, parsed, ctx, ["aspnet"])
        assert graph.has_edge("Program.cs", "OrderHandlers.cs")


# ---------------------------------------------------------------------------
# Consumer 2 — HTTP contracts
# ---------------------------------------------------------------------------


class TestContractConsumer:
    def _extract(self, tmp_path: Path) -> dict[str, Any]:
        (tmp_path / "Program.cs").write_text(PROGRAM_CS, encoding="utf-8")
        (tmp_path / "OrderHandlers.cs").write_text(HANDLERS_CS, encoding="utf-8")
        return {
            c.contract_id: c
            for c in HttpExtractor().extract(tmp_path, "api")
            if c.role == "provider"
        }

    def test_map_group_prefixes_are_stitched(self, tmp_path: Path) -> None:
        ids = set(self._extract(tmp_path))
        # Without MapGroup these were "/{param}" and an empty path, which the
        # builder drops entirely.
        assert "http::GET::/api/orders/{param}" in ids
        assert "http::POST::/api/orders" in ids

    def test_an_ungrouped_route_keeps_its_literal_path(self, tmp_path: Path) -> None:
        assert "http::DELETE::/tenants/{param}/cache" in self._extract(tmp_path)

    def test_the_handler_is_recorded_for_binding(self, tmp_path: Path) -> None:
        contract = self._extract(tmp_path)["http::GET::/api/orders/{param}"]
        assert contract.meta["handler"] == "OrderHandlers.GetOrder"
        # The graph consumer's output is not folded in: this is a contract, and
        # its file is where the route was declared.
        assert contract.file_path == "Program.cs"


# ---------------------------------------------------------------------------
# What the handler buys: identity
# ---------------------------------------------------------------------------


def _symbol(name: str, path: str, start: int, end: int) -> Symbol:
    return Symbol(
        id=f"{path}::{name}",
        name=name,
        qualified_name=f"OrderHandlers.{name}",
        kind="method",
        signature=f"{name}(int id) -> IResult",
        start_line=start,
        end_line=end,
        docstring=None,
        visibility="public",
    )


class TestHandlerBinding:
    async def test_a_route_binds_to_its_handler_not_its_registration(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "Program.cs").write_text(PROGRAM_CS, encoding="utf-8")
        (tmp_path / "OrderHandlers.cs").write_text(HANDLERS_CS, encoding="utf-8")
        contracts = [
            c for c in HttpExtractor().extract(tmp_path, "api") if c.role == "provider"
        ]
        index = await make_repo_index(
            tmp_path,
            {
                "Program.cs": [_symbol("Main", "Program.cs", 1, 12)],
                "OrderHandlers.cs": [
                    _symbol("GetOrder", "OrderHandlers.cs", 5, 5),
                    _symbol("CreateOrder", "OrderHandlers.cs", 6, 6),
                ],
            },
            alias="api",
        )
        try:
            bind_symbol_ids(contracts, index)
        finally:
            await index.close()
        bound = {c.contract_id: c.symbol_id for c in contracts}
        assert bound["http::GET::/api/orders/{param}"] == "OrderHandlers.cs::GetOrder"
        assert bound["http::POST::/api/orders"] == "OrderHandlers.cs::CreateOrder"
        # The lambda route names no handler, so it falls back to the line lookup.
        assert bound["http::DELETE::/tenants/{param}/cache"] == "Program.cs::Main"

    async def test_an_ambiguous_handler_name_is_refused(self, tmp_path: Path) -> None:
        index = await make_repo_index(
            tmp_path,
            {
                "A.cs": [_symbol("GetOrder", "A.cs", 1, 2)],
                "B.cs": [_symbol("GetOrder", "B.cs", 1, 2)],
            },
            alias="api",
        )
        try:
            assert index.symbol_named("GetOrder") is None
            assert index.symbol_named("Nope") is None
        finally:
            await index.close()
