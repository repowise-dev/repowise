"""C# HttpClient consumers: the Json forms, request messages, locals and fields."""

from __future__ import annotations

import textwrap

from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.http.csharp_http import CSharpHttpDialect, without_query
from repowise.core.workspace.origins import ContractOrigin


def _consumers(source: str) -> dict[int, tuple[str, str]]:
    content = textwrap.dedent(source)
    ctx = ScanContext("web", "Services/Client.cs", ".cs", content)
    return {c.line: (c.contract_id, c.origin) for c in CSharpHttpDialect().extract(ctx)}


def test_field_prefix_and_per_method_locals() -> None:
    found = _consumers("""
        public class CatalogService(HttpClient httpClient)
        {
            private readonly string remoteServiceBaseUrl = "api/catalog/";

            public Task<CatalogItem?> GetCatalogItem(int id)
            {
                var uri = $"{remoteServiceBaseUrl}items/{id}";
                return httpClient.GetFromJsonAsync<CatalogItem>(uri);
            }

            public async Task<List<CatalogItem>> GetCatalogItems(IEnumerable<int> ids)
            {
                var uri = $"{remoteServiceBaseUrl}items/by?ids={string.Join("&ids=", ids)}";
                return await httpClient.GetFromJsonAsync<List<CatalogItem>>(uri);
            }

            public async Task<CatalogFacets> GetFacets()
            {
                var uri = $"{remoteServiceBaseUrl}items/facets?{filterQs}".TrimEnd('&', '?');
                return await httpClient.GetFromJsonAsync<CatalogFacets>(uri);
            }
        }
    """)
    assert found == {
        9: ("http::GET::/api/catalog/items/{param}", ContractOrigin.CLIENT_CALL),
        15: ("http::GET::/api/catalog/items/by", ContractOrigin.CLIENT_CALL),
        21: ("http::GET::/api/catalog/items/facets", ContractOrigin.CLIENT_CALL),
    }


def test_json_string_and_request_message_shapes() -> None:
    found = _consumers("""
        public class OrderingService(HttpClient client)
        {
            private const string Orders = "/api/orders/";

            public Task Create(CreateOrderRequest request)
            {
                var message = new HttpRequestMessage(HttpMethod.Post, Orders);
                return client.SendAsync(message);
            }

            public Task Add(Hook payload) => client.PostAsJsonAsync("/api/webhooks", payload);
            public Task<string> Raw() => client.GetStringAsync("/api/raw/text");
        }
    """)
    assert found == {
        8: ("http::POST::/api/orders", ContractOrigin.CLIENT_CALL),
        12: ("http::POST::/api/webhooks", ContractOrigin.CLIENT_CALL),
        # A cache's GetStringAsync(key) shares the name: not a confirmed client.
        13: ("http::GET::/api/raw/text", ContractOrigin.NAME_HEURISTIC),
    }


def test_names_that_do_not_settle_are_refused() -> None:
    found = _consumers("""
        public class Client(HttpClient http)
        {
            private string mutableBase = "/api/mutable";
            private readonly string reassigned = "/api/old";

            public Client(string configured) { this.reassigned = configured; }

            public void Declares() { var url = "/api/declared/here"; }

            public Task FromParameter(string url) => http.GetAsync(url);

            public Task Reassigned(bool flag)
            {
                var path = "/api/first";
                if (flag) path = "/api/second";
                return http.GetAsync(path);
            }

            public Task Plain() => http.GetAsync(mutableBase);
            public Task Ctor() => http.GetAsync(reassigned);
            public Task Verb(HttpMethod m) => http.SendAsync(new HttpRequestMessage(m, "/api/v"));
            public Task Replaced() => http.GetAsync($"/a/b?x={y}".Replace("a", "b"));
        }
    """)
    assert found == {}


def test_a_parameter_or_rebinding_hides_the_declared_value() -> None:
    found = _consumers("""
        class A
        {
            const string url = "/api/field";
            Task M(string url) => c.GetAsync(url);
            Task Out() { var u = "/api/a"; Foo(out u); return c.GetAsync(u); }
            Task Ref() { string r = "/api/r"; Foo(ref r); return c.GetAsync(r); }
            Task Tuple() { var t = "/api/t"; (t, v) = Pair(); return c.GetAsync(t); }
            void Lambda() { var l = "/api/l"; xs.ForEach(l => c.GetAsync(l)); }
        }
    """)
    assert found == {}


def test_a_local_far_above_the_use_is_not_read() -> None:
    padding = "\n".join("            Noop();" for _ in range(1000))
    found = _consumers(f"""
        class A
        {{
            Task M()
            {{
                var u = "/api/far";
{padding}
                return c.GetAsync(u);
            }}
        }}
    """)
    assert found == {}


def test_without_query_cuts_only_a_lone_literal() -> None:
    assert without_query('$"{b}items/by?ids={string.Join("&ids=", ids)}"') == '$"{b}items/by"'
    assert without_query("\"/a?x=1\".TrimEnd('&')") == '"/a"'
    assert without_query('$"/a?x={y}".Replace("a", "b")') == '$"/a?x={y}".Replace("a", "b")'
    assert without_query('@"/a/""q""?x"') == '@"/a/""q"""'
    assert without_query("uri") == "uri"
