"""The shared HTTP client-call layer: URL expression resolution per language,
method inference from an argument, and the contract loop the dialects share.

The claim under test is that a URL argument is read as an *expression* and
refused when it cannot be settled in the file, so a guessed edge never forms.
"""

from __future__ import annotations

import pytest

from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.http.client_calls import (
    CSHARP_SYNTAX,
    GO_SYNTAX,
    JAVA_SYNTAX,
    JS_SYNTAX,
    KOTLIN_SYNTAX,
    PHP_SYNTAX,
    PYTHON_SYNTAX,
    RUBY_SYNTAX,
    RUST_SYNTAX,
    ClientCallMatch,
    call_arguments,
    consumer_contracts,
    method_from_argument,
    resolve_url,
    split_first_arg,
    string_constants,
)


class TestLiterals:
    @pytest.mark.parametrize(
        ("syntax", "expr", "expected"),
        [
            (JS_SYNTAX, "'/api/users'", "/api/users"),
            (JS_SYNTAX, "`${BASE}/users/${id}`", "${BASE}/users/${id}"),
            (PYTHON_SYNTAX, '"/api/users"', "/api/users"),
            (PYTHON_SYNTAX, 'f"{base}/users/{uid}"', "${base}/users/${uid}"),
            (PYTHON_SYNTAX, 'r"/raw"', "/raw"),
            (RUST_SYNTAX, '"http://svc/health"', "http://svc/health"),
            (CSHARP_SYNTAX, '$"{_baseUrl}/vix/{id}/bid"', "${_baseUrl}/vix/${id}/bid"),
            (CSHARP_SYNTAX, '@"/verbatim/{notinterp}"', "/verbatim/{notinterp}"),
            (GO_SYNTAX, '"/api/v1"', "/api/v1"),
            (GO_SYNTAX, "`/raw/path`", "/raw/path"),
            (
                RUBY_SYNTAX,
                '"#{BASE_URL}/accounts/#{@account_id}"',
                "${BASE_URL}/accounts/${@account_id}",
            ),
            (RUBY_SYNTAX, "'#{not_interpolated}'", "#{not_interpolated}"),
            (KOTLIN_SYNTAX, '"$TEST_SERVER/echo_query"', "${TEST_SERVER}/echo_query"),
            (KOTLIN_SYNTAX, '"${server.url}/x"', "${server.url}/x"),
            (KOTLIN_SYNTAX, '"""$BASE/raw"""', "${BASE}/raw"),
            (PHP_SYNTAX, '"$base/users/{$id}"', "${base}/users/${id}"),
            (PHP_SYNTAX, "'$literal/x'", "$literal/x"),
        ],
    )
    def test_literal_bodies(self, syntax, expr, expected):
        assert resolve_url(expr, syntax) == expected

    @pytest.mark.parametrize(
        ("syntax", "expr"),
        [
            (JS_SYNTAX, "path"),
            (JS_SYNTAX, '"/a" + id'),
            (JS_SYNTAX, "buildPath(id)"),
            (PYTHON_SYNTAX, 'b"/bytes"'),
            (PYTHON_SYNTAX, 'f"/x/{{literal}}"'),
            (RUST_SYNTAX, "&url"),
            (GO_SYNTAX, "u"),
        ],
    )
    def test_unsettled_expressions_are_refused(self, syntax, expr):
        assert resolve_url(expr, syntax) is None


class TestFormatCalls:
    def test_rust_format(self):
        expr = 'format!("{}/systems/{}", base, id)'
        assert resolve_url(expr, RUST_SYNTAX) == "${x}/systems/${x}"

    def test_go_sprintf(self):
        expr = 'fmt.Sprintf("%s/%s/@v/%s.zip", proxy, mod, ver)'
        assert resolve_url(expr, GO_SYNTAX) == "${x}/${x}/@v/${x}.zip"

    def test_java_string_format(self):
        expr = 'String.format("https://api.example.com/v3/price?ids=%s", ids)'
        assert resolve_url(expr, JAVA_SYNTAX) == "https://api.example.com/v3/price?ids=${x}"

    def test_a_format_call_whose_template_is_not_a_literal_is_refused(self):
        assert resolve_url("fmt.Sprintf(tmpl, a)", GO_SYNTAX) is None


