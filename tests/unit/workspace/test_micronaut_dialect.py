"""Micronaut provider contracts, from the annotation the graph already read.

``@Controller`` was a presence check that stamped a component role, and nothing
read the paths, so a Micronaut repo published no endpoints at all: cross-repo
links, breaking-change detection and schema recovery had nothing to attach to.
The claims under test are that every spelling of the verb annotation becomes a
contract with the path Micronaut actually serves, that the class prefix reaches
it, that the graph consumer still stamps its role and forms its injection edges
from the same recognition, and that Spring's and JAX-RS's identically spelled
annotations stay out.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import networkx as nx

from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.framework_routes import (
    micronaut_annotations,
    micronaut_class_paths,
    micronaut_client_types,
    micronaut_routes,
)
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.workspace.extractors.http.jaxrs import JaxRsDialect
from repowise.core.workspace.extractors.http.micronaut import MicronautDialect
from repowise.core.workspace.extractors.http.spring import SpringDialect
from repowise.core.workspace.extractors.http_extractor import HttpExtractor

USER_CONTROLLER_JAVA = """\
package com.example;

import io.micronaut.http.HttpResponse;
import io.micronaut.http.annotation.Body;
import io.micronaut.http.annotation.Controller;
import io.micronaut.http.annotation.Delete;
import io.micronaut.http.annotation.Get;
import io.micronaut.http.annotation.Head;
import io.micronaut.http.annotation.Post;
import jakarta.inject.Inject;

@Controller("/v1/users")
public class UserController {

    @Inject
    private UserService users;

    @Get("/{id}")
    public HttpResponse<String> show(String id) {
        return HttpResponse.ok(id);
    }

    @Get
    public List<String> list() {
        return users.all();
    }

    @Post(uri = "/")
    @Produces(MediaType.APPLICATION_JSON)
    public HttpResponse<String> create(@Body String name) {
        return HttpResponse.created(name);
    }

    @Delete(value = "/{id}")
    public void remove(String id) {
    }

    @Head("/{id}/probe")
    public void probe(String id) {
    }

    @Get(produces = {MediaType.TEXT_HTML}, consumes = {MediaType.TEXT_HTML})
    public String page() {
        return "";
    }

    @Get("/search{?q,sort}")
    public List<String> search(String q) {
        return List.of();
    }
}
"""

BOOK_CLIENT_JAVA = """\
package com.example;

import io.micronaut.http.annotation.Get;
import io.micronaut.http.client.annotation.Client;

@Client("http://localhost:8082")
public interface BookInventoryClient {

    @Get("/books/stock/{isbn}")
    Boolean stock(String isbn);
}
"""

CLIENT_BESIDE_CONTROLLER_JAVA = """\
package com.example;

import io.micronaut.http.annotation.Controller;
import io.micronaut.http.annotation.Get;
import io.micronaut.http.client.annotation.Client;

public class AvailabilityTest {

    @Client("/")
    interface AvailabilityClient {
        @Get("/called")
        String called();
    }

    @Controller("/availability")
    static class AvailabilityController {
        @Get
        String index() {
            return "";
        }
    }
}
"""

USER_SERVICE_JAVA = """\
package com.example;

import jakarta.inject.Singleton;

@Singleton
public class UserService {
    public List<String> all() {
        return List.of();
    }
}
"""

GREET_CONTROLLER_KT = """\
package com.example

import io.micronaut.http.annotation.Controller
import io.micronaut.http.annotation.Get

@Controller("/greet")
class GreetController {

    @Get("/{name}")
    fun greet(name: String): String = "hello ${name}"

    @Get
    fun index(): String = "hi"
}
"""

SPRING_CONTROLLER_JAVA = """\
package com.example;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/spring")
public class SpringController {

    @GetMapping("/items")
    public String items() {
        return "";
    }
}
"""

JAXRS_RESOURCE_JAVA = """\
package com.example;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;

