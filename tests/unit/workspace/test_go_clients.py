"""The Go ``net/http`` consumer dialect.

The claim under test is that a verb is read from the call and never invented,
and that a URL argument is resolved as an expression or refused, so a call whose
target the file does not settle produces no contract at all.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.extractors.http.fastapi import FastApiDialect
from repowise.core.workspace.extractors.http.go_clients import GoClientsDialect, net_http_calls
from repowise.core.workspace.extractors.http.paths import is_unusable_consumer_path
from repowise.core.workspace.matching import annotate_consumer_targets, match_contracts
from repowise.core.workspace.origins import LinkStrategy

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


# ---------------------------------------------------------------------------
# A call to a service's root path (issue #4565)
# ---------------------------------------------------------------------------

ROOT_LITERAL_HOST = """package main

func chat(body io.Reader) {
	url := "http://shoppingassistantservice:80"
	req, err := http.NewRequest(http.MethodPost, url, body)
}
"""

ROOT_LOCALHOST = """package main

func chat(body io.Reader) {
	req, err := http.NewRequest(http.MethodPost, "http://localhost:8080/", body)
}
"""

# Online Boutique: the struct field is filled from the environment in one file
# (the receiver there is `svc`) and read in another (the receiver is `fe`).
MAIN_GO = """package main

func main() {
	svc := new(frontendServer)
	mustMapEnv(&svc.productCatalogSvcAddr, "PRODUCT_CATALOG_SERVICE_ADDR")
	mustMapEnv(&svc.shoppingAssistantSvcAddr, "SHOPPING_ASSISTANT_SERVICE_ADDR")
}
"""

HANDLERS_GO = """package main

func (fe *frontendServer) chatBotHandler(w http.ResponseWriter, r *http.Request) {
	url := "http://" + fe.shoppingAssistantSvcAddr
	req, err := http.NewRequest(http.MethodPost, url, r.Body)
}
"""

FIELD_PLUS_PATH = """package main

func (fe *frontendServer) chat(r *http.Request) {
	url := "http://" + fe.shoppingAssistantSvcAddr + "/chat"
	req, err := http.NewRequest(http.MethodPost, url, r.Body)
}
"""

UNBOUND_VARIABLE_ROOT = """package main

func chat(base string, body io.Reader) {
	req, err := http.NewRequest(http.MethodPost, base + "/", body)
}
"""

FIELD_WITH_PORT = """package main

func (fe *frontendServer) chat(r *http.Request) {
	url := "http://" + fe.shoppingAssistantSvcAddr + ":8080"
	req, err := http.NewRequest(http.MethodPost, url, r.Body)
}
"""

OTHER_BINDING_GO = """package main

func main() {
	svc := new(otherServer)
	mustMapEnv(&svc.shoppingAssistantSvcAddr, "ASSISTANT_ADDR")
}
"""

COMMENTED_BINDING_GO = """package main

func main() {
	// mustMapEnv(&svc.shoppingAssistantSvcAddr, "SHOPPING_ASSISTANT_SERVICE_ADDR")
}
"""

ASSISTANT_PROVIDER = """from fastapi import FastAPI

app = FastAPI()


@app.post("/")
def chat():
    return {}
"""

ENV_KEY = "SHOPPING_ASSISTANT_SERVICE_ADDR"


def _scan(files: dict[str, str], repo: str = "frontend") -> list:
    """Every consumer contract a repo's Go files yield, bindings collected repo-wide."""
    extractor = HttpExtractor()
    extractor.provider_dialects = ()
    extractor.consumer_dialects = (GoClientsDialect(),)
    return extractor.extract(
        Path("."), repo, files=[(name, ".go", text) for name, text in files.items()]
    )


