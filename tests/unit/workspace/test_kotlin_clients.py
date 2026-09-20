"""The Kotlin HTTP consumer dialect: Ktor client calls.

The claim under test is that a Ktor call site is read only when the file names
Ktor's client package and the URL settles inside the file: a literal, an
interpolated string over a folded constant, a ``Url(...)`` wrapper, or the
``url(...)`` builder inside a request block. A block with no URL in it, a
``request`` with no verb, the four-argument ``url`` overload and a call on a
type name are refused rather than guessed.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.contracts import match_contracts
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.extractors.http.fastapi import FastApiDialect
from repowise.core.workspace.extractors.http.kotlin_clients import (
    KotlinClientsDialect,
    ktor_calls,
)

LITERALS = """package com.example.sync

import io.ktor.client.*
import io.ktor.client.request.*

const val TEST_SERVER = "http://127.0.0.1:8080"

suspend fun run(client: HttpClient, id: String) {
    client.get("$TEST_SERVER/echo_query?a=b")
    client.get("http://www.google.com/search").bodyAsText()
    client.post("/api/accounts")
    client.head("/api/accounts/${id}/status")
    client.delete(Url("/api/accounts/legacy"))
}
"""

BLOCKS = """package com.example.sync

import io.ktor.client.*
import io.ktor.client.request.*

suspend fun run(client: HttpClient, serverPort: Int, base: String) {
    client.post {
        setBody("x")
        url(path = "/widget", port = serverPort)
    }
    client.get { url("http://svc.example.com/widget/list") }
    client.put { url { takeFrom("/widget/replace") } }
}
"""

REQUESTS = """package com.example.sync

import io.ktor.client.*
import io.ktor.client.request.*

suspend fun run(client: HttpClient) {
    client.request("/api/accounts") { method = HttpMethod.Post }
    client.request {
        url("/api/accounts/search")
        method = HttpMethod.Get
    }
}
"""

REFUSALS = """package com.example.sync

import io.ktor.client.*
import io.ktor.client.request.*
import java.nio.file.Paths

suspend fun run(client: HttpClient, target: String, port: Int) {
    // A default-request block: the URL lives on the client, not here.
    client.get {
        header("X-Trace", "1")
    }
    // The verb is decided at run time.
    client.request("/api/accounts") { header("X-Trace", "1") }
    // The path is positional among a scheme, a host and a port.
    client.get { url("https", "svc.example.com", port, "/widget") }
    // A parameter: this file never says what it holds.
    client.get(target)
    // Not a request at all.
    Paths.get("jvm/test-resources/public.zip")
    client.get("lookup-key")
}

/**
 * Documentation, not a call site:
 *
 * val response = client.get("https://ktor.io/docs/example")
 */
fun documented() = Unit
"""

RELATIVE_AND_ROOTED = """package com.example.sync

import io.ktor.client.*
import io.ktor.client.request.*

const val BASE = "https://svc.example.com"

suspend fun run(client: HttpClient) {
    client.get("api/v1/users")
    client.get("/api/v1/users")
    client.get("$BASE/api/v1/orders")
}
"""

NOT_KTOR = """package com.example.cache

class Cache(private val entries: Map<String, String>) {
    fun read(): String? = entries.get("/api/accounts")
}
"""

PROVIDER_SOURCE = '''"""Accounts service."""

from fastapi import FastAPI

app = FastAPI()


@app.post("/api/accounts")
def create_account():
    return {}
'''


def contracts(source: str, rel_path: str = "src/Sync.kt") -> list:
    ctx = ScanContext("portal", rel_path, ".kt", source, {}, None)
    return KotlinClientsDialect().extract(ctx)


def ids(source: str, rel_path: str = "src/Sync.kt") -> list[str]:
    return sorted(c.contract_id for c in contracts(source, rel_path))


class TestKotlinClientsRecognition:
    def test_a_verb_call_carries_its_url_argument_and_verb(self):
        rows = [(m.method, m.url) for m in ktor_calls(LITERALS)]
        assert ("GET", '"$TEST_SERVER/echo_query?a=b"') in rows
        assert ("POST", '"/api/accounts"') in rows
        assert ("HEAD", '"/api/accounts/${id}/status"') in rows
        assert ("DELETE", 'Url("/api/accounts/legacy")') in rows

    def test_a_block_call_carries_the_url_the_block_builds(self):
        rows = [(m.method, m.url) for m in ktor_calls(BLOCKS)]
        assert rows == [
            ("POST", '"/widget"'),
            ("GET", '"http://svc.example.com/widget/list"'),
            ("PUT", '"/widget/replace"'),
        ]

    def test_a_request_call_takes_its_verb_from_the_block(self):
        rows = [(m.method, m.url) for m in ktor_calls(REQUESTS)]
        assert rows == [
            ("POST", '"/api/accounts"'),
            ("GET", '"/api/accounts/search"'),
        ]

    def test_every_row_is_labelled_ktor(self):
        assert {m.client for m in ktor_calls(LITERALS)} == {"ktor"}


class TestKotlinClientsContracts:
    def test_a_literal_url_becomes_a_contract_id(self):
        assert "http::POST::/api/accounts" in ids(LITERALS)

    def test_an_interpolated_parameter_collapses(self):
        assert "http::HEAD::/api/accounts/{param}/status" in ids(LITERALS)

    def test_a_constant_this_file_assigns_folds_into_the_url(self):
        row = next(c for c in contracts(LITERALS) if c.contract_id.endswith("/echo_query"))
        assert row.contract_id == "http::GET::/echo_query"
        assert row.meta["host"] == "127.0.0.1"
        assert row.meta["client"] == "ktor"

    def test_a_base_declared_elsewhere_is_stripped_and_recorded(self):
        source = """import io.ktor.client.request.*