@Path("/jaxrs")
public class JaxRsResource {

    @GET
    @Path("/items")
    public String items() {
        return "";
    }
}
"""


class _Extractor(HttpExtractor):
    # A class attribute because `source_extensions` is a classmethod: set on an
    # instance it would leave the file walk reading the whole registry's set.
    provider_dialects = (JaxRsDialect(), SpringDialect(), MicronautDialect())
    consumer_dialects = ()


def _providers(repo: Path, alias: str = "api") -> dict[str, Any]:
    return {c.contract_id: c for c in _Extractor().extract(repo, alias) if c.role == "provider"}


class TestMicronautRecognition:
    def test_the_class_prefix_is_told_from_the_method_paths(self) -> None:
        assert [p for _off, p in micronaut_class_paths(USER_CONTROLLER_JAVA)] == ["/v1/users"]

    def test_every_annotation_spelling_is_read(self) -> None:
        assert [(r.verb, r.path, r.handler) for r in micronaut_routes(USER_CONTROLLER_JAVA)] == [
            ("GET", "/{id}", "show"),
            ("GET", "", "list"),
            ("POST", "/", "create"),
            ("DELETE", "/{id}", "remove"),
            ("HEAD", "/{id}/probe", "probe"),
            ("GET", "", "page"),
            ("GET", "/search", "search"),
        ]

    def test_arguments_that_name_no_path_leave_the_class_prefix(self) -> None:
        # `@Get(produces = ...)` names no path at all, so Micronaut serves it
        # exactly where a bare `@Get` is served. Reading it as unreadable cost
        # 42 real endpoints in one sample corpus.
        source = "@Get(produces = MediaType.TEXT_PLAIN)\npublic String page() { return null; }\n"
        assert [r.path for r in micronaut_routes(source)] == [""]

    def test_a_query_expansion_is_not_path_text(self) -> None:
        # `{?q,sort}` is a query template. Left in, it reaches the contract id
        # as a path parameter, because the normaliser only drops a plain `?`.
        source = '@Get("/list{?args*}")\npublic String list() { return null; }\n'
        assert [r.path for r in micronaut_routes(source)] == ["/list"]

    def test_a_client_interface_is_told_from_a_controller(self) -> None:
        assert micronaut_client_types(BOOK_CLIENT_JAVA) == [BOOK_CLIENT_JAVA.index("@Client")]
        assert micronaut_class_paths(BOOK_CLIENT_JAVA) == []

    def test_a_controller_written_on_one_line_is_a_controller(self) -> None:
        # How the guides and the generated app both write a small controller,
        # and what a declaration match anchored to the line start reads as no
        # class at all.
        source = '@Controller("/hi") public class Hi {\n  @Get String hi() { return ""; }\n}\n'
        assert [p for _off, p in micronaut_class_paths(source)] == ["/hi"]

    def test_an_annotation_carrying_parens_does_not_hide_the_method(self) -> None:
        source = (
            '@Get("/r")\n@Produces({"application/json"})\n'
            '@ApiResponse(content = @Content(schema = @Schema(name = "R")))\n'
            "public Report report() { return null; }\n"
        )
        assert [r.handler for r in micronaut_routes(source)] == ["report"]

    def test_a_controller_prefix_named_by_a_constant_keeps_its_entry(self) -> None:
        # The prefix is unreadable, but the type is still a controller: the
        # graph reads the entry, the contract side refuses the routes under it.
        assert [p for _off, p in micronaut_class_paths(CONST_CONTROLLER_JAVA)] == [None]

    def test_a_record_and_an_enum_are_controllers_too(self) -> None:
        # Both are ordinary Micronaut controller spellings, and a declaration
        # form left out here reads as no type at all.
        assert [p for _off, p in micronaut_class_paths(RECORD_CONTROLLER_JAVA)] == ["/points"]
        source = '@Controller("/modes") enum Mode { A }\n'
        assert [p for _off, p in micronaut_class_paths(source)] == ["/modes"]

    def test_one_annotation_scan_serves_all_three_readers(self) -> None:
        # The offsets are the file's `@` positions written as code, so passing
        # them in must read exactly as computing them per call did.
        offsets = micronaut_annotations(USER_CONTROLLER_JAVA)
        assert micronaut_class_paths(USER_CONTROLLER_JAVA, offsets) == micronaut_class_paths(
            USER_CONTROLLER_JAVA
        )
        assert list(micronaut_routes(USER_CONTROLLER_JAVA, offsets)) == list(
            micronaut_routes(USER_CONTROLLER_JAVA)
        )
        clients = micronaut_client_types(BOOK_CLIENT_JAVA)
        assert (
            micronaut_client_types(BOOK_CLIENT_JAVA, micronaut_annotations(BOOK_CLIENT_JAVA))
            == clients
        )

    def test_a_kotlin_controller_reads_the_same(self) -> None:
        assert [p for _off, p in micronaut_class_paths(GREET_CONTROLLER_KT)] == ["/greet"]
        assert [(r.verb, r.path, r.handler) for r in micronaut_routes(GREET_CONTROLLER_KT)] == [
            ("GET", "/{name}", "greet"),
            ("GET", "", "index"),
        ]

    def test_a_uri_list_names_no_single_path(self) -> None:
        # `uris = {...}` serves several paths from one method. None of them is
        # readable here, and the class prefix on its own is not one of them.
        source = '@Get(uris = {"/a", "/b"})\npublic String many() { return null; }\n'
        assert [r.path for r in micronaut_routes(source)] == [None]


CONST_CONTROLLER_JAVA = """\
package com.example;

