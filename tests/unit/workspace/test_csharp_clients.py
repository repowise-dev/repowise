"""C# client bases and wrappers read across a repo's files, YARP forwarders and Refit."""

from __future__ import annotations

import textwrap
from pathlib import Path

from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.extractors.http.csharp_clients import with_base
from repowise.core.workspace.extractors.strings import CSHARP_SYNTAX, resolve_string
from repowise.core.workspace.origins import ContractOrigin


def _consumers(files: dict[str, str]) -> dict[tuple[str, int], tuple[str, str | None, str]]:
    walked = [(path, ".cs", textwrap.dedent(text)) for path, text in files.items()]
    contracts = HttpExtractor().extract(Path("."), "web", files=walked)
    return {
        (c.file_path, c.line): (c.contract_id, c.meta.get("host"), c.origin)
        for c in contracts
        if c.role == "consumer"
    }


def test_typed_and_named_client_bases_place_the_host() -> None:
    found = _consumers({
        "Extensions.cs": """
            public static class Extensions
            {
                private const string Catalog = "catalog";

                public static void AddApplicationServices(this IHostApplicationBuilder builder)
                {
                    builder.Services.AddHttpClient<OrderingService>(o => o.BaseAddress = new("https+http://ordering-api"))
                        .AddApiVersion(1.0);
                    builder.Services.AddHttpClient(Catalog)
                        .ConfigureHttpClient(c => c.BaseAddress = new Uri("http://catalog-api/api/"));
                }
            }
        """,
        "OrderingService.cs": """
            public class OrderingService(HttpClient httpClient, IDistributedCache cache)
            {
                public Task<OrderRecord[]> GetOrders() => httpClient.GetFromJsonAsync<OrderRecord[]>("/api/Orders/")!;
                public Task<string?> Cached() => cache.GetStringAsync("orders/last");
            }
        """,
        "CatalogReader.cs": """
            public class CatalogReader(IHttpClientFactory factory)
            {
                private const string Catalog = "catalog";

                public async Task<string> Items()
                {
                    var client = factory.CreateClient(Catalog);
                    return await client.GetStringAsync("items/all");
                }
            }
        """,
    })
    assert found[("OrderingService.cs", 4)] == (
        "http::GET::/api/orders", "ordering-api", ContractOrigin.CLIENT_CALL
    )
    # A cache in the same typed client is not the HttpClient: no base.
    assert found[("OrderingService.cs", 5)] == (
        "http::GET::/orders/last", None, ContractOrigin.NAME_HEURISTIC
    )
    assert found[("CatalogReader.cs", 9)] == (
        "http::GET::/api/items/all", "catalog-api", ContractOrigin.INSTANCE_BASE
    )


def test_a_type_registered_with_an_unread_base_takes_none() -> None:
    found = _consumers({
        "WebApp.cs": """
            class WebApp { void Add(IServiceCollection s) {
                s.AddHttpClient<CatalogService>(o => o.BaseAddress = new("http://catalog-api"));
            } }
        """,
        "Hybrid.cs": """
            class Hybrid { void Add(IServiceCollection s) {
                s.AddHttpClient<CatalogService>(o => o.BaseAddress = new(MobileBffHost));
            } }
        """,
        "CatalogService.cs": """
            public class CatalogService(HttpClient httpClient)
            {
                public Task<Item[]?> Items() => httpClient.GetFromJsonAsync<Item[]>("api/catalog/items");
            }
        """,
    })
    assert found[("CatalogService.cs", 4)][:2] == ("http::GET::/api/catalog/items", None)


def test_wrapper_methods_are_confirmed_by_their_body() -> None:
    found = _consumers({
        "HttpService.cs": """
            public class HttpService : IHttpService
            {
                private readonly HttpClient _httpClient;
                private readonly string _apiUrl;

                public async Task<T> HttpGet<T>(string uri) where T : class
                {
                    var result = await _httpClient.GetAsync($"{_apiUrl}{uri}");
                    return await Read<T>(result);
                }

                public async Task<T> HttpDelete<T>(string uri, int id) where T : class
                {
                    var result = await _httpClient.DeleteAsync($"{_apiUrl}{uri}/{id}");
                    return await Read<T>(result);
                }
            }
        """,
        "LocalStore.cs": """
            public class LocalStore
            {
                private readonly IDistributedCache _cache;

                public Task<string?> Lookup(string key)
                {
                    return _cache.GetStringAsync(key);
                }
            }
        """,
        "CatalogItemService.cs": """
            public class CatalogItemService(IHttpService httpService, LocalStore store)
            {
                public Task<Items> List() => httpService.HttpGet<Items>("catalog-items");
                public Task<Item> Get(int id) => httpService.HttpGet<Item>($"catalog-items/{id}");
                public Task<Status> Delete(int id) => httpService.HttpDelete<Status>("catalog-items", id);
                public Task<string?> Note() => store.Lookup("notes");
            }
        """,
    })
    rows = {k: v for k, v in found.items() if k[0] == "CatalogItemService.cs"}
    assert rows == {
        ("CatalogItemService.cs", 4): ("http::GET::/catalog-items", None, ContractOrigin.DATAFLOW),
        ("CatalogItemService.cs", 5): ("http::GET::/catalog-items/{param}", None, ContractOrigin.DATAFLOW),
        ("CatalogItemService.cs", 6): ("http::DELETE::/catalog-items/{param}", None, ContractOrigin.DATAFLOW),
    }


