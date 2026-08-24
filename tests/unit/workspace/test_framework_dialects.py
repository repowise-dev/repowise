"""Provider dialects for the frameworks the graph layer already recognised.

Django, JAX-RS, the Next.js App Router and the Hono/Fastify/Koa router DSL had
graph edges and no contract, so cross-repo links, breaking-change detection and
schema recovery had nothing to attach to on a repo built with any of them. The
claims under test are that each now produces a contract with the path its
framework actually serves, that the two consumers read one declaration, and that
the shapes only a real repo carries -- an ``include()`` prefix, a class-level
``@Path`` under a dotted annotation, a route group, a Hono app -- are handled.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import networkx as nx

from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.framework_routes import (
    django_includes,
    django_routes,
    jaxrs_class_paths,
    jaxrs_routes,
    js_router_bindings,
    next_route_path,
    next_route_verbs,
)
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.workspace.contracts import bind_symbol_ids
from repowise.core.workspace.extractors.http_extractor import HttpExtractor

from ._repo_index import make_repo_index

ROOT_URLS_PY = """\
from django.urls import include, path, re_path
from . import views
from myapp.views import DetailView

urlpatterns = [
    path("healthz/", views.healthz),
    path("orders/<int:pk>/", DetailView.as_view()),
    re_path(r"^legacy/(?P<slug>[\\w-]+)/$", views.legacy),
    path("api/", include("myapp.urls")),
]
"""

APP_URLS_PY = """\
from django.urls import path
from . import views

urlpatterns = [
    path("items/", views.items),
]
"""

VIEWS_PY = """\
def healthz(request):
    return None


def items(request):
    return None


def legacy(request, slug):
    return None
"""

ACCOUNT_RESOURCE_JAVA = """\
package com.acme;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.PUT;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;

@Path("/v2/accounts")
@io.swagger.v3.oas.annotations.tags.Tag(name = "Account")
public class AccountResource {

  @GET
  @Path("/data_report")
  @Produces(MediaType.APPLICATION_JSON)
  public Report getReport() {
    return null;
  }

  @PUT
  @Path("/{id}/number")
  public Response changeNumber(@PathParam("id") String id) {
    return null;
  }

  @GET
  public Response list() {
    return null;
  }
}
"""

HONO_APP_TS = """\
import { Hono } from 'hono';
import { listUsers } from './handlers';

const api = new Hono();
api.get('/users', listUsers);
api.post('/users', listUsers);

const legacy = express.Router();
legacy.get('/ping', listUsers);
"""

NEXT_ROUTE_TS = """\
export async function GET(request: Request) {
  return Response.json({});
}