import io.micronaut.http.annotation.Controller;
import io.micronaut.http.annotation.Get;
import jakarta.inject.Inject;

@Controller(ApiPaths.USERS)
public class ConstController {

    @Inject
    private UserService users;

    @Get("/{id}")
    public String show(String id) {
        return id;
    }
}
"""

RECORD_CONTROLLER_JAVA = """\
package com.example;

import io.micronaut.http.annotation.Controller;
import io.micronaut.http.annotation.Get;

@Controller("/points")
public record PointController(PointService points) {

    @Get("/{id}")
    public String show(String id) {
        return id;
    }
}
"""

OPERATIONS_INTERFACE_JAVA = """\
package com.example;

import io.micronaut.http.annotation.Get;

public interface BookOperations {

    @Get("/books/{isbn}")
    String find(String isbn);
}
"""


class TestMicronautContracts:
    def _extract(self, tmp_path: Path) -> dict[str, Any]:
        (tmp_path / "UserController.java").write_text(USER_CONTROLLER_JAVA, encoding="utf-8")
        (tmp_path / "GreetController.kt").write_text(GREET_CONTROLLER_KT, encoding="utf-8")
        return _providers(tmp_path)

    def test_the_class_prefix_is_stitched_onto_each_method(self, tmp_path: Path) -> None:
        contract = self._extract(tmp_path)["http::GET::/v1/users/{param}"]
        assert contract.meta["framework"] == "micronaut"
        assert contract.meta["handler"] == "show"

    def test_a_verb_with_no_path_serves_the_class_prefix(self, tmp_path: Path) -> None:
        assert "http::GET::/v1/users" in self._extract(tmp_path)

    def test_the_named_uri_form_is_a_path(self, tmp_path: Path) -> None:
        assert "http::POST::/v1/users" in self._extract(tmp_path)

    def test_the_value_alias_is_a_path(self, tmp_path: Path) -> None:
        assert "http::DELETE::/v1/users/{param}" in self._extract(tmp_path)

    def test_a_kotlin_controller_yields_the_same_contracts(self, tmp_path: Path) -> None:
        ids = self._extract(tmp_path)
        assert "http::GET::/greet/{param}" in ids
        assert "http::GET::/greet" in ids

    def test_a_path_parameter_normalizes_like_every_other_dialect(self, tmp_path: Path) -> None:
        # `{id}` and `{name}` are two names for one position, so a consumer
        # calling either must land on the same contract id.
        ids = self._extract(tmp_path)
        assert "http::GET::/v1/users/{id}" not in ids
        assert "http::GET::/v1/users/{param}" in ids

    def test_arguments_that_name_no_path_serve_the_class_prefix(self, tmp_path: Path) -> None:
        assert "http::GET::/v1/users" in self._extract(tmp_path)

    def test_a_query_expansion_stays_out_of_the_contract_id(self, tmp_path: Path) -> None:
        ids = self._extract(tmp_path)
        assert "http::GET::/v1/users/search" in ids
        assert not any(id_.startswith("http::GET::/v1/users/search{") for id_ in ids)

    def test_the_three_java_dialects_do_not_double_emit(self, tmp_path: Path) -> None:
        (tmp_path / "UserController.java").write_text(USER_CONTROLLER_JAVA, encoding="utf-8")
        frameworks = {c.meta["framework"] for c in _providers(tmp_path).values()}
        assert frameworks == {"micronaut"}


class TestMicronautGraphConsumer:
    def test_a_controller_is_still_an_entry_point_with_its_edges(self, tmp_path: Path) -> None:
        (tmp_path / "UserController.java").write_text(USER_CONTROLLER_JAVA, encoding="utf-8")
        (tmp_path / "UserService.java").write_text(USER_SERVICE_JAVA, encoding="utf-8")
        graph = _graph(tmp_path, "*.java", "java", ["micronaut"])
        # The same annotation the contracts above came from: `@Controller` is
        # the file's only Micronaut stereotype, so the role and the injection
        # edge below it both hang off recognising it.
        node = graph.nodes["UserController.java"]
        assert node["is_entry_point"] is True
        assert node["framework_role"] == "micronaut_component"
        assert graph.has_edge("UserController.java", "UserService.java")


class TestMicronautRefusals:
    def test_a_spring_controller_yields_no_micronaut_contract(self, tmp_path: Path) -> None:
        (tmp_path / "SpringController.java").write_text(SPRING_CONTROLLER_JAVA, encoding="utf-8")
        frameworks = {c.meta["framework"] for c in _providers(tmp_path).values()}
        assert frameworks == {"spring"}

    def test_a_jaxrs_resource_yields_no_micronaut_contract(self, tmp_path: Path) -> None:
        (tmp_path / "JaxRsResource.java").write_text(JAXRS_RESOURCE_JAVA, encoding="utf-8")
        frameworks = {c.meta["framework"] for c in _providers(tmp_path).values()}
        assert frameworks == {"jaxrs"}

    def test_a_spring_annotation_set_is_not_micronauts(self, tmp_path: Path) -> None:
        # Spring spells `@Controller` the same way, so without the import gate
        # every Spring MVC class gained a fabricated Micronaut prefix.
        (tmp_path / "Legacy.java").write_text(
            "import org.springframework.stereotype.Controller;\n\n"
            '@Controller("/legacy")\npublic class Legacy {\n'
            '  @Get("/x")\n  public String x() { return ""; }\n}\n',
            encoding="utf-8",
        )
        assert _providers(tmp_path) == {}

    def test_an_annotation_in_a_comment_is_documentation(self, tmp_path: Path) -> None:
        (tmp_path / "Doc.java").write_text(
            "import io.micronaut.http.annotation.Controller;\n\n"
            '/**\n * @Controller("/docs")\n * @Get("/example")\n */\n'
            "public class Doc {\n}\n",
            encoding="utf-8",
        )
        assert _providers(tmp_path) == {}

    def test_an_annotation_in_a_string_is_not_a_route(self, tmp_path: Path) -> None:
        (tmp_path / "Doc.java").write_text(
            "import io.micronaut.http.annotation.Controller;\n\n"
            '@Controller("/real")\npublic class Doc {\n'
            '  String sample = "@Get(\\"/quoted\\")";\n}\n',
            encoding="utf-8",
        )
        assert _providers(tmp_path) == {}

    def test_a_uri_list_is_not_served_at_the_class_prefix(self, tmp_path: Path) -> None:
        # A stated ceiling: several paths from one method, and reading the
        # prefix alone would publish an endpoint Micronaut does not serve.
        (tmp_path / "Many.java").write_text(
            "import io.micronaut.http.annotation.Controller;\n\n"
            '@Controller("/many")\npublic class Many {\n'
            '  @Get(uris = {"/a", "/b"})\n  public String many() { return ""; }\n}\n',
            encoding="utf-8",
        )
        assert _providers(tmp_path) == {}

    def test_a_declarative_client_is_not_a_provider(self, tmp_path: Path) -> None:
        # `@Client` interfaces carry the same verb annotations as a controller
        # and describe calls out. 403 of one sample corpus's files hold one, and
        # reading them as endpoints published services that serve nothing.
        (tmp_path / "BookInventoryClient.java").write_text(BOOK_CLIENT_JAVA, encoding="utf-8")
        assert _providers(tmp_path) == {}

    def test_a_client_beside_a_controller_takes_only_its_own_routes(self, tmp_path: Path) -> None:
        # One file, both annotations: only the type each route sits under can
        # tell them apart.
        (tmp_path / "AvailabilityTest.java").write_text(
            CLIENT_BESIDE_CONTROLLER_JAVA, encoding="utf-8"
        )
        assert set(_providers(tmp_path)) == {"http::GET::/availability"}

    def test_a_constant_class_prefix_publishes_nothing(self, tmp_path: Path) -> None:
        # The sub-path on its own is not where the route is served, so the
        # route is refused rather than published under a guessed prefix.
        (tmp_path / "ConstController.java").write_text(CONST_CONTROLLER_JAVA, encoding="utf-8")
        assert _providers(tmp_path) == {}

    def test_a_constant_class_prefix_is_still_a_component(self, tmp_path: Path) -> None:
        # The graph reads the same annotation for a role and its injection
        # edges, and neither depends on the prefix being readable.
        (tmp_path / "ConstController.java").write_text(CONST_CONTROLLER_JAVA, encoding="utf-8")
        (tmp_path / "UserService.java").write_text(USER_SERVICE_JAVA, encoding="utf-8")
        graph = _graph(tmp_path, "*.java", "java", ["micronaut"])
        node = graph.nodes["ConstController.java"]
        assert node["is_entry_point"] is True
        assert node["framework_role"] == "micronaut_component"
        assert graph.has_edge("ConstController.java", "UserService.java")

    def test_a_constant_method_path_publishes_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "Const.java").write_text(
            "import io.micronaut.http.annotation.Controller;\n\n"
            '@Controller("/const")\npublic class Const {\n'
            '  @Get(ApiPaths.SHOW)\n  public String show() { return ""; }\n}\n',
            encoding="utf-8",
        )
        assert _providers(tmp_path) == {}

    def test_an_operations_interface_serves_nothing(self, tmp_path: Path) -> None:
        # The interface a `@Client` implements carries the verb annotations and
        # no type-level annotation at all; only a `@Controller` serves.
        (tmp_path / "BookOperations.java").write_text(OPERATIONS_INTERFACE_JAVA, encoding="utf-8")
        assert _providers(tmp_path) == {}

    def test_head_and_options_are_not_contracts(self, tmp_path: Path) -> None:
        (tmp_path / "Probe.java").write_text(
            "import io.micronaut.http.annotation.Controller;\n\n"
            '@Controller("/probe")\npublic class Probe {\n'
            '  @Head("/a")\n  public void a() {}\n'
            '  @Options("/b")\n  public void b() {}\n}\n',
            encoding="utf-8",
        )
        assert _providers(tmp_path) == {}


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
