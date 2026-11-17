"""Spring's declarative HTTP clients as consumers.

An ``@FeignClient`` interface written with the controller annotations, and a
Spring 6 ``@HttpExchange`` interface, declare the calls a service makes. The
claim under test is that each mapping becomes one consumer at the path the
client composes (client ``path``, type mapping, method mapping), that a Feign
client's ``name`` is the host the service-name map places, and that anything
the file does not settle (a constant path, a controller's own mappings) emits
nothing.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.http import HttpExtractor
from repowise.core.workspace.extractors.http.java_clients import SpringClientsDialect
from repowise.core.workspace.extractors.http.spring import SpringDialect
from repowise.core.workspace.matching import match_contracts

# piggymetrics account-service: a Feign client to the auth service.
AUTH_CLIENT = """package com.piggymetrics.account.client;

@FeignClient(name = "auth-service")
public interface AuthServiceClient {

	@RequestMapping(method = RequestMethod.POST, value = "/uaa/users", consumes = MediaType.APPLICATION_JSON_UTF8_VALUE)
	void createUser(User user);

}
"""

# piggymetrics statistics-service: a third-party API behind a property.
RATES_CLIENT = """package com.piggymetrics.statistics.client;

@FeignClient(url = "${rates.url}", name = "rates-client", fallback = ExchangeRatesClientFallback.class)
public interface ExchangeRatesClient {

    @RequestMapping(method = RequestMethod.GET, value = "/latest")
    ExchangeRatesContainer getRates(@RequestParam("base") Currency base);

}
"""

PREFIXED_CLIENT = """package com.example;

@FeignClient(value = "orders", path = "/api")
@RequestMapping("/orders")
interface OrdersClient {
    @GetMapping("/{id}")
    Order get(@PathVariable String id);

    @RequestMapping("/recent")
    List<Order> recent();

    // @DeleteMapping("/{id}")
    // void delete(@PathVariable String id);
}
"""

LITERAL_URL_CLIENT = """@FeignClient(name = "github", url = "https://api.github.com/")
interface GitHub {
    @GetMapping("/repos/{owner}/{repo}")
    Repo repo(@PathVariable String owner, @PathVariable String repo);
}
"""

CONSTANT_PATH_CLIENT = """@FeignClient(name = "orders", path = Paths.ORDERS)
interface OrdersClient {
    @GetMapping("/{id}")
    Order get(@PathVariable String id);
}
"""

CONSTANT_URL_CLIENT = """@FeignClient(name = "orders", url = Urls.ORDERS)
interface OrdersClient {
    @GetMapping("/orders/{id}")
    Order get(@PathVariable String id);
}
"""

KOTLIN_CLIENT = """@FeignClient("inventory")
interface InventoryClient {
    @PostMapping("/stock/reserve")
    fun reserve(@RequestBody req: Reservation)
}
"""

# A controller beside a Feign client in one file: one is served, one is called.
CONTROLLER_AND_CLIENT = """@RestController
@RequestMapping("/carts")
class CartController {
    @GetMapping("/{id}")
    Cart get(@PathVariable String id) { return null; }
}

@FeignClient(name = "pricing")
interface PricingClient {
    @GetMapping("/prices/{sku}")
    Price price(@PathVariable String sku);
}
"""

EXCHANGE_CLIENT = """package com.example;

@HttpExchange("/customers")
public interface CustomersClient {
    @GetExchange("/{id}")
    Customer get(@PathVariable String id);

    @HttpExchange(method = "POST", url = "/search")
    List<Customer> search(@RequestBody Query q);