export async function POST(request: Request) {
  return Response.json({});
}
"""


def _providers(repo: Path, alias: str = "api") -> dict[str, Any]:
    return {
        c.contract_id: c
        for c in HttpExtractor().extract(repo, alias)
        if c.role == "provider"
    }


# ---------------------------------------------------------------------------
# Django
# ---------------------------------------------------------------------------


class TestDjangoRecognition:
    def test_every_urlconf_shape_is_read(self) -> None:
        routes = [(r.verb, r.path, r.handler) for r in django_routes(ROOT_URLS_PY)]
        assert routes == [
            ("*", "healthz/", "views.healthz"),
            ("*", "orders/{pk}/", "DetailView.as_view"),
            ("*", "legacy/{slug}/", "views.legacy"),
        ]

    def test_an_include_is_not_a_route(self) -> None:
        assert list(django_includes(ROOT_URLS_PY)) == [("api/", "myapp.urls")]

    def test_a_wrapped_view_is_marked_as_a_call(self) -> None:
        # `DetailView.as_view()` is invoked, so the contract must not bind to it.
        (wrapped,) = [r for r in django_routes(ROOT_URLS_PY) if r.path.startswith("orders")]
        assert wrapped.handler_call is True

    def test_a_dotted_attribute_is_not_a_urlconf_entry(self) -> None:
        assert not list(django_routes('shutil.path("x", y)'))


class TestDjangoContracts:
    def _extract(self, tmp_path: Path) -> dict[str, Any]:
        (tmp_path / "urls.py").write_text(ROOT_URLS_PY, encoding="utf-8")
        (tmp_path / "myapp").mkdir()
        (tmp_path / "myapp" / "urls.py").write_text(APP_URLS_PY, encoding="utf-8")
        (tmp_path / "myapp" / "views.py").write_text(VIEWS_PY, encoding="utf-8")
        return _providers(tmp_path)

    def test_a_urlconf_entry_becomes_a_verbless_contract(self, tmp_path: Path) -> None:
        ids = self._extract(tmp_path)
        assert "http::*::/healthz" in ids
        assert ids["http::*::/healthz"].meta["framework"] == "django"

    def test_a_converter_and_a_named_group_normalize_alike(self, tmp_path: Path) -> None:
        ids = set(self._extract(tmp_path))
        assert "http::*::/orders/{param}" in ids
        assert "http::*::/legacy/{param}" in ids

    def test_an_include_prefix_reaches_the_included_urlconf(self, tmp_path: Path) -> None:
        # Without the mount this was "/items", which matches nothing a client calls.
        assert "http::*::/api/items" in self._extract(tmp_path)

    def test_a_path_call_outside_django_is_not_an_endpoint(self, tmp_path: Path) -> None:
        # `path` is an ordinary name, and the dialect reads every .py file, so
        # without the import gate any repo with a helper of that shape gained
        # fabricated endpoints.
        (tmp_path / "router.py").write_text(
            'def path(p, h): ...\n\nroutes = [path("admin/", handlers.admin)]\n',
            encoding="utf-8",
        )
        assert _providers(tmp_path) == {}

    def test_a_urlconf_away_from_urls_py_is_still_read(self, tmp_path: Path) -> None:
        # Django's own admin publishes most of its surface from `get_urls()` in
        # options.py, so the gate is the import and not the filename.
        (tmp_path / "options.py").write_text(
            "from django.urls import path\n\n"
            "def get_urls():\n    return [path('add/', self.add_view)]\n",
            encoding="utf-8",
        )
        assert "http::*::/add" in _providers(tmp_path)

    def test_a_wrapped_view_records_no_handler(self, tmp_path: Path) -> None:
        contracts = self._extract(tmp_path)
        assert contracts["http::*::/healthz"].meta["handler"] == "views.healthz"
        assert "handler" not in contracts["http::*::/orders/{param}"].meta


class TestDjangoGraphConsumer:
    def test_a_urlconf_links_to_the_module_it_names(self, tmp_path: Path) -> None:
        (tmp_path / "urls.py").write_text(ROOT_URLS_PY, encoding="utf-8")
        (tmp_path / "myapp").mkdir()
        (tmp_path / "myapp" / "urls.py").write_text(APP_URLS_PY, encoding="utf-8")
        (tmp_path / "myapp" / "views.py").write_text(VIEWS_PY, encoding="utf-8")
        graph = _graph(tmp_path, "*.py", "python", ["django"])
        # The same declaration the contracts above came from: the include's
        # module and the view module the entries name.
        assert graph.has_edge("urls.py", "myapp/urls.py")
        assert graph.has_edge("myapp/urls.py", "myapp/views.py")


# ---------------------------------------------------------------------------
# JAX-RS
# ---------------------------------------------------------------------------


class TestJaxRsRecognition:
    def test_the_class_path_is_told_from_the_method_paths(self) -> None:
        # A dotted annotation sits between the two, and every @Path here uses the
        # same syntax, so only the declaration each one reaches separates them.
        assert [p for _off, p in jaxrs_class_paths(ACCOUNT_RESOURCE_JAVA)] == ["/v2/accounts"]

    def test_each_verb_carries_its_own_sub_path(self) -> None:
        assert [(r.verb, r.path) for r in jaxrs_routes(ACCOUNT_RESOURCE_JAVA)] == [
            ("GET", "/data_report"),
            ("PUT", "/{id}/number"),
            ("GET", ""),
        ]

    def test_an_annotation_carrying_braces_does_not_end_the_run(self) -> None:
        # `@Produces({...})` puts a brace between @GET and its @Path. Only the
        # paren-depth check keeps the run open across it; a scan for the next
        # bare `{` stops here and reads no path at all.
        source = (
            "@Path(\"/v1\")\npublic class R {\n"
            '  @GET\n  @Produces({"application/json", "text/plain"})\n'
            '  @Path("/report")\n  public Report r() { return null; }\n}\n'
        )
        assert [(r.verb, r.path) for r in jaxrs_routes(source)] == [("GET", "/report")]

    def test_a_text_block_does_not_swallow_the_rest_of_the_file(self) -> None:
        # A Java text block holding an odd number of quotes: pairwise quote
        # matching reads the opening `\"\"\"` as an empty string plus an
        # unterminated one, and every later route is lost inside it.
        source = (
            "@Path(\"/v1\")\npublic class R {\n"
            '  @GET\n  @Operation(description = """\n'
            'He said "gotcha then { boom } ; done\n""")\n'
            '  @Path("/first")\n  public R a() { return null; }\n'
            '  @POST\n  @Path("/second")\n  public R b() { return null; }\n}\n'
        )
        assert [(r.verb, r.path) for r in jaxrs_routes(source)] == [
            ("GET", "/first"),
            ("POST", "/second"),
        ]
        assert [p for _off, p in jaxrs_class_paths(source)] == ["/v1"]

    def test_a_method_path_is_not_read_as_the_class_prefix(self) -> None:
        # Both are spelled `@Path`; only the declaration each one reaches apart.
        source = (
            "@Path(\"/v1\")\npublic class R {\n"
            '  @GET\n  @Path("/inner")\n  public R a() { return null; }\n}\n'
        )
        assert [p for _off, p in jaxrs_class_paths(source)] == ["/v1"]


