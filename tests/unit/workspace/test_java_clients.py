"""The Java HTTP consumer dialect.

The claim under test is that each Java shape carries its verb from a place the
file actually names (a Feign annotation, a builder terminal, a callee name, an
``HttpMethod`` constant) and that a URL is resolved as an expression or refused,
so a builder whose terminal or target the file does not settle emits nothing.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.contracts import match_contracts
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.extractors.http.fastapi import FastApiDialect
from repowise.core.workspace.extractors.http.java_clients import (
    JavaClientsDialect,
    feign_calls,
    java_net_http_calls,
    resttemplate_calls,
)

FEIGN_CLIENT = """package com.example.client;

import feign.Param;
import feign.RequestLine;

public interface ImagesApi {

    @RequestLine("POST /v1/{entityType}/{entityId}/images")
    void create(@Param("entityType") String entityType);

    @RequestLine("GET /v1/{entityType}/{entityId}/images")
    String read(@Param("entityType") String entityType);

    @RequestLine("DELETE /v1/{entityType}/{entityId}/images")
    void remove(@Param("entityType") String entityType);
}
"""

FEIGN_QUERY_AND_RELATIVE = """package com.example.client;

public interface LoansApi {

    @RequestLine("POST v1/internal/loans/{loanId}/activate?date={date}")
    void activate(@Param("loanId") Long loanId);
}
"""

FEIGN_NON_VERB = """package com.example.client;

public interface Odd {

    @RequestLine("TRACE /v1/diagnostics")
    void trace();
}
"""

BUILDER_LITERAL = """package com.example;

class Client {
    void ping() throws Exception {
        final HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create("https://api.example.com/v1/ping"))
            .GET()
            .build();
    }
}
"""

BUILDER_URI_FIRST_ARG = """package com.example;

class Client {
    HttpRequest post(String base) {
        return HttpRequest.newBuilder(URI.create(base + "/v1/echo"))
            .POST(HttpRequest.BodyPublishers.ofString("{}"))
            .header("Content-Type", "application/json")
            .build();
    }
}
"""

BUILDER_STRING_FORMAT = """package com.example;

class Client {
    HttpRequest fetch(String id) {
        return HttpRequest.newBuilder()
            .uri(URI.create(String.format("https://api.example.com/v1/items/%s", id)))
            .DELETE()
            .build();
    }
}
"""

BUILDER_METHOD_LITERAL = """package com.example;

class Client {
    HttpRequest patch() {
        return HttpRequest.newBuilder(URI.create("https://api.example.com/v1/items/7"))
            .method("PATCH", HttpRequest.BodyPublishers.ofString("{}"))
            .build();
    }
}
"""

BUILDER_FOLDED_CONSTANT = """package com.example;

class Client {
    private static final String ENDPOINT = "https://api.example.com/v1/status";

    HttpRequest status() {
        return HttpRequest.newBuilder().uri(URI.create(ENDPOINT)).GET().build();
    }
}
"""

BUILDER_NO_TERMINAL = """package com.example;

class Client {
    HttpRequest.Builder base() {
        return HttpRequest.newBuilder(URI.create("https://api.example.com/v1/items"))
            .header("Accept", "application/json");
    }
}
"""

BUILDER_VARIABLE_METHOD = """package com.example;

class Client {
    HttpRequest any(String method) {
        return HttpRequest.newBuilder()
            .uri(URI.create("https://api.example.com/v1/items"))
            .method(method, HttpRequest.BodyPublishers.noBody())
            .build();
    }
}
"""

BUILDER_OPAQUE_URI = """package com.example;

class Client {
    HttpRequest send(URI uri) {
        return HttpRequest.newBuilder().uri(uri).GET().build();
    }
}
"""

BUILDER_HELPER_URL = """package com.example;

class Client {
    HttpRequest copy() {
        return HttpRequest.newBuilder()
            .uri(URI.create(copyUrl()))
            .PUT(HttpRequest.BodyPublishers.ofString("{}"))
            .build();
    }
}
"""

BUILDER_NO_URI = """package com.example;

