"""The Salesforce Apex REST provider and callout consumer dialects.

A provider is a ``@RestResource`` class's verb-annotated method, served under
``/services/apexrest``. A consumer is an ``HttpRequest`` whose endpoint and one
verb resolve inside one method or at its wrapper's literal call sites; anything
the file does not settle is refused rather than guessed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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

    def test_named_credential_metadata_resolves_base_url(self, tmp_path: Path) -> None:
        nc_dir = tmp_path / "namedCredentials"
        nc_dir.mkdir(parents=True)
        (nc_dir / "Weather_Endpoint.namedCredential-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<NamedCredential xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            "    <endpoint>https://api.weather.example</endpoint>\n"
            "    <label>Weather Endpoint</label>\n"
            "</NamedCredential>\n",
            encoding="utf-8",
        )
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('callout:Weather_Endpoint/weather?lat=1');\n"
            "        req.setMethod('GET');\n"
        )
        ctx = ScanContext("svc", "classes/Svc.cls", ".cls", source, {}, None, repo_root=tmp_path)
        [c] = ApexCalloutsDialect().extract(ctx)
        assert c.contract_id == "http::GET::/weather"
        assert c.meta["host"] == "api.weather.example"
        assert c.meta["path"] == "/weather"
        assert c.meta.get("base_token") is None
        assert c.symbol_name.startswith("apex-callout:GET callout:Weather_Endpoint/weather")

    def test_named_credential_metadata_under_force_app_resolves_base_url(
        self, tmp_path: Path
    ) -> None:
        nc_dir = tmp_path / "force-app" / "main" / "default" / "namedCredentials"
        nc_dir.mkdir(parents=True)
        (nc_dir / "Weather_Endpoint.namedCredential-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<NamedCredential xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            "    <endpoint>https://api.weather.example/api/v1</endpoint>\n"
            "</NamedCredential>\n",
            encoding="utf-8",
        )
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('callout:Weather_Endpoint/weather');\n"
            "        req.setMethod('GET');\n"
        )
        ctx = ScanContext("svc", "classes/Svc.cls", ".cls", source, {}, None, repo_root=tmp_path)
        [c] = ApexCalloutsDialect().extract(ctx)
        assert c.contract_id == "http::GET::/api/v1/weather"
        assert c.meta["host"] == "api.weather.example"
        assert c.meta["path"] == "/api/v1/weather"

    def test_named_credential_with_malformed_xml_falls_back_to_token(
        self, tmp_path: Path
    ) -> None:
        nc_dir = tmp_path / "namedCredentials"
        nc_dir.mkdir(parents=True)
        (nc_dir / "Weather_Endpoint.namedCredential-meta.xml").write_text(
            "<NamedCredential><broken></NamedCredential>", encoding="utf-8"
        )
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('callout:Weather_Endpoint/weather');\n"
            "        req.setMethod('GET');\n"
        )
        ctx = ScanContext("svc", "classes/Svc.cls", ".cls", source, {}, None, repo_root=tmp_path)
        [c] = ApexCalloutsDialect().extract(ctx)
        assert c.contract_id == "http::GET::/weather"
        assert c.meta["base_token"] == "Weather_Endpoint"
        assert c.meta.get("host") is None

    def test_external_credential_with_no_endpoint_falls_back_to_token(
        self, tmp_path: Path
    ) -> None:
        nc_dir = tmp_path / "namedCredentials"
        nc_dir.mkdir(parents=True)
        (nc_dir / "Weather_Endpoint.namedCredential-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<NamedCredential xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            "    <label>External Setup</label>\n"
            "    <namedCredentialType>SecuredEndpoint</namedCredentialType>\n"
            "</NamedCredential>\n",
            encoding="utf-8",
        )
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('callout:Weather_Endpoint/weather');\n"
            "        req.setMethod('GET');\n"
        )
        ctx = ScanContext("svc", "classes/Svc.cls", ".cls", source, {}, None, repo_root=tmp_path)
        [c] = ApexCalloutsDialect().extract(ctx)
        assert c.contract_id == "http::GET::/weather"
        assert c.meta["base_token"] == "Weather_Endpoint"

    def test_named_credential_with_templated_endpoint_falls_back_to_token(
        self, tmp_path: Path
    ) -> None:
        nc_dir = tmp_path / "namedCredentials"
        nc_dir.mkdir(parents=True)
        (nc_dir / "Weather_Endpoint.namedCredential-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<NamedCredential xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            "    <endpoint>https://${host}.example</endpoint>\n"
            "</NamedCredential>\n",
            encoding="utf-8",
        )
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('callout:Weather_Endpoint/weather');\n"
            "        req.setMethod('GET');\n"
        )
        ctx = ScanContext("svc", "classes/Svc.cls", ".cls", source, {}, None, repo_root=tmp_path)
        [c] = ApexCalloutsDialect().extract(ctx)
        assert c.contract_id == "http::GET::/weather"
        assert c.meta["base_token"] == "Weather_Endpoint"

    def test_named_credential_under_custom_nested_path_resolves_base_url(
        self, tmp_path: Path
    ) -> None:
        nc_dir = tmp_path / "src" / "metadata" / "namedCredentials"
        nc_dir.mkdir(parents=True)
        (nc_dir / "Weather_Endpoint.namedCredential-meta.xml").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<NamedCredential xmlns="http://soap.sforce.com/2006/04/metadata">\n'
            "    <endpoint>https://api.weather.example</endpoint>\n"
            "</NamedCredential>\n",
            encoding="utf-8",
        )
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('callout:Weather_Endpoint/weather');\n"
            "        req.setMethod('GET');\n"
        )
        ctx = ScanContext("svc", "classes/Svc.cls", ".cls", source, {}, None, repo_root=tmp_path)
        [c] = ApexCalloutsDialect().extract(ctx)
        assert c.contract_id == "http::GET::/weather"
        assert c.meta["host"] == "api.weather.example"
        assert c.meta["path"] == "/weather"
        assert c.meta.get("base_token") is None

    def test_named_credential_missing_xml_falls_back_to_token(
        self, tmp_path: Path
    ) -> None:
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('callout:Weather_Endpoint/weather');\n"
            "        req.setMethod('GET');\n"
        )
        ctx = ScanContext("svc", "classes/Svc.cls", ".cls", source, {}, None, repo_root=tmp_path)
        [c] = ApexCalloutsDialect().extract(ctx)
        assert c.contract_id == "http::GET::/weather"
        assert c.meta["base_token"] == "Weather_Endpoint"
        assert c.meta.get("host") is None

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

    def test_a_parameter_shadows_a_final_field_of_the_same_name(self) -> None:
        source = (
            "public class C {\n"
            "    static final String URL = 'https://a.example.com/x';\n"
            "    static void send(String URL) {\n"
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint(URL);\n"
            "        req.setMethod('GET');\n"
            "    }\n"
            "}\n"
        )
        assert ids(source) == set()

    def test_a_legacy_test_method_class_emits_nothing(self) -> None:
        source = (
            "public class LegacyTests {\n"
            "    static testMethod void t() {\n"
            "        HttpRequest req = new HttpRequest();\n"
            "        req.setEndpoint('https://a.example.com/x');\n"
            "        req.setMethod('GET');\n"
            "    }\n"
            "}\n"
        )
        assert ids(source) == set()

    def test_a_request_held_by_another_object_is_not_the_local(self) -> None:
        source = callout(
            "        HttpRequest req = new HttpRequest();\n"
            "        wrapper.req.setEndpoint('callout:Svc/items');\n"
            "        req.setMethod('GET');\n"
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


WRAPPER = """public class Client {
    private HttpResponse makeApiCall(String endpoint, String method) {
        HttpRequest req = new HttpRequest();
        req.setEndpoint(endpoint);
        req.setMethod(method);
        return new Http().send(req);
    }
    public void run(String endpoint, String method) {
        CALLS
    }
    MEMBERS
}
"""


def wrapper(calls: str, members: str = "") -> str:
    return WRAPPER.replace("CALLS", calls).replace("MEMBERS", members)


class TestApexCalloutWrapper:
    def test_literal_arguments_record_the_callers_line(self) -> None:
        source = wrapper("makeApiCall('callout:Billing/invoices', 'GET');")
        [c] = contracts(source)
        assert c.contract_id == "http::GET::/invoices"
        assert c.line == 9
        assert c.meta["base_token"] == "Billing"
        assert c.meta["client"] == "apex-callout"
        assert c.symbol_name == "apex-callout:GET callout:Billing/invoices"
        assert c.origin == "client_call"
        assert c.confidence == 0.75

    def test_each_caller_supplies_its_own_endpoint_and_verb(self) -> None:
        source = wrapper(
            "makeApiCall('callout:Billing/invoices', 'GET');\n"
            "        makeApiCall('https://billing.example.com/payments', 'POST');\n"
            "        makeApiCall('callout:Billing/invoices', 'GET');"
        )
        rows = contracts(source)
        assert [(c.contract_id, c.line) for c in rows] == [
            ("http::GET::/invoices", 9),
            ("http::POST::/payments", 10),
            ("http::GET::/invoices", 11),
        ]
        assert rows[1].meta["host"] == "billing.example.com"

    @pytest.mark.parametrize(
        "args",
        [
            "endpoint, 'GET'",
            "'callout:Billing/' + endpoint, 'GET'",
            "'callout:Billing/' + 'invoices', 'GET'",
            "getEndpoint(), 'GET'",
            "URL, 'GET'",
            "'callout:Billing/invoices', method",
            "'callout:Billing/invoices', 'G' + 'ET'",
            "'callout:Billing/invoices', 'INVALID'",
            "'callout:Billing/invoices'",
            "'callout:Billing/invoices', 'GET', true",
        ],
    )
    def test_non_literal_or_invalid_arguments_add_nothing(self, args: str) -> None:
        source = wrapper(
            f"makeApiCall({args});",
            "static final String URL = 'callout:Billing/invoices';",
        )
        assert ids(source) == set()

    def test_a_literal_verb_inside_the_wrapper_still_works(self) -> None:
        source = wrapper("makeApiCall('callout:Billing/invoices', method);")
        source = source.replace("req.setMethod(method)", "req.setMethod('POST')")
        assert ids(source) == {"http::POST::/invoices"}

    def test_an_unresolved_caller_does_not_hide_a_literal_caller(self) -> None:
        source = wrapper(
            "makeApiCall(endpoint, method);\n"
            "        makeApiCall('callout:Billing/invoices', 'GET');"
        )
        [c] = contracts(source)
        assert c.contract_id == "http::GET::/invoices"
        assert c.line == 10

    @pytest.mark.parametrize("prefix", ["new ", "NEW Client."])
    def test_a_constructor_with_the_same_name_is_not_a_wrapper_call(self, prefix: str) -> None:
        source = wrapper(
            f"{prefix}makeApiCall('callout:Wrong/constructor', 'GET');",
            "class makeApiCall { public makeApiCall(String endpoint, String method) {} }",
        )
        assert ids(source) == set()

    @pytest.mark.parametrize("verb", ["", "req.setMethod('DELETE');", "req.setMethod(other);"])
    def test_missing_or_conflicting_verbs_are_refused(self, verb: str) -> None:
        source = wrapper("makeApiCall('callout:Billing/invoices', 'GET');")
        if not verb:
            source = source.replace("req.setMethod(method);", "")
        else:
            source = source.replace("req.setMethod(method);", f"req.setMethod(method); {verb}")
        assert ids(source) == set()

    def test_repeated_setters_that_resolve_to_the_same_verb_are_unambiguous(self) -> None:
        source = wrapper("makeApiCall('callout:Billing/invoices', 'GET');")
        source = source.replace("req.setMethod(method);", "req.setMethod(method); req.setMethod('GET');")
        assert ids(source) == {"http::GET::/invoices"}

    @pytest.mark.parametrize("url", ["/relative/path", "callout:", "ftp://example.com/path"])
    def test_unusable_literal_endpoints_are_refused(self, url: str) -> None:
        assert ids(wrapper(f"makeApiCall('{url}', 'GET');")) == set()

    def test_a_call_inside_a_nested_block_uses_the_enclosing_class(self) -> None:
        source = wrapper("if (endpoint == null) { makeApiCall('callout:Billing/invoices', 'GET'); }")
        assert ids(source) == {"http::GET::/invoices"}

    def test_a_wrapper_inside_an_inner_class_has_its_own_call_sites(self) -> None:
        source = "class Outer {\n" + wrapper("this.makeApiCall('callout:Billing/invoices', 'GET');") + "}\n"
        assert ids(source) == {"http::GET::/invoices"}

    def test_a_verb_parameter_named_after_a_verb_still_requires_a_literal(self) -> None:
        source = wrapper("makeApiCall('callout:Billing/invoices', method);")
        source = source.replace("String method)", "String GET)").replace("setMethod(method)", "setMethod(GET)")
        assert ids(source) == set()

    def test_a_field_initializer_call_is_not_an_overload(self) -> None:
        source = wrapper(
            "makeApiCall('callout:Billing/invoices', 'GET');",
            "HttpResponse initial = makeApiCall('callout:Billing/payments', 'POST');",
        )
        assert ids(source) == {"http::GET::/invoices", "http::POST::/payments"}

    def test_annotations_and_case_insensitive_parameters(self) -> None:
        source = wrapper("makeApiCall('callout:Billing/invoices', 'GET');")
        source = source.replace(
            "private HttpResponse", "@AuraEnabled(cacheable=true) public static HttpResponse"
        )
        source = source.replace("setEndpoint(endpoint)", "setEndpoint(ENDPOINT)")
        source = source.replace("setMethod(method)", "setMethod(METHOD)")
        assert ids(source) == {"http::GET::/invoices"}

    @pytest.mark.parametrize(
        "member",
        [
            "void makeApiCall(String endpoint) {}",
            "void MAKEAPICALL(String endpoint, Integer method) {}",
            "abstract HttpResponse makeApiCall(String endpoint);",
        ],
    )
    def test_overloads_are_refused(self, member: str) -> None:
        assert ids(wrapper("makeApiCall('callout:Billing/invoices', 'GET');", member)) == set()

    def test_a_forwarded_parameter_is_not_traced_a_second_level(self) -> None:
        source = wrapper(
            "forward('callout:Billing/invoices', 'GET');",
            "void forward(String endpoint, String method) { makeApiCall(endpoint, method); }",
        )
        assert ids(source) == set()

    @pytest.mark.parametrize("receiver", ["", "this.", "Client.", "THIS . "])
    def test_unambiguous_same_class_receivers(self, receiver: str) -> None:
        source = wrapper(f"{receiver}MAKEAPICALL('callout:Billing/invoices', 'GET');")
        assert ids(source) == {"http::GET::/invoices"}

    @pytest.mark.parametrize(
        "receiver", ["other.", "OtherClass.", "holder.Client.", "getClient()."]
    )
    def test_calls_on_other_objects_are_refused(self, receiver: str) -> None:
        assert ids(wrapper(f"{receiver}makeApiCall('callout:Billing/invoices', 'GET');")) == set()

    def test_callers_and_overloads_in_other_classes_are_ignored(self) -> None:
        source = wrapper(
            "makeApiCall('callout:Billing/invoices', 'GET');",
            "class Inner { void run() { makeApiCall('callout:Wrong/inner', 'DELETE'); } "
            "void makeApiCall(String endpoint) {} }",
        )
        source += (
            "class Other { void run() { Client.makeApiCall('callout:Wrong/outer', 'POST'); } }"
        )
        assert ids(source) == {"http::GET::/invoices"}

    def test_comments_and_strings_do_not_count_as_call_sites(self) -> None:
        source = wrapper(
            "// makeApiCall('callout:Wrong/comment', 'GET');\n"
            r"String example = 'makeApiCall(\'callout:Wrong/string\', \'GET\');';"
        )
        assert ids(source) == set()

    @pytest.mark.parametrize(
        "assignment",
        ["endpoint = 'callout:Wrong/path';", "endpoint += '/more';", "method = 'DELETE';"],
    )
    def test_reassigned_parameters_are_not_read_from_the_caller(self, assignment: str) -> None:
        source = wrapper("makeApiCall('callout:Billing/invoices', 'GET');")
        source = source.replace("HttpRequest req", f"{assignment}\n        HttpRequest req")
        assert ids(source) == set()

    def test_argument_positions_and_nested_arguments(self) -> None:
        source = wrapper("makeApiCall(buildOptions('a', 'b'), 'GET', 'callout:Billing/invoices');")
        source = source.replace(
            "String endpoint, String method",
            "Map<String, String> options, String method, String endpoint",
            1,
        )
        assert ids(source) == {"http::GET::/invoices"}


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