class TestJaxRsContracts:
    def _extract(self, tmp_path: Path) -> dict[str, Any]:
        (tmp_path / "AccountResource.java").write_text(
            ACCOUNT_RESOURCE_JAVA, encoding="utf-8"
        )
        return _providers(tmp_path)

    def test_the_class_prefix_is_stitched_onto_each_method(self, tmp_path: Path) -> None:
        ids = self._extract(tmp_path)
        assert "http::GET::/v2/accounts/data_report" in ids
        assert "http::PUT::/v2/accounts/{param}/number" in ids
        assert ids["http::GET::/v2/accounts/data_report"].meta["framework"] == "jaxrs"

    def test_a_verb_with_no_sub_path_serves_the_class_path(self, tmp_path: Path) -> None:
        assert "http::GET::/v2/accounts" in self._extract(tmp_path)


# ---------------------------------------------------------------------------
# Next.js App Router
# ---------------------------------------------------------------------------


class TestNextAppRecognition:
    def test_the_path_comes_from_the_file_location(self) -> None:
        assert next_route_path("app/api/posts/route.ts") == "/api/posts"
        assert next_route_path("src/app/api/users/[id]/route.tsx") == "/api/users/{id}"

    def test_inert_segments_name_no_url_segment(self) -> None:
        assert (
            next_route_path("app/(marketing)/api/@modal/_lib/v1/[...slug]/route.ts")
            == "/api/v1/{slug}"
        )

    def test_only_route_files_are_endpoints(self) -> None:
        # `page`/`layout` are App Router files but publish no API surface.
        assert next_route_path("app/api/posts/page.tsx") is None
        assert next_route_path("pages/api/posts.ts") is None

    def test_the_verb_comes_from_the_exported_name(self) -> None:
        verbs = [v for v, _off in next_route_verbs(NEXT_ROUTE_TS)]
        assert verbs == ["GET", "POST"]


