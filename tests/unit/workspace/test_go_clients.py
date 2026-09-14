"""The Go ``net/http`` consumer dialect.

The claim under test is that a verb is read from the call and never invented,
and that a URL argument is resolved as an expression or refused, so a call whose
target the file does not settle produces no contract at all.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.contracts import match_contracts
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.extractors.http.fastapi import FastApiDialect
from repowise.core.workspace.extractors.http.go_clients import GoClientsDialect, net_http_calls

PACKAGE_VERBS = """package main

import "net/http"

func run() {
	http.Get("https://api.example.com/v1/items")
	http.Head("https://api.example.com/v1/items")
	http.Post("https://api.example.com/v1/items", "application/json", body)
	http.PostForm("https://api.example.com/v1/forms", values)
}
"""

NEW_REQUEST = """package main

import "net/http"

func run(ctx context.Context) {
	a, _ := http.NewRequest(http.MethodGet, "https://api.example.com/v1/items", nil)
	b, _ := http.NewRequest("DELETE", "https://api.example.com/v1/items/42", nil)
	c, _ := http.NewRequestWithContext(ctx, http.MethodPut, "https://api.example.com/v1/items/42", body)
}
"""

CONSTANT_URL = """package main

const verifyURL = "https://api.example.com/v1/verify"

func run() {
	http.Post(verifyURL, "application/json", body)
}
"""

SHORT_DECL_URL = """package main

func run() {
	endpoint := "https://api.example.com/v1/ping"
	http.Get(endpoint)
}
"""

SPRINTF_URL = """package main

func run(base, id string) {
	http.Get(fmt.Sprintf("%s/v1/items/%s", base, id))
}
"""

CONCAT_URL = """package main

func run(base string) {
	http.Get(base + "/v1/items")
}
"""

RAW_STRING_URL = """package main

func run() {
	http.Get(`https://api.example.com/v1/raw`)
}
"""

MULTILINE_CALL = """package main

func run(ctx context.Context) {
	req, err := http.NewRequestWithContext(
		ctx,
		http.MethodPost,
		"https://api.example.com/v1/token",
		strings.NewReader(post.Encode()),
	)
}
"""

RUNTIME_METHOD = """package main

func run(o options, url string) {
	req, err := http.NewRequest(o.Method, "https://api.example.com/v1/items", o.BodyReader())
}
"""

HELPER_URL = """package main

func run() {
	requestURL, err := formatPypiRegistryURL(baseURL, name, version)
	req, err := http.NewRequest(http.MethodGet, requestURL, nil)
}
"""

SPRINTF_INTO_VARIABLE = """package main

func run(proxy, name string) {
	u := fmt.Sprintf("%s/%s/@v/latest.zip", proxy, name)
	resp, err := http.Get(u)
}
"""

REASSIGNED_URL = """package main

func run(flag bool) {
	endpoint := "https://api.example.com/v1/a"
	endpoint = "https://api.example.com/v1/b"
	http.Get(endpoint)
}
"""

COMMENT_EXAMPLE = """package main

// Sign applies a signature to a request.
//
//	Example usage:
//
//	req, _ := http.NewRequest("GET", "https://my-bucket.example.com/my-object", nil)
func Sign(req *http.Request) error {
	return nil
}
"""

CLIENT_RECEIVER = """package main

func run() {
	client := &http.Client{}
	client.Get("https://api.example.com/v1/items")
}
"""

BARE_ROOT = """package main

func run() {
	req, _ := http.NewRequest(http.MethodGet, "/", nil)
}
"""

MAP_LOOKUP = """package main