def test_combined_uris_through_a_request_provider() -> None:
    found = _consumers({
        "RequestProvider.cs": """
            public class RequestProvider : IRequestProvider
            {
                public async Task<TResult?> GetAsync<TResult>(string uri, string token = "")
                {
                    HttpClient httpClient = GetOrCreateHttpClient(token);
                    using var response = await httpClient.GetAsync(uri).ConfigureAwait(false);
                    return await response.Content.ReadFromJsonAsync<TResult>();
                }
            }
        """,
        "OrderService.cs": """
            public class OrderService(IRequestProvider requestProvider, ISettingsService settings)
            {
                private const string ApiUrlBase = "api/orders";
                private const string ApiVersion = "api-version=1.0";

                public async Task<Order> GetOrderAsync(int orderId)
                {
                    var uri = $"{UriHelper.CombineUri(settings.GatewayOrdersEndpointBase, $"{ApiUrlBase}/{orderId}")}?{ApiVersion}";
                    return await requestProvider.GetAsync<Order>(uri);
                }

                public async Task<Item[]> Items(int type)
                {
                    var uri = UriHelper.CombineUri(settings.GatewayCatalogEndpointBase, $"api/catalog//items?type={type}");
                    return await requestProvider.GetAsync<Item[]>(uri);
                }
            }
        """,
    })
    assert found[("OrderService.cs", 10)] == (
        "http::GET::/api/orders/{param}", None, ContractOrigin.DATAFLOW
    )
    assert found[("OrderService.cs", 16)] == ("http::GET::/api/catalog/items", None, ContractOrigin.DATAFLOW)


def test_map_forwarder_names_the_destination() -> None:
    found = _consumers({
        "Program.cs": """
            app.MapForwarder("/product-images/{id}", "https+http://catalog-api", "/api/catalog/items/{id}/pic");
            app.MapForwarder("/basket/{**rest}", "http://basket-api", transformBuilder => { });
        """,
    })
    assert found[("Program.cs", 2)] == (
        "http::*::/api/catalog/items/{param}/pic", "catalog-api", ContractOrigin.CLIENT_CALL
    )
    # A transform in place of a path forwards the request's own path.
    assert found[("Program.cs", 3)] == ("http::*::/basket/{param}", "basket-api", ContractOrigin.CLIENT_CALL)


def test_refit_attributes_under_their_registered_base() -> None:
    found = _consumers({
        "ICatalogApi.cs": """
            using Refit;

            public interface ICatalogApi
            {
                [Get("/items/{id}")]
                Task<Item> GetItem(int id);

                [Post("/items")]
                Task Create([Body] Item item);
            }
        """,
        "Program.cs": """
            builder.Services.AddRefitClient<ICatalogApi>()
                .ConfigureHttpClient(c => c.BaseAddress = new Uri("http://catalog-api/api/catalog"));
        """,
        "Controller.cs": """
            public class ItemsController : ControllerBase
            {
                [Get("/not-refit")]
                public IActionResult Get() => Ok();
            }
        """,
    })
    assert found == {
        ("ICatalogApi.cs", 6): ("http::GET::/api/catalog/items/{param}", "catalog-api", ContractOrigin.CLIENT_CALL),
        ("ICatalogApi.cs", 9): ("http::POST::/api/catalog/items", "catalog-api", ContractOrigin.CLIENT_CALL),
    }


def test_with_base_follows_dotnet_resolution() -> None:
    assert with_base("http://h/api/", "items") == "http://h/api/items"
    assert with_base("http://h/api", "items") == "http://h/items"
    assert with_base("http://h/api/", "/items") == "http://h/items"
    assert with_base("http://h", "items") == "http://h/items"
    assert with_base("http://h/", "https://other/x") == "https://other/x"
    assert with_base("http://h/", "${Base}/x") == "${Base}/x"


def test_segment_joins_resolve_and_refuse() -> None:
    def text(expr: str) -> str | None:
        return resolve_string(expr, CSHARP_SYNTAX, {"Root": '"api/catalog/"'})

    assert text('new Uri(baseUri, "items")') == "${baseUri}/items"
    assert text('Url.Combine("http://h/", Root, "/items")') == "http://h/api/catalog/items"
    assert text('new("http://h/")') == "http://h/"
    # A later piece the file does not settle is not a URL segment.
    assert text('new Uri("http://h/", UriKind.Absolute)') is None