suspend fun f(client: HttpClient) {
    client.get("$TEST_SERVER/echo_query")
}
"""
        row = contracts(source)[0]
        assert row.contract_id == "http::GET::/echo_query"
        assert row.meta["base_stripped"] is True
        assert row.meta["base_token"] == "TEST_SERVER"

    def test_an_absolute_url_records_its_host(self):
        row = next(c for c in contracts(LITERALS) if c.contract_id.endswith("/search"))
        assert row.meta["host"] == "www.google.com"
        assert row.contract_id == "http::GET::/search"

    def test_a_url_wrapper_is_unwrapped(self):
        assert "http::DELETE::/api/accounts/legacy" in ids(LITERALS)

    def test_a_block_built_url_becomes_a_contract(self):
        assert ids(BLOCKS) == [
            "http::GET::/widget/list",
            "http::POST::/widget",
            "http::PUT::/widget/replace",
        ]

    def test_a_request_block_verb_becomes_the_contract_method(self):
        assert ids(REQUESTS) == [
            "http::GET::/api/accounts/search",
            "http::POST::/api/accounts",
        ]

    def test_the_receiver_is_ambiguous_so_confidence_is_lowered(self):
        assert {c.confidence for c in contracts(LITERALS)} == {0.65}


class TestKotlinClientsRefusals:
    def test_a_file_without_ktors_client_package_emits_nothing(self):
        assert contracts(NOT_KTOR, "src/Cache.kt") == []

    def test_every_refused_shape_emits_nothing(self):
        assert contracts(REFUSALS) == []

    def test_a_block_with_no_url_emits_nothing(self):
        source = REFUSALS[: REFUSALS.index("// The verb is decided")]
        assert list(ktor_calls(source)) == []

    def test_a_request_with_no_method_emits_nothing(self):
        source = 'import io.ktor.client.request.*\nfun f() { client.request("/a") { } }\n'
        assert list(ktor_calls(source)) == []

    def test_the_four_argument_url_overload_emits_nothing(self):
        source = (
            'import io.ktor.client.request.*\nfun f() { client.get { url("https", h, p, "/a") } }\n'
        )
        assert list(ktor_calls(source)) == []

    def test_a_call_on_a_type_name_emits_nothing(self):
        source = (
            'import io.ktor.client.request.*\nfun f() = Paths.get("jvm/test-resources/a.zip")\n'
        )
        assert list(ktor_calls(source)) == []

    def test_a_documented_example_emits_nothing(self):
        source = 'import io.ktor.client.request.*\n/**\n * val r = client.get("https://ktor.io/docs")\n */\nfun f() = Unit\n'
        assert list(ktor_calls(source)) == []

    def test_a_slash_free_argument_emits_nothing(self):
        source = 'import io.ktor.client.request.*\nfun f() { client.get("lookup-key") }\n'
        assert contracts(source) == []

    def test_a_relative_path_emits_nothing_while_a_rooted_one_emits(self):
        assert [m.url for m in ktor_calls(RELATIVE_AND_ROOTED)] == [
            '"api/v1/users"',
            '"/api/v1/users"',
            '"$BASE/api/v1/orders"',
        ]
        assert ids(RELATIVE_AND_ROOTED) == [
            "http::GET::/api/v1/orders",
            "http::GET::/api/v1/users",
        ]


class TestKotlinClientsCrossRepoLink:
    def test_a_ktor_call_links_to_a_python_route(self):
        provider_extractor = HttpExtractor()
        provider_extractor.provider_dialects = (FastApiDialect(),)
        provider_extractor.consumer_dialects = ()
        providers = provider_extractor.extract(
            Path("."), "accounts", files=[("app/main.py", ".py", PROVIDER_SOURCE)]
        )

        consumer_extractor = HttpExtractor()
        consumer_extractor.provider_dialects = ()
        consumer_extractor.consumer_dialects = (KotlinClientsDialect(),)
        consumers = consumer_extractor.extract(
            Path("."), "portal", files=[("src/Sync.kt", ".kt", REQUESTS)]
        )

        links = match_contracts(providers + consumers)
        assert [(link.provider_repo, link.consumer_repo, link.contract_id) for link in links] == [
            ("accounts", "portal", "http::POST::/api/accounts")
        ]
