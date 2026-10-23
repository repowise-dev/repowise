"""The Salesforce Apex REST provider and callout consumer dialects.

A provider is a ``@RestResource`` class's verb-annotated method, served under
``/services/apexrest``. A consumer is an ``HttpRequest`` whose endpoint and one
verb are both literal inside one method; anything the file does not settle is
refused rather than guessed.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.framework_routes import apex_rest_routes
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.extractors.http.apex_callouts import ApexCalloutsDialect
from repowise.core.workspace.extractors.http.apex_rest import ApexRestDialect
from repowise.core.workspace.extractors.http.js_clients import JsClientsDialect
from repowise.core.workspace.matching import match_contracts

REST_RESOURCE = """/**
 * @RestResource(urlMapping='/commented/*') is documentation.
 */
@RestResource(urlMapping='/integration-service/*')
global with sharing class CustomRestEndpoint {
    @HttpGet
    global static String getRecord() { return ''; }

    // @HttpPut is not on any method
    @HttpDelete
    global static void deleteRecord() { }

    @httpPost global static void create() { }
}
"""


def contracts(content: str, rel_path: str = "classes/Svc.cls") -> list:
    ctx = ScanContext("svc", rel_path, ".cls", content, {}, None)
    return [*ApexRestDialect().extract(ctx), *ApexCalloutsDialect().extract(ctx)]


def ids(content: str) -> set[str]:
    return {c.contract_id for c in contracts(content)}


class TestApexRestProvider:
    def test_each_verb_method_is_served_under_the_apex_rest_base(self) -> None:
        assert ids(REST_RESOURCE) == {
            "http::GET::/services/apexrest/integration-service/{param}",
            "http::DELETE::/services/apexrest/integration-service/{param}",
            "http::POST::/services/apexrest/integration-service/{param}",
        }

    def test_rows_carry_the_annotation_line_and_framework(self) -> None:
        get = next(c for c in contracts(REST_RESOURCE) if c.meta["method"] == "GET")
        assert get.role == "provider"
        assert get.line == 6
        assert get.meta["framework"] == "apex-rest"

    def test_a_wildcard_mid_path_is_one_parameter_segment(self) -> None:
        source = (
            "@RestResource(urlMapping='/Account/*/contacts')\n"
            "global class Contacts { @HttpGet global static void g() {} }\n"
        )
        assert ids(source) == {"http::GET::/services/apexrest/account/{param}/contacts"}

    def test_annotations_read_without_regard_to_case(self) -> None:
        source = (
            "@restResource(UrlMapping='/whatsapp')\n"
            "global class W { @HTTPGET global static void g() {} }\n"
        )
        assert ids(source) == {"http::GET::/services/apexrest/whatsapp"}

    def test_a_mapping_that_is_not_a_literal_serves_nothing(self) -> None:
        source = (
            "@RestResource(urlMapping=Paths.ACCOUNTS)\n"
            "global class A { @HttpGet global static void g() {} }\n"
        )
        assert ids(source) == set()
        # The graph side still binds the handler: the platform calls it.
        assert [r.path for r in apex_rest_routes(source)] == [None]

    def test_verb_annotations_without_a_resource_serve_nothing(self) -> None:
        source = "global class A { @HttpGet global static void g() {} }\n"
        assert ids(source) == set()

    def test_a_resource_only_in_a_comment_serves_nothing(self) -> None:
        source = (
            "// @RestResource(urlMapping='/x/*')\n"
            "global class A { @HttpGet global static void g() {} }\n"
        )
        assert ids(source) == set()


def callout(body: str, members: str = "") -> str:
    return (
        "public with sharing class Client {\n"
        f"{members}"
        "    public static HttpResponse call(String id) {\n"
        f"{body}"
        "        return new Http().send(req);\n"
        "    }\n"
        "}\n"
    )


class TestApexCalloutConsumer:
    def test_a_literal_absolute_endpoint_records_its_host(self) -> None:
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint(\n"
            "            'https://www.googleapis.com/books/v1/volumes?q=salesforce'\n"
            "        );\n"
            "        req.setMethod('GET');\n"
        )
        [c] = contracts(source)
        assert c.contract_id == "http::GET::/books/v1/volumes"
        assert c.role == "consumer"
        assert c.line == 4
        assert c.meta["host"] == "www.googleapis.com"
        assert c.meta["client"] == "apex-callout"

    def test_a_named_credential_becomes_the_base_token(self) -> None:
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('callout:Weather_Endpoint/weather?lat=1&date=' + id);\n"
            "        req.setMethod('GET');\n"
        )
        [c] = contracts(source)
        assert c.contract_id == "http::GET::/weather"
        assert c.meta["base_token"] == "Weather_Endpoint"
        assert c.symbol_name.startswith("apex-callout:GET callout:Weather_Endpoint/weather")

    def test_the_verb_may_be_set_before_the_endpoint(self) -> None:
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setMethod('POST');\n"
            "        req.setEndpoint('callout:Orders/v1/orders/' + id);\n"
        )
        assert ids(source) == {"http::POST::/v1/orders/{param}"}

    def test_a_same_file_constant_folds(self) -> None:
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint(API_URL);\n"
            "        req.setMethod('POST');\n",
            members="    private static final String API_URL = 'https://api.sendgrid.com/api/mail.send';\n",
        )
        assert ids(source) == {"http::POST::/api/mail.send"}

    def test_query_values_built_from_expressions_leave_the_path(self) -> None:
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('https://repair.example.com/api/score?id=' + reqs[0].custId);\n"
            "        req.setMethod('GET');\n"
        )
        assert ids(source) == {"http::GET::/api/score"}

    def test_a_local_assigned_once_in_the_method_folds(self) -> None:
        source = callout(
            "        String url = 'https://maps.example.com/api/tz/json?loc=' + reqs[0].lat;\n"
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint(url);\n"
            "        req.setMethod('GET');\n"
        )
        assert ids(source) == {"http::GET::/api/tz/json"}

    def test_a_local_extended_after_assignment_emits_nothing(self) -> None:
        source = callout(
            "        String url = 'https://maps.example.com/api';\n"
            "        url += suffix;\n"
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint(url);\n"
            "        req.setMethod('GET');\n"
        )
        assert ids(source) == set()

    def test_a_parameter_never_reads_another_method_s_local(self) -> None:
        # `endpoint` is assigned once in the file, but in the caller; the
        # request is sent to `url`, so folding by name would drop a segment.
        source = (
            "public class C {\n"
            "    static void run(String key) {\n"
            "        String endpoint = 'https://api.example.com/messages/';\n"
            "        String url = endpoint + key;\n"
            "        send(url);\n"
            "    }\n"
            "    static void send(String endpoint) {\n"
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint(endpoint);\n"
            "        req.setMethod('POST');\n"
            "    }\n"
            "}\n"
        )
        assert ids(source) == set()

    def test_a_request_taken_as_a_parameter_counts(self) -> None:
        source = (
            "public class C {\n"
            "    static void prepare(HttpRequest req) {\n"
            "        req.setEndpoint('callout:Svc/items');\n"
            "        req.setMethod('GET');\n"
            "    }\n"
            "}\n"
        )
        assert ids(source) == {"http::GET::/items"}

    def test_an_anonymous_script_is_one_scope(self) -> None:
        source = (
            "HttpRequest req = new HttpRequest();\n"
            "req.setEndpoint('https://jsonplaceholder.typicode.com/todos/1');\n"
            "req.setMethod('GET');\n"
        )
        ctx = ScanContext("svc", "scripts/sample.apex", ".apex", source, {}, None)
        assert {c.contract_id for c in ApexCalloutsDialect().extract(ctx)} == {
            "http::GET::/todos/1"
        }

    def test_a_variable_endpoint_emits_nothing(self) -> None:
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint(endpoint);\n"
            "        req.setMethod('GET');\n"
        )
        assert ids(source) == set()

    def test_a_variable_named_credential_emits_nothing(self) -> None:
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('callout:' + this.credential + '/users/current');\n"
            "        req.setMethod('GET');\n"
        )
        assert ids(source) == set()

    def test_a_variable_host_emits_nothing(self) -> None:
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('https://' + host + '/v1/users');\n"
            "        req.setMethod('GET');\n"
        )
        assert ids(source) == set()

    def test_a_verb_held_in_a_variable_emits_nothing(self) -> None:
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('callout:Svc/items');\n"
            "        req.setMethod(method);\n"
        )
        assert ids(source) == set()

    def test_two_verbs_for_one_request_emit_nothing(self) -> None:
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('callout:Svc/items');\n"
            "        if (id == null) { req.setMethod('GET'); } else { req.setMethod('DELETE'); }\n"
        )
        assert ids(source) == set()

    def test_a_verb_set_in_another_method_is_not_borrowed(self) -> None:
        source = (
            "public class C {\n"
            "    HttpRequest build() {\n"
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('callout:Svc/items');\n"
            "        return req;\n"
            "    }\n"
            "    void other() {\n"
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setMethod('GET');\n"
            "    }\n"
            "}\n"
        )
        assert ids(source) == set()

    def test_a_receiver_that_is_not_a_request_emits_nothing(self) -> None:
        source = callout(
            "        Callable req = new Fake();\n"
            "        req.setEndpoint('callout:Svc/items');\n"
            "        req.setMethod('GET');\n"
        )
        assert ids(source) == set()

    def test_a_commented_out_call_emits_nothing(self) -> None:
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        // req.setEndpoint('callout:Svc/items');\n"
            "        req.setMethod('GET');\n"
        )
        assert ids(source) == set()

    def test_a_test_class_mock_emits_nothing(self) -> None:
        source = (
            "@isTest\n"
            "global class Mock implements HttpCalloutMock {\n"
            "    global HttpResponse respond(HttpRequest req) {\n"
            "        req.setEndpoint('https://salesforce.com/services/data/v55.0/x');\n"
            "        req.setMethod('GET');\n"
            "        return null;\n"
            "    }\n"
            "}\n"
        )
        assert ids(source) == set()


JS_CONSUMER = """export async function load(id) {
  return fetch(`/services/apexrest/integration-service/${id}`);
}
"""


class TestApexCrossRepoLink:
    def test_a_js_consumer_links_to_an_apex_rest_resource(self) -> None:
        extractor = HttpExtractor()
        extractor.provider_dialects = (ApexRestDialect(),)
        extractor.consumer_dialects = (JsClientsDialect(),)
        providers = extractor.extract(
            Path("."), "org", files=[("classes/Svc.cls", ".cls", REST_RESOURCE)]
        )
        consumers = extractor.extract(
            Path("."), "portal", files=[("src/load.js", ".js", JS_CONSUMER)]
        )
        links = match_contracts(providers + consumers)
        assert [(link.provider_repo, link.consumer_repo) for link in links] == [("org", "portal")]

    def test_the_registered_extractor_reads_apex_files(self) -> None:
        assert {".cls", ".trigger", ".apex"} <= HttpExtractor.source_extensions()