class TestUnwrapAndConcat:
    def test_java_uri_create_is_unwrapped(self):
        assert resolve_url('URI.create(base + "/echo")', JAVA_SYNTAX) == "${base}/echo"

    def test_go_concat_with_a_trailing_name(self):
        assert resolve_url('"/users/" + id', GO_SYNTAX) == "/users/${id}"

    def test_php_concat(self):
        assert resolve_url("$this->base . '/contacts/' . $id", PHP_SYNTAX) == (
            "${$this->base}/contacts/${$id}"
        )

    def test_concat_with_a_call_is_refused(self):
        assert resolve_url('base + "/" + fmt(x)', GO_SYNTAX) is None

    def test_a_concat_of_two_names_carries_no_path(self):
        assert resolve_url("c.InfoAPIURL + id.Name", GO_SYNTAX) == "${c.InfoAPIURL}${id.Name}"


class TestConstantFolding:
    def test_python_single_assignment_folds(self):
        content = 'BASE = "http://svc"\nurl = f"{BASE}/users"\nrequests.get(url)\n'
        constants = string_constants(content, PYTHON_SYNTAX)
        assert resolve_url("url", PYTHON_SYNTAX, constants) == "http://svc/users"

    def test_a_reassigned_name_is_retired(self):
        content = 'url = "/a"\nurl = "/b"\n'
        assert string_constants(content, PYTHON_SYNTAX) == {}

    def test_go_short_declaration(self):
        content = 'u := fmt.Sprintf("%s/x", base)\nresp, err := http.Get(u)\n'
        constants = string_constants(content, GO_SYNTAX)
        # A format call bound once folds at the call site like a literal.
        assert resolve_url("u", GO_SYNTAX, constants) == "${x}/x"
        assert resolve_url("other", GO_SYNTAX, constants) is None
        content = 'const liveReloadSourceURL = "https://example.com/livereload.js"\n'
        constants = string_constants(content, GO_SYNTAX)
        assert resolve_url("liveReloadSourceURL", GO_SYNTAX, constants) == (
            "https://example.com/livereload.js"
        )

    def test_ruby_frozen_constant(self):
        content = "CLEARBIT_ENDPOINT = 'https://person.clearbit.com/v2/find'.freeze\n"
        constants = string_constants(content, RUBY_SYNTAX)
        assert resolve_url("CLEARBIT_ENDPOINT", RUBY_SYNTAX, constants) == (
            "https://person.clearbit.com/v2/find"
        )

    def test_ruby_interpolated_constant_folds_inline(self):
        content = "BASE_URL = 'https://api.dyte.io/v2'\n"
        constants = string_constants(content, RUBY_SYNTAX)
        expr = '"#{BASE_URL}/accounts/#{@account_id}/kit"'
        assert resolve_url(expr, RUBY_SYNTAX, constants) == (
            "https://api.dyte.io/v2/accounts/${@account_id}/kit"
        )

    def test_java_field_initialiser(self):
        content = 'private static final String BASE = "https://svc/api";\n'
        constants = string_constants(content, JAVA_SYNTAX)
        assert resolve_url('BASE + "/users"', JAVA_SYNTAX, constants) == "https://svc/api/users"

    def test_kotlin_const_val(self):
        content = 'const val TEST_SERVER = "http://127.0.0.1:8080"\n'
        constants = string_constants(content, KOTLIN_SYNTAX)
        assert resolve_url('"$TEST_SERVER/echo"', KOTLIN_SYNTAX, constants) == (
            "http://127.0.0.1:8080/echo"
        )

    def test_php_variable_and_const(self):
        content = "$base = 'https://svc';\nconst PATH = '/x';\n"
        constants = string_constants(content, PHP_SYNTAX)
        assert resolve_url("$base . PATH", PHP_SYNTAX, constants) == "https://svc/x"

    def test_a_comparison_is_not_an_assignment(self):
        assert string_constants('if (a == "x") {}\n', JAVA_SYNTAX) == {}

    @pytest.mark.parametrize(
        ("syntax", "content"),
        [
            (GO_SYNTAX, 'base := "http://host"\nbase += "/v1"\n'),
            (GO_SYNTAX, 'url := "https://host/v1"\nurl, err = build(id)\n'),
            (PHP_SYNTAX, "$b = 'http://host';\n$b .= '/v1';\n"),
            (KOTLIN_SYNTAX, 'var b = "http://host"\nb += "/v1"\n'),
        ],
    )
    def test_a_compound_or_multiple_assignment_retires_the_name(self, syntax, content):
        assert string_constants(content, syntax) == {}

    def test_a_field_assignment_is_not_a_local(self):
        content = 'this.baseUrl = "https://prod/api";\nString f(String baseUrl) {}\n'
        assert string_constants(content, JAVA_SYNTAX) == {}

    @pytest.mark.parametrize(
        ("syntax", "content"),
        [
            (JAVA_SYNTAX, '/** String url = "/v1/example"; */\n'),
            (KOTLIN_SYNTAX, '// val base = "/v1/example"\n'),
            (RUBY_SYNTAX, "# BASE = 'https://h/root'\n"),
        ],
    )
    def test_an_assignment_in_a_comment_is_not_a_binding(self, syntax, content):
        assert string_constants(content, syntax) == {}

    def test_an_unwrap_or_format_call_must_be_the_whole_expression(self):
        constants = {"BASE": '"https://h/root"'}
        assert resolve_url('URI.create(BASE).resolve("/v2")', JAVA_SYNTAX, constants) is None
        assert resolve_url('fmt.Sprintf("%s/api", b) + "/v2"', GO_SYNTAX) is None
        assert resolve_url("URI.parse(BASE) + '/x'", RUBY_SYNTAX, constants) is None

    def test_a_doubled_percent_is_a_literal(self):
        assert resolve_url('fmt.Sprintf("/a/100%%/%s", x)', GO_SYNTAX) == "/a/100%/${x}"

    def test_csharp_escaped_braces_are_refused(self):
        assert resolve_url('$"/a/{{lit}}/{id}"', CSHARP_SYNTAX) is None


