"""Ktor server and Javalin provider contracts.

Both frameworks declare routes as calls nested in lambdas rather than as
annotations, so a Ktor or Javalin service published no endpoints and the calls
the Ktor client reader found stayed unmatched. The claims under test are that
nested prefixes follow lambda scope, that a verb with no path serves the
enclosing path, that a client request or a map lookup stays out, and that the
contracts carry the route-registration origin.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.core.workspace.extractors.http import PROVIDER_DIALECTS, HttpExtractor
from repowise.core.workspace.extractors.http.javalin import JavalinDialect
from repowise.core.workspace.extractors.http.kotlin_clients import KotlinClientsDialect
from repowise.core.workspace.extractors.http.ktor_server import KtorServerDialect
from repowise.core.workspace.origins import ContractOrigin

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "workspace_http"


class _Extractor(HttpExtractor):
    # A class attribute because `source_extensions` is a classmethod.
    provider_dialects = (KtorServerDialect(), JavalinDialect())
    consumer_dialects = (KotlinClientsDialect(),)


def _contracts(repo: Path, role: str) -> dict[str, Any]:
    return {c.contract_id: c for c in _Extractor().extract(repo, "svc") if c.role == role}


def _write(tmp_path: Path, name: str, text: str) -> Path:
    (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


class TestKtorServer:
    def test_nested_routes_compose_their_prefixes(self) -> None:
        ids = _contracts(_FIXTURES / "ktor", "provider")
        assert "http::GET::/api/users/{param}" in ids
        assert "http::GET::/api/status" in ids

    def test_a_verb_with_no_path_serves_the_enclosing_route(self) -> None:
        ids = _contracts(_FIXTURES / "ktor", "provider")
        assert "http::GET::/api/users" in ids
        assert "http::POST::/api/users" in ids
        # Straight under `routing { }`, the root.
        assert "http::GET::/" in ids

    def test_exactly_the_declared_routes(self) -> None:
        ids = _contracts(_FIXTURES / "ktor", "provider")
        # The typed `get<Articles>` and every route in the extension function,
        # whose prefix is its call site, are not among them.
        assert set(ids) == {
            "http::GET::/",
            "http::GET::/api/users",
            "http::GET::/api/users/{param}",
            "http::POST::/api/users",
            "http::GET::/api/status",
            "http::GET::/test-only",
        }

    def test_a_client_call_stays_a_consumer(self) -> None:
        consumers = _contracts(_FIXTURES / "ktor", "consumer")
        assert "http::GET::/api/users" in consumers
        # The test's own `routing { }` serves one route; its client call adds none.
        providers = _contracts(_FIXTURES / "ktor", "provider")
        from_test = {i for i, c in providers.items() if c.file_path == "ApplicationTest.kt"}
        assert from_test == {"http::GET::/test-only"}

    def test_origin_framework_and_line(self) -> None:
        contract = _contracts(_FIXTURES / "ktor", "provider")["http::GET::/api/users/{param}"]
        assert contract.origin == ContractOrigin.ROUTE_REGISTRATION
        assert contract.meta["framework"] == "ktor"
        lines = (_FIXTURES / "ktor" / "Application.kt").read_text(encoding="utf-8").splitlines()
        assert 'get("{id}")' in lines[contract.line - 1]

    def test_a_route_after_a_closed_block_does_not_inherit_its_prefix(self, tmp_path: Path) -> None:
        repo = _write(
            tmp_path,
            "Routes.kt",
            "import io.ktor.server.routing.*\n"
            "fun Application.module() {\n"
            "    routing {\n"
            '        route("/a") { get("/x") { } }\n'
            '        get("/y") { }\n'
            "    }\n"
            "}\n",
        )
        assert set(_contracts(repo, "provider")) == {"http::GET::/a/x", "http::GET::/y"}

    def test_a_prefix_it_cannot_read_refuses_the_routes_below(self, tmp_path: Path) -> None:
        repo = _write(
            tmp_path,
            "Routes.kt",
            "import io.ktor.server.routing.*\n"
            "fun Route.items() {\n"
            "    route(Paths.ITEMS) {\n"
            '        get("{id}") { }\n'
            "    }\n"
            '    // get("/commented") { }\n'
            "}\n",
        )
        assert _contracts(repo, "provider") == {}

    def test_a_verb_outside_routing_is_refused(self, tmp_path: Path) -> None:
        # In an extension function, inside a `route` block or not, the path is
        # served under the call site's prefix.
        repo = _write(
            tmp_path,
            "Routes.kt",
            "import io.ktor.server.routing.*\n"
            "fun Route.items() {\n"
            '    get("/x") { }\n'
            '    route("/p") { get("/y") { } }\n'
            "}\n",
        )
        assert _contracts(repo, "provider") == {}

    def test_install_routing_serves_like_routing(self, tmp_path: Path) -> None:
        repo = _write(
            tmp_path,
            "Routes.kt",
            "import io.ktor.server.routing.*\n"
            "fun Application.module() {\n"
            "    install(Routing) {\n"
            '        get("/x") { }\n'
            "        get { }\n"
            "    }\n"
            "}\n",
        )
        assert set(_contracts(repo, "provider")) == {"http::GET::/x", "http::GET::/"}

    def test_a_file_without_the_routing_package_is_not_read(self, tmp_path: Path) -> None:
        repo = _write(tmp_path, "Other.kt", 'fun f() { get("/x") { } }\n')
        assert _contracts(repo, "provider") == {}


class TestJavalin:
    def test_api_builder_paths_nest_by_scope(self) -> None:
        ids = _contracts(_FIXTURES / "javalin", "provider")
        assert "http::GET::/api/users" in ids
        assert "http::POST::/api/users" in ids
        assert "http::GET::/api/users/{param}" in ids
        assert "http::DELETE::/api/users/{param}" in ids
        # Outside every `path`, a route keeps its own path.
        assert "http::GET::/version" in ids

    def test_app_verbs_chains_and_v7_routes(self) -> None:
        ids = _contracts(_FIXTURES / "javalin", "provider")
        assert "http::GET::/health" in ids  # chained off Javalin.create(...)
        assert "http::POST::/orders" in ids
        assert "http::GET::/orders/{param}" in ids
        assert "http::GET::/v7/ping" in ids
        assert "http::POST::/v7/items" in ids

    def test_kotlin_trailing_lambdas(self) -> None:
        ids = _contracts(_FIXTURES / "javalin", "provider")
        assert "http::GET::/kt" in ids
        assert "http::GET::/kt/items" in ids
        assert "http::PUT::/kt/items/{param}" in ids

    def test_a_lookup_or_a_method_declaration_is_not_a_route(self) -> None:
        ids = _contracts(_FIXTURES / "javalin", "provider")
        assert "http::GET::/orders/cached" not in ids
        assert len(ids) == 13

    def test_origin_and_framework(self) -> None:
        contract = _contracts(_FIXTURES / "javalin", "provider")["http::POST::/orders"]
        assert contract.origin == ContractOrigin.ROUTE_REGISTRATION
        assert contract.meta["framework"] == "javalin"

    def test_an_expression_lambda_verb_is_read(self, tmp_path: Path) -> None:
        # `() -> get(...)`: the arrow before the verb is a call, not a return type.
        repo = _write(
            tmp_path,
            "Routes.java",
            "import static io.javalin.apibuilder.ApiBuilder.*;\n"
            "class Routes {\n"
            '    void routes() { path("/api", () -> get("/x", ctx -> ctx.result("x"))); }\n'
            "    List<String> get(String key) { return null; }\n"
            "}\n",
        )
        assert set(_contracts(repo, "provider")) == {"http::GET::/api/x"}

    def test_a_verb_on_an_unknown_receiver_is_not_a_route(self, tmp_path: Path) -> None:
        repo = _write(
            tmp_path,
            "ClientTest.java",
            "import io.javalin.testtools.JavalinTest;\n"
            "class ClientTest {\n"
            "    void run() {\n"
            '        client.get("/users", req -> req.header("A", "b"));\n'
            '        get("/bare", ctx -> ctx.result("no ApiBuilder import"));\n'
            "    }\n"
            "}\n",
        )
        assert _contracts(repo, "provider") == {}


def test_both_dialects_are_registered() -> None:
    names = {d.name for d in PROVIDER_DIALECTS}
    assert {"ktor-server", "javalin"} <= names
