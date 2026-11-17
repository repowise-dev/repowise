"""A match in a comment is not a contract, for every dialect, checked in one place.

Doc comments show usage as example code. The engine checks a match against the
file's comment mask (built once, and only when something matched): a contract
whose line is nothing but a comment is dropped, and a table row's call in a
comment is never read, so a commented-out decorator cannot set state either.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors import (
    GrpcExtractor,
    HttpExtractor,
    TopicExtractor,
)
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.dialect import run_dialect
from repowise.core.workspace.extractors.http.nestjs import NestDialect


def _http(rel_path: str, content: str) -> list[tuple[str, str, int | None]]:
    suffix = Path(rel_path).suffix
    found = HttpExtractor().extract(Path("."), "svc", files=[(rel_path, suffix, content)])
    return sorted((c.role, c.contract_id, c.line) for c in found)


# The Sock Shop front-end's `helpers/index.js`: a JSDoc usage example that was
# read as a provider and as a consumer of an external host.
SOCK_SHOP_HELPER = """\
var request = require("request");
var helpers = {};

  /* Public: performs an HTTP GET request to the given URL
   *
   * Examples:
   *
   * app.get("/users", function(req, res) {
   *   helpers.simpleHttpRequest("http://api.example.org/users", res, function(err) {
   *     res.send({ error: err });
   *   });
   * });
   */
helpers.simpleHttpRequest = function(url, res, next) {};
module.exports = helpers;
"""


def test_a_jsdoc_example_is_neither_a_provider_nor_a_consumer() -> None:
    assert _http("helpers/index.js", SOCK_SHOP_HELPER) == []


def test_a_line_comment_route_is_dropped_and_the_live_one_kept() -> None:
    content = (
        "const app = express();\n"
        "// app.get('/old', legacy);\n"
        "app.get('/new', handler); // was app.get('/older', h)\n"
    )
    assert _http("server.js", content) == [("provider", "http::GET::/new", 3)]


def test_a_hash_comment_route_in_python_is_dropped() -> None:
    content = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "# @router.post('/disabled')\n"
        "@router.get('/live')\n"
        "def live(): ...\n"
    )
    assert _http("api.py", content) == [("provider", "http::GET::/live", 4)]


def test_a_go_doc_comment_request_is_dropped() -> None:
    content = (
        'import "net/http"\n'
        "// Fetch calls the API, for example:\n"
        '//   req, _ := http.NewRequest("GET", "https://host/v1/items", nil)\n'
        "func Fetch() {\n"
        '\thttp.Get("https://host/v1/orders")\n'
        "}\n"
    )
    assert _http("client.go", content) == [("consumer", "http::GET::/v1/orders", 5)]


def test_a_url_with_a_scheme_is_not_read_as_a_comment() -> None:
    content = 'import "net/http"\nfunc F() { http.Get("http://host/v1/x") }\n'
    assert _http("client.go", content) == [("consumer", "http::GET::/v1/x", 2)]


def test_a_commented_out_controller_does_not_set_the_prefix() -> None:
    # The decorator is skipped where it is matched, so the class takes the
    # live `@Controller`, not the one above it in a comment.
    content = (
        "import { Controller, Get } from '@nestjs/common';\n"
        "// @Controller('legacy')\n"
        "@Controller('orders')\n"
        "export class OrdersController {\n"
        "  @Get(':id')\n"
        "  find() {}\n"
        "}\n"
    )
    ctx = ScanContext("svc", "src/orders.controller.ts", ".ts", content, {"nest-app:": ""})
    found = run_dialect(NestDialect(), ctx)
    assert [c.contract_id for c in found] == ["http::GET::/orders/{param}"]


def test_a_commented_topic_call_is_not_a_contract() -> None:
    content = (
        "@Service\n"
        "class Orders {\n"
        "  /* kafkaTemplate.send(\"orders.legacy\", order); */\n"
        "  void place() { kafkaTemplate.send(\"orders.placed\", order); }\n"
        "}\n"
    )
    found = TopicExtractor().extract(
        Path("."), "svc", files=[("src/Orders.java", ".java", content)]
    )
    assert [c.contract_id for c in found] == ["topic::orders.placed"]


def test_a_commented_grpc_stub_is_not_a_consumer() -> None:
    content = (
        "import grpc\n"
        "# stub = LegacyServiceStub(channel)\n"
        "stub = OrdersServiceStub(channel)\n"
    )
    found = GrpcExtractor().extract(Path("."), "svc", files=[("client.py", ".py", content)])
    assert [c.contract_id for c in found] == ["grpc::OrdersService/*"]


def test_a_rust_attribute_route_is_code_not_a_hash_comment() -> None:
    content = '#[get("/health")]\nasync fn health() -> &\'static str { "ok" }\n'
    assert _http("src/main.rs", content) == [("provider", "http::GET::/health", 1)]


def test_a_python_docstring_example_is_still_read() -> None:
    # Ceiling: masking keeps strings, so an example inside a docstring is code
    # as far as the mask can tell.
    content = (
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        '"""\n'
        "@app.get('/from-docstring')\n"
        '"""\n'
    )
    assert _http("main.py", content) == [("provider", "http::GET::/from-docstring", 4)]