class Client {
    HttpRequest.Builder get() {
        return HttpRequest.newBuilder().GET();
    }
}
"""

BUILDER_NEIGHBOURING_VERB = """package com.example;

class Client {
    void run(String base) throws Exception {
        client.send(HttpRequest.newBuilder(URI.create(base + "/v1/first")).build(), handler);
        client.send(HttpRequest.newBuilder(URI.create(base + "/v1/second")).GET().build(), handler);
    }
}
"""

REST_TEMPLATE_CALLEES = """package com.example;

import org.springframework.web.client.RestTemplate;

class Client {
    void run(RestTemplate restTemplate, String baseUrl) {
        restTemplate.getForObject(baseUrl + "/v1/pets", Pet.class);
        restTemplate.postForEntity(baseUrl + "/v1/pets", request, Pet.class);
        restTemplate.delete(baseUrl + "/v1/pets/7");
    }
}
"""

REST_TEMPLATE_EXCHANGE = """package com.example;

import org.springframework.web.client.RestTemplate;

class Client {
    void run(RestTemplate rest, int port) {
        rest.exchange("http://localhost:" + port + "/oups", HttpMethod.GET, entity, String.class);
        rest.exchange("https://api.example.com/v1/sms", HttpMethod.POST, entity, String.class);
    }
}
"""

REST_TEMPLATE_VARIABLE_VERB = """package com.example;

import org.springframework.web.client.RestTemplate;

class Client {
    void run(RestTemplate rest, HttpMethod verb) {
        rest.exchange("https://api.example.com/v1/sms", verb, entity, String.class);
    }
}
"""

REST_TEMPLATE_OPAQUE_URI = """package com.example;

import org.springframework.web.client.RestTemplate;

class Client {
    void run(RestTemplate restTemplate, URI uri) {
        restTemplate.exchange(uri, HttpMethod.GET, entity, String.class);
    }
}
"""

MAP_PUT_IN_REST_TEMPLATE_FILE = """package com.example;

import org.springframework.web.client.RestTemplate;

class Client {
    void run(Map<String, String> headers) {
        headers.put("Accept", "application/json");
        headers.delete("Accept");
    }
}
"""

MAP_PUT_WITHOUT_REST_TEMPLATE = """package com.example;

class Cache {
    void run(Map<String, String> routes) {
        routes.put("/v1/pets", "handler");
    }
}
"""

MAP_PUT_BESIDE_A_TEMPLATE_PUT = """package com.example;

import org.springframework.web.client.RestTemplate;

class Client {
    void run(RestTemplate restTemplate, HttpHeaders headers, Object body) {
        headers.put("Content-Type", "application/json");
        restTemplate.put("/v1/pets/7", body);
    }
}
"""

OKHTTP_BUILDER = """package com.example;

import okhttp3.Request;

class Client {
    Request build(String url) {
        return new Request.Builder().url(url).get().build();
    }
}
"""

REST_TEMPLATE_REQUEST_ENTITY = """package com.example;

import org.springframework.web.client.RestTemplate;

class Client {
    void run(RestTemplate template) {
        template.exchange(RequestEntity.get("/owners/1").build(), String.class);
    }
}
"""

BUILDER_INSTANCE_FORMATTED = """package com.example;

