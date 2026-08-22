"""One recognition of a framework's routes, two consumers.

``ingestion.framework_edges`` and ``workspace.extractors.http`` each used to
match ASP.NET's ``.MapGet(...)``, Express's ``router.get(...)``, gin's
``r.GET(...)``, Laravel's ``Route::get(...)`` and axum's ``.route(...)`` with
their own regex, so a fix landed on one side only. The claims under test are
that both now read the same match, that neither consumer's output collapsed into
the other's, and that the shapes neither copy handled — ``MapGroup`` prefixes, a
multi-line axum route, Laravel's third handler spelling — now work.
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
    axum_routes,
    express_routes,
    go_groups,
    go_routes,
    laravel_routes,
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


def _parse_repo(repo: Path, glob: str, language: str) -> dict[str, Any]:
    parser = ASTParser()
    out: dict[str, Any] = {}
    for src in repo.rglob(glob):
        rel = src.resolve().relative_to(repo.resolve()).as_posix()
        fi = FileInfo(
            path=rel,
            abs_path=str(src.resolve()),
            language=language,
            size_bytes=src.stat().st_size,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=rel.endswith("Program.cs"),
        )
        out[rel] = parser.parse_file(fi, src.read_bytes())
    return out


def _parsed(repo: Path) -> dict[str, Any]:
    return _parse_repo(repo, "*.cs", "csharp")


def _graph_edges(repo: Path, parsed: dict[str, Any], stack: list[str]) -> nx.DiGraph:
    graph = nx.DiGraph()
    for path in parsed:
        graph.add_node(path)
    ctx = ResolverContext(
        path_set=set(parsed),
        stem_map={Path(p).stem.lower(): [p] for p in parsed},
        graph=graph,
        repo_path=repo,
    )
    add_framework_edges(graph, parsed, ctx, stack)
    return graph


def _providers(repo: Path) -> dict[str, Any]:
    return {
        c.contract_id: c
        for c in HttpExtractor().extract(repo, "api")
        if c.role == "provider"
    }


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

    def test_a_lambda_parameter_is_not_a_handler(self, tmp_path: Path) -> None:
        # `async ctx =>` used to yield handler="async", which the graph side only
        # failed to resolve but the contract side would persist and bind by.
        (tmp_path / "Program.cs").write_text(
            'app.MapPut("/z", async ctx => await Do(ctx));\n'
            'app.MapPatch("/y", ctx => Handle(ctx));\n'
            'app.MapGet("/x", Handlers.Get);\n',
            encoding="utf-8",
        )
        handlers = {
            c.contract_id: c.meta.get("handler")
            for c in HttpExtractor().extract(tmp_path, "api")
            if c.role == "provider"
        }
        assert handlers == {
            "http::PUT::/z": None,
            "http::PATCH::/y": None,
            "http::GET::/x": "Handlers.Get",
        }

    def test_a_single_quoted_path_is_still_read(self, tmp_path: Path) -> None:
        # Not valid C#, but both matchers this replaced accepted it, and the
        # gate for this phase is that no framework loses a contract.
        (tmp_path / "Program.cs").write_text("app.MapGet('/legacy', H);\n", encoding="utf-8")
        ids = {c.contract_id for c in HttpExtractor().extract(tmp_path, "api")}
        assert "http::GET::/legacy" in ids


# ---------------------------------------------------------------------------
# What the handler buys: identity
# ---------------------------------------------------------------------------


async def _index_of_repo(tmp_path: Path, glob: str, language: str) -> Any:
    """Open an index over the rows real ingestion produces for the fixtures.

    The symbol ids are never written by hand. C# ingestion mints
    ``OrderHandlers.cs::OrderHandlers::GetOrder`` — class-qualified, not the
    ``<file>::<name>`` shape a fixture would guess — and `Program.cs` is
    top-level statements, so it yields no symbols at all.
    """
    parsed = _parse_repo(tmp_path, glob, language)
    by_file: dict[str, list[Symbol]] = {}
    for rel, pf in parsed.items():
        for sym in pf.symbols:
            sym.file_path = rel
        by_file[rel] = list(pf.symbols)
    return await make_repo_index(tmp_path, by_file, alias="api")


class TestHandlerBinding:
    async def test_a_route_binds_to_its_handler_not_its_registration(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "Program.cs").write_text(PROGRAM_CS, encoding="utf-8")
        (tmp_path / "OrderHandlers.cs").write_text(HANDLERS_CS, encoding="utf-8")
        contracts = [
            c for c in HttpExtractor().extract(tmp_path, "api") if c.role == "provider"
        ]
        index = await _index_of_repo(tmp_path, "*.cs", "csharp")
        try:
            # The expected ids come from the parse, not from this file.
            expected = {
                s.name: s.symbol_id
                for s in index.symbols_for_file("OrderHandlers.cs")
                if s.kind == "method"
            }
            assert set(expected) == {"GetOrder", "CreateOrder"}
            bind_symbol_ids(contracts, index)
        finally:
            await index.close()
        bound = {c.contract_id: c.symbol_id for c in contracts}
        assert bound["http::GET::/api/orders/{param}"] == expected["GetOrder"]
        assert bound["http::POST::/api/orders"] == expected["CreateOrder"]
        # The lambda route names no handler and Program.cs has no symbols to fall
        # back to, so it stays unbound — as it did before this change.
        assert bound["http::DELETE::/tenants/{param}/cache"] is None

    async def test_an_ambiguous_handler_is_refused_end_to_end(self, tmp_path: Path) -> None:
        (tmp_path / "Program.cs").write_text(
            'app.MapGet("/a", Handle);\napp.MapGet("/b", Two.Handle);\n', encoding="utf-8"
        )
        for name in ("One", "Two"):
            (tmp_path / f"{name}.cs").write_text(
                f"public static class {name}\n{{\n"
                "    public static IResult Handle() => Results.Ok();\n}\n",
                encoding="utf-8",
            )
        contracts = [
            c for c in HttpExtractor().extract(tmp_path, "api") if c.role == "provider"
        ]
        index = await _index_of_repo(tmp_path, "*.cs", "csharp")
        try:
            bind_symbol_ids(contracts, index)
            two = index.symbol_named("Two.Handle")
            one = index.symbol_named("One.Handle")
        finally:
            await index.close()
        # Both really are in the index, so the refusal below is not vacuous.
        assert one is not None and two is not None and one.symbol_id != two.symbol_id
        bound = {c.contract_id: c.symbol_id for c in contracts}
        # Bare `Handle` names two symbols, so it binds to neither.
        assert bound["http::GET::/a"] is None
        # `Two.Handle` carries the qualifier that settles it.
        assert two is not None
        assert bound["http::GET::/b"] == two.symbol_id


# ===========================================================================
# W5b — go, laravel and axum, the three remaining one-construct duplicates
# ===========================================================================

# A gin router written the way the shapes co-occur: nested groups, a handler
# named bare, one named through a package, and a closure that names nothing.
ROUTES_GO = """\
package main