class TestNextAppContracts:
    def test_one_file_yields_one_contract_per_exported_verb(self, tmp_path: Path) -> None:
        route = tmp_path / "app" / "api" / "posts"
        route.mkdir(parents=True)
        (route / "route.ts").write_text(NEXT_ROUTE_TS, encoding="utf-8")
        ids = _providers(tmp_path)
        assert set(ids) == {"http::GET::/api/posts", "http::POST::/api/posts"}
        assert ids["http::GET::/api/posts"].meta["framework"] == "next-app"

    async def test_each_verb_binds_to_its_own_export(self, tmp_path: Path) -> None:
        # Every route handler in a Next repo exports the same few names, so the
        # binding has to be by line rather than by a repo-wide lookup on "GET".
        route = tmp_path / "app" / "api" / "posts"
        route.mkdir(parents=True)
        (route / "route.ts").write_text(NEXT_ROUTE_TS, encoding="utf-8")
        contracts = [c for c in HttpExtractor().extract(tmp_path, "api") if c.role == "provider"]
        index = await _index_of(tmp_path, "*.ts", "typescript")
        try:
            expected = {
                s.name: s.symbol_id
                for s in index.symbols_for_file("app/api/posts/route.ts")
            }
            bind_symbol_ids(contracts, index)
        finally:
            await index.close()
        bound = {c.contract_id: c.symbol_id for c in contracts}
        assert bound["http::GET::/api/posts"] == expected["GET"]
        assert bound["http::POST::/api/posts"] == expected["POST"]


# ---------------------------------------------------------------------------
# Hono / Fastify / Koa — the router DSL Express shares
# ---------------------------------------------------------------------------


class TestRouterBindings:
    def test_each_constructor_names_its_framework(self) -> None:
        assert js_router_bindings(HONO_APP_TS) == {"api": "hono", "legacy": "express"}

    def test_every_supported_constructor_is_recognised(self) -> None:
        source = (
            "const a = new Hono();\n"
            "const b = Fastify({ logger: true });\n"
            "const c = new Koa();\n"
            "const d = new Elysia();\n"
            "const e = express();\n"
            "const f: Router = express.Router();\n"
        )
        assert js_router_bindings(source) == {
            "a": "hono", "b": "fastify", "c": "koa",
            "d": "elysia", "e": "express", "f": "express",
        }


class TestRouterDslContracts:
    def test_a_hono_route_is_labelled_hono_not_express(self, tmp_path: Path) -> None:
        # `const api = new Hono()` was invisible: the contract layer only knew
        # Express bindings, so a route on any name but `app`/`router` was dropped.
        (tmp_path / "server.ts").write_text(HONO_APP_TS, encoding="utf-8")
        ids = _providers(tmp_path)
        assert ids["http::GET::/users"].meta["framework"] == "hono"
        assert ids["http::GET::/ping"].meta["framework"] == "express"

    def test_a_conventional_name_takes_the_file_s_framework(self, tmp_path: Path) -> None:
        (tmp_path / "server.ts").write_text(
            "import { Hono } from 'hono';\n"
            "const other = new Hono();\n"
            "app.get('/health', h);\n",
            encoding="utf-8",
        )
        assert _providers(tmp_path)["http::GET::/health"].meta["framework"] == "hono"

    def test_an_express_repo_is_unchanged(self, tmp_path: Path) -> None:
        (tmp_path / "server.js").write_text(
            "const router = express.Router();\nrouter.get('/api/users', auth, list);\n",
            encoding="utf-8",
        )
        ids = _providers(tmp_path)
        assert ids["http::GET::/api/users"].meta["framework"] == "express"

    def test_a_nested_namespace_constructor_still_binds(self, tmp_path: Path) -> None:
        # The regex this table replaced took any dotted prefix; a one-segment
        # prefix would have dropped these routes outright.
        (tmp_path / "server.js").write_text(
            "const r = services.http.Router();\nr.get('/deep', h);\n", encoding="utf-8"
        )
        assert _providers(tmp_path)["http::GET::/deep"].meta["framework"] == "express"


# ---------------------------------------------------------------------------
# Fixtures built from real parser output, never hand-written ids
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
            is_entry_point=False,
        )
        out[rel] = parser.parse_file(fi, src.read_bytes())
    return out


def _graph(repo: Path, glob: str, language: str, stack: list[str]) -> nx.DiGraph:
    parsed = _parse_repo(repo, glob, language)
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


async def _index_of(repo: Path, glob: str, language: str) -> Any:
    """An index over the rows real ingestion produces, so no id is guessed."""
    parsed = _parse_repo(repo, glob, language)
    by_file: dict[str, list[Any]] = {}
    for rel, pf in parsed.items():
        for sym in pf.symbols:
            sym.file_path = rel
        by_file[rel] = list(pf.symbols)
    return await make_repo_index(repo, by_file, alias="api")