class Client {
    HttpRequest list(String prefix) {
        return HttpRequest.newBuilder()
            .uri(URI.create("%s%s".formatted(listUrl(), prefix)))
            .GET()
            .build();
    }
}
"""


def contracts(content: str, rel_path: str = "Client.java") -> list:
    ctx = ScanContext("svc", rel_path, ".java", content, {}, None)
    return JavaClientsDialect().extract(ctx)


def ids(content: str) -> set[str]:
    return {c.contract_id for c in contracts(content)}


class TestJavaClientsRecognition:
    def test_feign_annotation_carries_verb_and_path(self) -> None:
        rows = list(feign_calls(FEIGN_CLIENT))
        assert [(r.method, r.url) for r in rows] == [
            ("POST", '"/v1/{entityType}/{entityId}/images"'),
            ("GET", '"/v1/{entityType}/{entityId}/images"'),
            ("DELETE", '"/v1/{entityType}/{entityId}/images"'),
        ]
        assert {r.client for r in rows} == {"feign"}

    def test_feign_ignores_a_word_that_is_not_a_verb(self) -> None:
        assert list(feign_calls(FEIGN_NON_VERB)) == []

    def test_builder_terminal_names_the_verb(self) -> None:
        rows = list(java_net_http_calls(BUILDER_LITERAL))
        assert [(r.client, r.method) for r in rows] == [("java-net-http", "GET")]

    def test_builder_reads_the_uri_from_its_own_argument(self) -> None:
        rows = list(java_net_http_calls(BUILDER_URI_FIRST_ARG))
        assert [(r.method, r.url) for r in rows] == [("POST", 'URI.create(base + "/v1/echo")')]

    def test_builder_method_call_reads_a_quoted_verb(self) -> None:
        rows = list(java_net_http_calls(BUILDER_METHOD_LITERAL))
        assert [r.method for r in rows] == ["PATCH"]

    def test_a_chain_does_not_borrow_the_next_call_s_terminal(self) -> None:
        rows = list(java_net_http_calls(BUILDER_NEIGHBOURING_VERB))
        assert [r.method for r in rows] == ["GET"]

    def test_resttemplate_verb_comes_from_the_callee_name(self) -> None:
        rows = list(resttemplate_calls(REST_TEMPLATE_CALLEES))
        assert [(r.callee, r.method) for r in rows] == [
            ("getForObject", None),
            ("postForEntity", None),
            ("delete", None),
        ]

    def test_resttemplate_exchange_verb_comes_from_the_second_argument(self) -> None:
        rows = list(resttemplate_calls(REST_TEMPLATE_EXCHANGE))
        assert [r.method for r in rows] == ["GET", "POST"]


class TestJavaClientsContracts:
    def test_feign_paths_normalise_their_placeholders(self) -> None:
        assert ids(FEIGN_CLIENT) == {
            "http::POST::/v1/{param}/{param}/images",
            "http::GET::/v1/{param}/{param}/images",
            "http::DELETE::/v1/{param}/{param}/images",
        }

    def test_a_relative_feign_path_gains_a_slash_and_drops_its_query(self) -> None:
        assert ids(FEIGN_QUERY_AND_RELATIVE) == {"http::POST::/v1/internal/loans/{param}/activate"}

    def test_feign_rows_are_labelled_and_confident(self) -> None:
        (c,) = contracts(FEIGN_QUERY_AND_RELATIVE)
        assert c.role == "consumer"
        assert c.meta["client"] == "feign"
        assert c.confidence == 0.75

    def test_a_literal_uri_records_its_host(self) -> None:
        (c,) = contracts(BUILDER_LITERAL)
        assert c.contract_id == "http::GET::/v1/ping"
        assert c.meta["client"] == "java-net-http"
        assert c.meta["host"] == "api.example.com"

    def test_a_concatenated_base_is_stripped_and_recorded(self) -> None:
        (c,) = contracts(BUILDER_URI_FIRST_ARG)
        assert c.contract_id == "http::POST::/v1/echo"
        assert c.meta["base_stripped"] is True
        assert c.meta["base_token"] == "base"

    def test_string_format_holes_collapse_to_a_parameter(self) -> None:
        assert ids(BUILDER_STRING_FORMAT) == {"http::DELETE::/v1/items/{param}"}

    def test_a_same_file_constant_folds(self) -> None:
        assert ids(BUILDER_FOLDED_CONSTANT) == {"http::GET::/v1/status"}

    def test_resttemplate_rows_are_labelled_and_hedged(self) -> None:
        cs = contracts(REST_TEMPLATE_CALLEES)
        assert {c.contract_id for c in cs} == {
            "http::GET::/v1/pets",
            "http::POST::/v1/pets",
            "http::DELETE::/v1/pets/7",
        }
        assert {c.meta["client"] for c in cs} == {"resttemplate"}
        assert {c.confidence for c in cs} == {0.65}

    def test_an_exchange_host_expression_leaves_the_path(self) -> None:
        cs = contracts(REST_TEMPLATE_EXCHANGE)
        assert {c.contract_id for c in cs} == {
            "http::GET::/oups",
            "http::POST::/v1/sms",
        }


class TestJavaClientsRefusals:
    def test_a_builder_with_no_terminal_verb_emits_nothing(self) -> None:
        assert contracts(BUILDER_NO_TERMINAL) == []

    def test_a_verb_held_in_a_variable_emits_nothing(self) -> None:
        assert contracts(BUILDER_VARIABLE_METHOD) == []

    def test_an_opaque_uri_variable_emits_nothing(self) -> None:
        assert contracts(BUILDER_OPAQUE_URI) == []

    def test_a_url_built_by_a_helper_call_emits_nothing(self) -> None:
        assert contracts(BUILDER_HELPER_URL) == []

    def test_a_builder_with_no_uri_emits_nothing(self) -> None:
        assert contracts(BUILDER_NO_URI) == []

    def test_an_exchange_with_a_variable_verb_emits_nothing(self) -> None:
        assert contracts(REST_TEMPLATE_VARIABLE_VERB) == []

    def test_an_exchange_with_an_opaque_uri_emits_nothing(self) -> None:
        assert contracts(REST_TEMPLATE_OPAQUE_URI) == []

    def test_a_map_call_in_a_resttemplate_file_emits_nothing(self) -> None:
        assert contracts(MAP_PUT_IN_REST_TEMPLATE_FILE) == []

    def test_a_file_that_never_names_resttemplate_is_not_read(self) -> None:
        assert list(resttemplate_calls(MAP_PUT_WITHOUT_REST_TEMPLATE)) == []
        assert contracts(MAP_PUT_WITHOUT_REST_TEMPLATE) == []

    def test_only_a_template_receiver_owns_a_bare_put(self) -> None:
        rows = list(resttemplate_calls(MAP_PUT_BESIDE_A_TEMPLATE_PUT))
        assert [(r.callee, r.url) for r in rows] == [("put", '"/v1/pets/7"')]
        assert ids(MAP_PUT_BESIDE_A_TEMPLATE_PUT) == {"http::PUT::/v1/pets/7"}

    def test_an_okhttp_request_builder_emits_nothing(self) -> None:
        assert contracts(OKHTTP_BUILDER) == []

    def test_an_exchange_whose_verb_lives_in_a_request_entity_emits_nothing(self) -> None:
        assert contracts(REST_TEMPLATE_REQUEST_ENTITY) == []

    def test_an_instance_formatted_call_is_not_a_format_head(self) -> None:
        assert contracts(BUILDER_INSTANCE_FORMATTED) == []


JAVA_CONSUMER = """package com.example.client;

import feign.RequestLine;

public interface UsersApi {

    @RequestLine("GET /users/{userId}")
    String read(@Param("userId") String userId);
}
"""

FASTAPI_PROVIDER = """from fastapi import FastAPI

app = FastAPI()


@app.get("/users/{user_id}")
def read_user(user_id: str):
    return {"id": user_id}
"""


class TestJavaClientsCrossRepoLink:
    def test_java_consumer_links_to_a_python_provider(self) -> None:
        extractor = HttpExtractor()
        extractor.provider_dialects = (FastApiDialect(),)
        extractor.consumer_dialects = (JavaClientsDialect(),)
        providers = extractor.extract(
            Path("."), "api", files=[("main.py", ".py", FASTAPI_PROVIDER)]
        )
        consumers = extractor.extract(
            Path("."), "client", files=[("UsersApi.java", ".java", JAVA_CONSUMER)]
        )
        links = match_contracts(providers + consumers)
        assert len(links) == 1
        assert links[0].provider_repo == "api"
        assert links[0].consumer_repo == "client"