import "github.com/gin-gonic/gin"

func Setup(r *gin.Engine) {
	api := r.Group("/api")
	v1 := api.Group("/v1")
	v1.GET("/orders/:id", GetOrder)
	v1.POST("/orders", CreateOrder)
	r.GET("/ping", func(c *gin.Context) { c.String(200, "pong") })
	r.HandleFunc("/health", Health)
	r.Any("/catchall", CatchAll)
}
"""

HANDLERS_GO = """\
package main

import "github.com/gin-gonic/gin"

func GetOrder(c *gin.Context)  {}
func CreateOrder(c *gin.Context) {}
func Health(c *gin.Context)    {}
func CatchAll(c *gin.Context)  {}
"""

ROUTES_PHP = """\
<?php

Route::get('/orders/{id}', [OrderController::class, 'show']);
Route::post('/orders', 'OrderController@store');
Route::resource('photos', PhotoController::class);
Route::get('/ping', function () { return 'pong'; });
"""

MAIN_RS = """\
use axum::{routing::get, Router};

pub fn app() -> Router {
    Router::new()
        .route("/orders", get(list_orders).post(create_order))
        .route(
            "/orders/:id",
            get(show_order).delete(remove_order),
        )
        .route("/ping", get(|| async { "pong" }))
}
"""

HANDLERS_RS = """\
pub async fn list_orders() {}
pub async fn create_order() {}
pub async fn show_order() {}
pub async fn remove_order() {}
"""


class TestGoRecognition:
    def test_receiver_verb_path_and_handler(self) -> None:
        routes = [(r.verb, r.path, r.receiver, r.handler) for r in go_routes(ROUTES_GO)]
        assert routes == [
            ("GET", "/orders/:id", "v1", "GetOrder"),
            ("POST", "/orders", "v1", "CreateOrder"),
            ("GET", "/ping", "r", None),
            ("HANDLEFUNC", "/health", "r", "Health"),
            ("ANY", "/catchall", "r", "CatchAll"),
        ]

    def test_a_closure_is_not_a_handler(self) -> None:
        # `func` is the only spelling of an inline closure, so excluding that one
        # keyword is what stops the token `func` being read as a handler name.
        (route,) = list(go_routes('r.GET("/x", func(c *gin.Context) {})'))
        assert route.handler is None

    def test_a_wrapped_handler_is_kept_but_flagged(self) -> None:
        # Middleware wrappers are the dominant real shape in mattermost/grafana.
        # The graph consumer wants the edge; the contract consumer must not bind
        # a route to its middleware.
        (route,) = list(go_routes('r.GET("/x", api.RequireSession(handleUser))'))
        assert (route.handler, route.handler_call) == ("api.RequireSession", True)
        (plain,) = list(go_routes('r.GET("/y", handleUser)'))
        assert (plain.handler, plain.handler_call) == ("handleUser", False)

    def test_groups_carry_their_parent(self) -> None:
        assert [(g.var, g.parent, g.prefix) for g in go_groups(ROUTES_GO)] == [
            ("api", "r", "/api"),
            ("v1", "api", "/v1"),
        ]


class TestLaravelRecognition:
    def test_all_three_handler_spellings(self) -> None:
        routes = [(r.verb, r.path, r.handler) for r in laravel_routes(ROUTES_PHP)]
        assert routes == [
            ("GET", "/orders/{id}", "OrderController"),
            ("POST", "/orders", "OrderController"),
            ("RESOURCE", "photos", "PhotoController"),
            ("GET", "/ping", None),
        ]

    def test_a_namespaced_controller_keeps_its_namespace(self) -> None:
        (route,) = list(
            laravel_routes(r"Route::get('/x', [App\Http\Controllers\Orders::class, 'i']);")
        )
        assert route.handler == r"App\Http\Controllers\Orders"

    def test_a_concatenated_path_with_a_call_in_it(self) -> None:
        # Real monica route: scanning to the next `)` stops inside `config(`, and
        # scanning to the next `,` runs past the call. Only the balanced argument
        # span reads both parts.
        (route,) = list(
            laravel_routes(
                "Route::post(\n    '/telegram/webhook/'.config('x.webhook'),\n"
                "    [TelegramWebhookController::class, 'store']\n);"
            )
        )
        assert (route.path, route.handler) == (
            "/telegram/webhook/",
            "TelegramWebhookController",
        )

    def test_a_chained_name_call_is_outside_the_arguments(self) -> None:
        (route,) = list(
            laravel_routes("Route::get('/x', [C::class, 'i'])->name('login.provider');")
        )
        assert (route.path, route.handler) == ("/x", "C")

    def test_a_non_literal_path_still_yields_its_handler(self) -> None:
        # The graph regexes matched `[^,]*` here; requiring a literal would have
        # dropped the edge.
        (route,) = list(laravel_routes("Route::put($uri, [EditController::class, 'e']);"))
        assert (route.path, route.handler) == (None, "EditController")

    def test_resource_is_yielded_but_is_not_an_http_method(self) -> None:
        verbs = {r.verb for r in laravel_routes(ROUTES_PHP)}
        assert "RESOURCE" in verbs and not {"RESOURCE"} & HTTP_METHODS


class TestAxumRecognition:
    def test_chained_verbs_share_one_path_and_offset(self) -> None:
        routes = [(r.verb, r.path, r.handler, r.offset) for r in axum_routes(MAIN_RS)]
        assert [(v, p, h) for v, p, h, _ in routes] == [
            ("GET", "/orders", "list_orders"),
            ("POST", "/orders", "create_order"),
            ("GET", "/orders/:id", "show_order"),
            ("DELETE", "/orders/:id", "remove_order"),
            ("GET", "/ping", None),
        ]
        # The two verbs of one call site report the same registration offset.
        assert routes[0][3] == routes[1][3]
        assert routes[2][3] == routes[3][3]

    def test_a_multi_line_route_is_read(self) -> None:
        # The contract matcher this replaced scanned only to end of line, so the
        # verbs of a wrapped `.route(` were invisible to it.
        verbs = [r.verb for r in axum_routes(MAIN_RS) if r.path == "/orders/:id"]
        assert verbs == ["GET", "DELETE"]

    def test_on_names_no_literal_verb(self) -> None:
        (route,) = list(axum_routes('.route("/x", on(MethodFilter::GET, custom))'))
        assert (route.verb, route.handler) == ("ON", None)

    def test_a_lifetime_does_not_open_a_string(self) -> None:
        # `'` is a Rust lifetime; a quote-aware paren scan would swallow the file.
        src = '.route("/x", get(handler::<\'a>))\n.route("/y", post(other))'
        assert [r.path for r in axum_routes(src)] == ["/x", "/y"]


class TestGoConsumers:
    def _write(self, tmp_path: Path) -> None:
        (tmp_path / "routes.go").write_text(ROUTES_GO, encoding="utf-8")
        (tmp_path / "handlers.go").write_text(HANDLERS_GO, encoding="utf-8")

    def test_graph_links_the_router_to_its_handler_file(self, tmp_path: Path) -> None:
        self._write(tmp_path)
        graph = _graph_edges(tmp_path, _parse_repo(tmp_path, "*.go", "go"), ["gin"])
        assert graph.has_edge("routes.go", "handlers.go")

    def test_contracts_keep_their_group_prefixes(self, tmp_path: Path) -> None:
        self._write(tmp_path)
        ids = set(_providers(tmp_path))
        assert "http::GET::/api/v1/orders/{param}" in ids
        assert "http::POST::/api/v1/orders" in ids

    def test_handlefunc_records_no_verb_and_any_records_nothing(
        self, tmp_path: Path
    ) -> None:
        self._write(tmp_path)
        ids = set(_providers(tmp_path))
        assert "http::*::/health" in ids
        # gin's `Any` reaches the graph consumer but is not an HTTP method.
        assert not [i for i in ids if "catchall" in i]

    async def test_a_route_binds_to_its_handler(self, tmp_path: Path) -> None:
        self._write(tmp_path)
        contracts = [
            c for c in HttpExtractor().extract(tmp_path, "api") if c.role == "provider"
        ]
        index = await _index_of_repo(tmp_path, "*.go", "go")
        try:
            expected = {
                s.name: s.symbol_id for s in index.symbols_for_file("handlers.go")
            }
            assert "GetOrder" in expected
            bind_symbol_ids(contracts, index)
        finally:
            await index.close()
        bound = {c.contract_id: c.symbol_id for c in contracts}
        assert bound["http::GET::/api/v1/orders/{param}"] == expected["GetOrder"]


class TestLaravelConsumers:
    def _write(self, tmp_path: Path) -> None:
        (tmp_path / "routes").mkdir()
        (tmp_path / "routes" / "api.php").write_text(ROUTES_PHP, encoding="utf-8")
        controllers = tmp_path / "app" / "Http" / "Controllers"
        controllers.mkdir(parents=True)
        for name in ("OrderController", "PhotoController"):
            (controllers / f"{name}.php").write_text(
                f"<?php\nclass {name} {{ public function show() {{}} }}\n", encoding="utf-8"
            )

    def test_graph_links_the_route_file_to_every_controller(self, tmp_path: Path) -> None:
        self._write(tmp_path)
        graph = _graph_edges(tmp_path, _parse_repo(tmp_path, "*.php", "php"), ["laravel"])
        assert graph.has_edge("routes/api.php", "app/Http/Controllers/OrderController.php")
        # The bare `Controller::class` of a resource route is a third spelling
        # that the one shared alternation now covers.
        assert graph.has_edge("routes/api.php", "app/Http/Controllers/PhotoController.php")

    def test_contracts_cover_the_verbs_and_nothing_else(self, tmp_path: Path) -> None:
        self._write(tmp_path)
        ids = set(_providers(tmp_path))
        assert "http::GET::/orders/{param}" in ids
        assert "http::POST::/orders" in ids
        assert "http::GET::/ping" in ids
        # `resource` stands for a set of routes, so it is not one contract.
        assert not [i for i in ids if "photos" in i]


class TestAxumConsumers:
    def _write(self, tmp_path: Path) -> None:
        (tmp_path / "main.rs").write_text(MAIN_RS, encoding="utf-8")
        (tmp_path / "handlers.rs").write_text(HANDLERS_RS, encoding="utf-8")

    def test_graph_links_every_chained_handler(self, tmp_path: Path) -> None:
        self._write(tmp_path)
        graph = _graph_edges(tmp_path, _parse_repo(tmp_path, "*.rs", "rust"), ["axum"])
        assert graph.has_edge("main.rs", "handlers.rs")

    def test_contracts_cover_every_chained_verb(self, tmp_path: Path) -> None:
        self._write(tmp_path)
        ids = set(_providers(tmp_path))
        assert {
            "http::GET::/orders",
            "http::POST::/orders",
            "http::GET::/orders/{param}",
            "http::DELETE::/orders/{param}",
            "http::GET::/ping",
        } <= ids

    async def test_a_route_binds_to_its_handler(self, tmp_path: Path) -> None:
        self._write(tmp_path)
        contracts = [
            c for c in HttpExtractor().extract(tmp_path, "api") if c.role == "provider"
        ]
        index = await _index_of_repo(tmp_path, "*.rs", "rust")
        try:
            expected = {
                s.name: s.symbol_id for s in index.symbols_for_file("handlers.rs")
            }
            assert "list_orders" in expected
            bind_symbol_ids(contracts, index)
        finally:
            await index.close()
        bound = {c.contract_id: c.symbol_id for c in contracts}
        assert bound["http::GET::/orders"] == expected["list_orders"]