    @HttpExchange("/any")
    Object any();
}
"""

EXCHANGE_CONTROLLER = """@RestController
@HttpExchange("/customers")
class CustomersController implements CustomersApi {
    @GetExchange("/{id}")
    Customer get(@PathVariable String id) { return null; }
}
"""


def contracts(content: str, rel_path: str = "Client.java") -> list:
    suffix = "." + rel_path.rsplit(".", 1)[1]
    ctx = ScanContext("svc", rel_path, suffix, content, {}, None)
    return SpringClientsDialect().extract(ctx)


def rows(content: str, rel_path: str = "Client.java") -> list[tuple[str, str | None]]:
    return [(c.contract_id, c.meta.get("host")) for c in contracts(content, rel_path)]


class TestFeignInterfaces:
    def test_a_feign_mapping_is_a_call_to_the_named_service(self) -> None:
        (c,) = contracts(AUTH_CLIENT)
        assert c.role == "consumer"
        assert c.contract_id == "http::POST::/uaa/users"
        assert c.meta["host"] == "auth-service"
        assert c.meta["client"] == "feign"
        assert c.line == 6

    def test_a_url_property_is_a_base_the_resolver_reads(self) -> None:
        (c,) = contracts(RATES_CLIENT)
        assert c.contract_id == "http::GET::/latest"
        assert "host" not in c.meta
        assert c.meta.get("base_stripped") is True

    def test_client_path_type_mapping_and_method_mapping_compose(self) -> None:
        # The bare @RequestMapping is sent as a GET; the commented one is not read.
        assert rows(PREFIXED_CLIENT) == [
            ("http::GET::/api/orders/{param}", "orders"),
            ("http::GET::/api/orders/recent", "orders"),
        ]

    def test_a_literal_url_is_the_host(self) -> None:
        assert rows(LITERAL_URL_CLIENT) == [
            ("http::GET::/repos/{param}/{param}", "api.github.com")
        ]

    def test_a_path_held_in_a_constant_refuses_the_client(self) -> None:
        assert contracts(CONSTANT_PATH_CLIENT) == []

    def test_a_url_held_in_a_constant_is_no_host(self) -> None:
        # The name would place the call at a service the url overrides.
        assert rows(CONSTANT_URL_CLIENT) == [("http::GET::/orders/{param}", None)]

    def test_kotlin_positional_name(self) -> None:
        assert rows(KOTLIN_CLIENT, "InventoryClient.kt") == [
            ("http::POST::/stock/reserve", "inventory")
        ]

    def test_a_controller_beside_a_client_stays_a_provider(self) -> None:
        assert rows(CONTROLLER_AND_CLIENT) == [("http::GET::/prices/{param}", "pricing")]
        ctx = ScanContext("svc", "Cart.java", ".java", CONTROLLER_AND_CLIENT, {}, None)
        assert [c.contract_id for c in SpringDialect().extract(ctx)] == ["http::GET::/carts/{param}"]


class TestHttpExchangeInterfaces:
    def test_exchange_mappings_compose_onto_the_type_prefix(self) -> None:
        # `@HttpExchange("/any")` names no verb, and a client sends exactly one.
        assert rows(EXCHANGE_CLIENT) == [
            ("http::GET::/customers/{param}", None),
            ("http::POST::/customers/search", None),
        ]
        assert {c.meta["client"] for c in contracts(EXCHANGE_CLIENT)} == {"http-exchange"}

    def test_a_controller_file_serves_its_exchange_mappings(self) -> None:
        assert contracts(EXCHANGE_CONTROLLER) == []


class TestCrossRepoLink:
    def test_feign_consumer_links_to_the_controller_it_calls(self) -> None:
        extractor = HttpExtractor()
        provider = """@RestController
@RequestMapping("/uaa/users")
public class UserController {
    @RequestMapping(method = RequestMethod.POST)
    public void createUser(@RequestBody User user) {}
}
"""
        providers = extractor.extract(
            Path("."), "auth-service", files=[("UserController.java", ".java", provider)]
        )
        consumers = extractor.extract(
            Path("."), "account-service", files=[("AuthServiceClient.java", ".java", AUTH_CLIENT)]
        )
        assert [c.role for c in providers] == ["provider"]
        assert [c.role for c in consumers] == ["consumer"]
        links = match_contracts(providers + consumers)
        assert [(k.consumer_repo, k.provider_repo) for k in links] == [
            ("account-service", "auth-service")
        ]


NESTED_ANNOTATION_CLIENT = """@FeignClient(name = "a", configuration = @Cfg(path = "/z", url = "http://x"))
interface C {
    @GetMapping("/items")
    String f();
}
"""

CONSTANT_NAME_CLIENT = """@FeignClient(name = Services.ORDERS)
interface OrdersClient {
    @GetMapping("/orders/{id}")
    Order get(@PathVariable String id);
}
"""

KOTLIN_ARRAY_CLIENT = """@FeignClient(name = "catalog")
interface CatalogClient {
    @GetMapping(path = ["/items", "/products"])
    fun list(): List<Item>
}
"""


class TestFeignArguments:
    def test_a_nested_annotation_s_arguments_are_not_the_client_s(self) -> None:
        assert rows(NESTED_ANNOTATION_CLIENT) == [("http::GET::/items", "a")]

    def test_a_name_held_in_a_constant_is_no_host(self) -> None:
        assert rows(CONSTANT_NAME_CLIENT) == [("http::GET::/orders/{param}", None)]

    def test_a_kotlin_path_array_fans_out(self) -> None:
        assert rows(KOTLIN_ARRAY_CLIENT, "CatalogClient.kt") == [
            ("http::GET::/items", "catalog"),
            ("http::GET::/products", "catalog"),
        ]