class TestGoRootCallKeptByItsTarget:
    def test_literal_host_root_call_is_kept(self) -> None:
        (c,) = _extract(ROOT_LITERAL_HOST)
        assert c.contract_id == "http::POST::/"
        assert c.meta["host"] == "shoppingassistantservice"

    def test_root_call_to_localhost_is_refused(self) -> None:
        # The local machine names no service, so `/` would match any of them.
        assert _extract(ROOT_LOCALHOST) == []

    def test_env_bound_field_yields_a_base_token(self) -> None:
        (c,) = _scan({"main.go": MAIN_GO, "handlers.go": HANDLERS_GO})
        assert c.contract_id == "http::POST::/"
        assert c.meta["base_stripped"] is True
        assert c.meta["base_token"] == ENV_KEY

    def test_env_bound_field_keeps_the_path_after_it(self) -> None:
        (c,) = _scan({"main.go": MAIN_GO, "handlers.go": FIELD_PLUS_PATH})
        assert c.contract_id == "http::POST::/chat"
        assert c.meta["base_token"] == ENV_KEY

    def test_binding_in_the_calling_file_is_enough_for_a_single_file_scan(self) -> None:
        (c,) = _extract(MAIN_GO + HANDLERS_GO.replace("package main\n", ""))
        assert c.meta["base_token"] == ENV_KEY

    def test_unbound_variable_root_is_refused(self) -> None:
        # `base` is a local, not an env key a binding names: it places nothing.
        assert _extract(UNBOUND_VARIABLE_ROOT) == []
        assert _scan({"main.go": MAIN_GO, "chat.go": UNBOUND_VARIABLE_ROOT}) == []

    def test_field_with_no_binding_is_refused(self) -> None:
        assert _scan({"handlers.go": HANDLERS_GO}) == []

    def test_field_bound_to_two_env_keys_is_refused(self) -> None:
        files = {"main.go": MAIN_GO, "other.go": OTHER_BINDING_GO, "handlers.go": HANDLERS_GO}
        assert _scan(files) == []

    def test_commented_out_binding_does_not_count(self) -> None:
        assert _scan({"main.go": COMMENTED_BINDING_GO, "handlers.go": HANDLERS_GO}) == []

    def test_text_after_the_field_that_is_not_a_path_is_refused(self) -> None:
        # `fe.addr + ":8080"` is not a base followed by `/`; no path is invented.
        assert _scan({"main.go": MAIN_GO, "handlers.go": FIELD_WITH_PORT}) == []

    def test_root_rule_is_scoped_to_the_consumer_builder(self) -> None:
        # Socket and every other caller still read `/` as unusable.
        assert is_unusable_consumer_path("/") is True
        assert is_unusable_consumer_path("/{param}") is True


def _link_strategies(contracts: list, service_bases: dict[str, str] | None = None) -> list:
    annotate_consumer_targets(contracts, service_bases)
    return [(ln.provider_repo, ln.strategy) for ln in match_contracts(contracts)]


class TestGoRootCallMatching:
    @staticmethod
    def _provider(repo: str = "shoppingassistantservice") -> list:
        extractor = HttpExtractor()
        extractor.provider_dialects = (FastApiDialect(),)
        extractor.consumer_dialects = ()
        return extractor.extract(Path("."), repo, files=[("main.py", ".py", ASSISTANT_PROVIDER)])

    def test_literal_host_links_as_service_mapped(self) -> None:
        consumers = _scan({"handlers.go": ROOT_LITERAL_HOST})
        got = _link_strategies(self._provider() + consumers)
        assert got == [("shoppingassistantservice", LinkStrategy.SERVICE_MAPPED)]

    def test_env_key_links_as_service_mapped(self) -> None:
        consumers = _scan({"main.go": MAIN_GO, "handlers.go": HANDLERS_GO})
        got = _link_strategies(self._provider("assistant") + consumers, {ENV_KEY: "assistant"})
        assert got == [("assistant", LinkStrategy.SERVICE_MAPPED)]

    def test_root_call_whose_target_is_not_resolved_links_nowhere(self) -> None:
        # Kept for its base token, but nothing maps it: `/` alone places no call.
        consumers = _scan({"main.go": MAIN_GO, "handlers.go": HANDLERS_GO})
        assert _link_strategies(self._provider("assistant") + consumers) == []