func run(c cache, client apiClient) {
	v := c.Get("some-key")
	client.Post("/v1/items", body)
}
"""


def _ctx(content: str, rel_path: str = "client.go") -> ScanContext:
    return ScanContext("repo", rel_path, ".go", content, {}, None)


def _extract(content: str) -> list:
    return GoClientsDialect().extract(_ctx(content))


def _ids(content: str) -> list[str]:
    return [c.contract_id for c in _extract(content)]


class TestGoClientsRecognition:
    def test_package_verbs_carry_their_own_method(self) -> None:
        rows = list(net_http_calls(PACKAGE_VERBS))
        assert [r.method for r in rows] == ["GET", "HEAD", "POST", "POST"]
        assert {r.client for r in rows} == {"net/http"}

    def test_new_request_reads_the_method_argument(self) -> None:
        rows = list(net_http_calls(NEW_REQUEST))
        assert [r.method for r in rows] == ["GET", "DELETE", "PUT"]

    def test_new_request_url_is_the_second_argument(self) -> None:
        rows = list(net_http_calls(NEW_REQUEST))
        assert rows[0].url == '"https://api.example.com/v1/items"'

    def test_with_context_url_is_the_third_argument(self) -> None:
        rows = list(net_http_calls(NEW_REQUEST))
        assert rows[2].url == '"https://api.example.com/v1/items/42"'

    def test_arguments_split_across_lines(self) -> None:
        rows = list(net_http_calls(MULTILINE_CALL))
        assert [(r.method, r.url) for r in rows] == [("POST", '"https://api.example.com/v1/token"')]


class TestGoClientsContracts:
    def test_package_verbs_emit_one_contract_each(self) -> None:
        assert _ids(PACKAGE_VERBS) == [
            "http::GET::/v1/items",
            "http::HEAD::/v1/items",
            "http::POST::/v1/items",
            "http::POST::/v1/forms",
        ]

    def test_new_request_forms_emit_their_argument_verb(self) -> None:
        assert _ids(NEW_REQUEST) == [
            "http::GET::/v1/items",
            "http::DELETE::/v1/items/42",
            "http::PUT::/v1/items/42",
        ]

    def test_meta_names_the_client_and_the_host(self) -> None:
        (contract,) = _extract(CONSTANT_URL)
        assert contract.meta["client"] == "net/http"
        assert contract.meta["host"] == "api.example.com"
        assert contract.confidence == 0.75

    def test_package_constant_folds(self) -> None:
        assert _ids(CONSTANT_URL) == ["http::POST::/v1/verify"]

    def test_short_declaration_folds(self) -> None:
        assert _ids(SHORT_DECL_URL) == ["http::GET::/v1/ping"]

    def test_sprintf_template_collapses_to_parameters(self) -> None:
        (contract,) = _extract(SPRINTF_URL)
        assert contract.contract_id == "http::GET::/v1/items/{param}"
        assert contract.meta["base_stripped"] is True

    def test_concatenated_base_is_stripped(self) -> None:
        (contract,) = _extract(CONCAT_URL)
        assert contract.contract_id == "http::GET::/v1/items"
        assert contract.meta["base_token"] == "base"

    def test_raw_string_literal_resolves(self) -> None:
        assert _ids(RAW_STRING_URL) == ["http::GET::/v1/raw"]

    def test_line_is_the_call_site(self) -> None:
        (contract,) = _extract(CONSTANT_URL)
        assert contract.line == 6


class TestGoClientsRefusals:
    def test_runtime_method_variable_emits_nothing(self) -> None:
        assert _extract(RUNTIME_METHOD) == []

    def test_url_from_a_helper_call_emits_nothing(self) -> None:
        assert _extract(HELPER_URL) == []

    def test_variable_bound_once_to_a_format_call_folds(self) -> None:
        (c,) = _extract(SPRINTF_INTO_VARIABLE)
        assert c.contract_id == "http::GET::/{param}/@v/latest.zip"
        assert c.meta.get("base_stripped") is True

    def test_twice_assigned_name_emits_nothing(self) -> None:
        assert _extract(REASSIGNED_URL) == []

    def test_example_in_a_doc_comment_emits_nothing(self) -> None:
        assert _extract(COMMENT_EXAMPLE) == []

    def test_a_bare_root_path_emits_nothing(self) -> None:
        # A test server's `"/"` names no route to match on.
        assert _extract(BARE_ROOT) == []

    def test_client_receiver_call_emits_nothing(self) -> None:
        assert _extract(CLIENT_RECEIVER) == []

    def test_lookups_on_an_untyped_receiver_emit_nothing(self) -> None:
        assert _extract(MAP_LOOKUP) == []


GO_CONSUMER = """package main

func fetchUser(base, id string) {
	http.Get(fmt.Sprintf("%s/users/%s", base, id))
}
"""

FASTAPI_PROVIDER = """from fastapi import FastAPI

app = FastAPI()


@app.get("/users/{user_id}")
def read_user(user_id: str):
    return {"id": user_id}
"""


class TestGoClientsCrossRepoLink:
    def test_go_consumer_links_to_a_python_provider(self) -> None:
        extractor = HttpExtractor()
        extractor.provider_dialects = (FastApiDialect(),)
        extractor.consumer_dialects = (GoClientsDialect(),)
        providers = extractor.extract(
            Path("."), "api", files=[("main.py", ".py", FASTAPI_PROVIDER)]
        )
        consumers = extractor.extract(
            Path("."), "worker", files=[("client.go", ".go", GO_CONSUMER)]
        )
        links = match_contracts(providers + consumers)
        assert len(links) == 1
        assert links[0].provider_repo == "api"
        assert links[0].consumer_repo == "worker"