class TestMethodFromArgument:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("'GET'", "GET"),
            ('"post"', "POST"),
            ("http.MethodGet", "GET"),
            ("HttpMethod.POST", "POST"),
            ("HTTPMethods.Get", "GET"),
            ("Net::HTTP::Get", "GET"),
            ("o.Method", None),
            ("method", None),
            ("options", None),
            ("GET", "GET"),
            ('"BUY"', None),
        ],
    )
    def test_verbs(self, text, expected):
        assert method_from_argument(text) == expected


class TestArgumentScanning:
    def test_call_arguments_split_at_top_level(self):
        src = 'http.NewRequest(http.MethodGet, fmt.Sprintf("%s/x", a), nil)'
        assert call_arguments(src, src.index("(")) == [
            "http.MethodGet",
            'fmt.Sprintf("%s/x", a)',
            "nil",
        ]

    def test_an_unclosed_call_is_none_and_no_arguments_is_empty(self):
        assert call_arguments("f(a, b", 1) is None
        assert call_arguments("f( )", 1) == []

    def test_split_first_arg_keeps_the_rest(self):
        assert split_first_arg("`/a/${f(x, y)}`, { method: 'POST' }") == (
            "`/a/${f(x, y)}`",
            " { method: 'POST' }",
        )


class TestLiteralSpan:
    def test_a_capture_cut_at_an_inner_quote_keeps_its_prefix(self):
        from repowise.core.workspace.extractors.http.js_clients import fetch_calls

        content = "fetch(`https://h/v1/links?${new URLSearchParams({ a: 'x' })}`)"
        (row,) = fetch_calls(content)
        assert row.url == "`https://h/v1/links?${new URLSearchParams({ a: `"
        assert resolve_url(row.url, JS_SYNTAX) == "https://h/v1/links?${new URLSearchParams({ a: "


class TestConsumerContracts:
    def _ctx(self, content: str, suffix: str = ".go") -> ScanContext:
        return ScanContext("svc", f"client{suffix}", suffix, content, {}, None)

    def test_method_falls_back_to_the_callee_name(self):
        content = 'apiPost("/users")'
        rows = [ClientCallMatch(client="w", url='"/users"', offset=0, callee="apiPost")]
        (c,) = consumer_contracts(self._ctx(content), rows, GO_SYNTAX)
        assert c.contract_id == "http::POST::/users"
        assert c.line == 1
        assert c.meta["client"] == "w"

    def test_an_unresolved_url_emits_nothing(self):
        rows = [ClientCallMatch(client="w", url="u", offset=0, method="GET")]
        assert consumer_contracts(self._ctx("f(u)"), rows, GO_SYNTAX) == []

    def test_rooted_only_drops_a_base_relative_path(self):
        rows = [
            ClientCallMatch(client="w", url='"some/path"', offset=0, method="GET"),
            ClientCallMatch(client="w", url='"/rooted"', offset=0, method="GET"),
            ClientCallMatch(client="w", url='"${b}/x"', offset=0, method="GET"),
        ]
        got = consumer_contracts(self._ctx("x", ".js"), rows, JS_SYNTAX, rooted_only=True)
        assert [c.contract_id for c in got] == ["http::GET::/rooted", "http::GET::/x"]

    def test_path_only_drops_a_key_lookup(self):
        rows = [ClientCallMatch(client="w", url='"database_url"', offset=0, method="GET")]
        assert consumer_contracts(self._ctx("x"), rows, GO_SYNTAX, path_only=True) == []
        assert len(consumer_contracts(self._ctx("x"), rows, GO_SYNTAX)) == 1
