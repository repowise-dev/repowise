"""The gRPC stub shapes, one table row per language.

`grpc/{go,java,python,typescript,csharp}.py` were five near-identical modules and
are now five rows in `grpc/languages.py`. A corpus diff over 1,300 candidate
files proved the fold byte-identical for go, java and python — but that corpus
contains no gRPC C# and no `@GrpcMethod` at all, and those two rows carry every
per-language rule the table had to grow a field for: C#'s second provider shape,
its consumer gate and its false-prefix list, and TypeScript's method group.

So they are pinned here, field by field, against what the deleted modules built.
"""

from __future__ import annotations

from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.grpc import DIALECTS


def _extract(suffix: str, content: str) -> list[tuple]:
    ctx = ScanContext("api", f"Sample{suffix}", suffix, content)
    out = []
    for dialect in DIALECTS:
        if suffix in dialect.extensions:
            for c in dialect.extract(ctx):
                out.append(
                    (c.contract_id, c.role, c.symbol_name, c.confidence, tuple(c.meta.items()))
                )
    return out


class TestCSharp:
    def test_both_provider_shapes_and_a_gated_consumer(self) -> None:
        rows = _extract(
            ".cs",
            "using Grpc.Net.Client;\n"
            "app.MapGrpcService<GreeterService>();\n"
            "class Impl : Acme.Rpc.Greeter.GreeterBase { }\n"
            "var c = new AuthServiceClient(channel);\n",
        )
        assert rows == [
            (
                "grpc::GreeterService/*",
                "provider",
                "cs:MapGrpcService<GreeterService>",
                0.85,
                (("service", "GreeterService"), ("source", "csharp_mapgrpc")),
            ),
            (
                "grpc::Greeter/*",
                "provider",
                # The template says ServiceBase while the regex matched `GreeterBase`.
                # Preserved verbatim from the module this replaced, quirk included.
                "cs:extends Greeter.GreeterServiceBase",
                0.85,
                (("service", "Greeter"), ("source", "csharp_base")),
            ),
            (
                "grpc::AuthService/*",
                "consumer",
                "cs:new AuthServiceClient",
                0.65,
                (("service", "AuthService"), ("source", "csharp_client")),
            ),
        ]

    def test_without_a_grpc_marker_only_consumers_are_suppressed(self) -> None:
        # `new XxxClient(...)` also matches TLS and HTTP classes, so it needs the
        # gate — but the providers in the same file must still be emitted.
        rows = _extract(
            ".cs",
            "app.MapGrpcService<GreeterService>();\nvar c = new AuthServiceClient(channel);\n",
        )
        assert [r[1] for r in rows] == ["provider"]

    def test_the_false_prefix_list_includes_http(self) -> None:
        # C#'s list carries "http", python's does not; one shared list would have
        # deleted a real Python `HttpStub` or admitted a C# `HttpClient`.
        rows = _extract(
            ".cs",
            "using Grpc.Core;\nvar a = new HttpClient();\nvar b = new MockThingClient();\n"
            "var c = new RealThingClient();\n",
        )
        assert [r[0] for r in rows] == ["grpc::RealThing/*"]


class TestTypeScript:
    def test_the_only_method_qualified_contract_id(self) -> None:
        rows = _extract(".ts", "@GrpcMethod('AuthService', 'Login')\nlogin() {}\n")
        assert rows == [
            (
                # Every other language emits `/*`; this is the one that names a method.
                "grpc::AuthService/Login",
                "provider",
                "ts:@GrpcMethod('AuthService', 'Login')",
                0.85,
                (
                    ("service", "AuthService"),
                    ("method", "Login"),
                    ("source", "ts_decorator"),
                ),
            )
        ]

    def test_typescript_has_no_consumer_shape(self) -> None:
        assert not _extract(".ts", "const s = new AuthServiceClient(channel);\n")


class TestPythonAndGoKeepTheirOwnRules:
    def test_python_false_prefixes_exclude_test_doubles_but_not_http(self) -> None:
        rows = _extract(
            ".py",
            "a = MockThingStub(ch)\nb = RealThingStub(ch)\nc = HttpThingStub(ch)\n",
        )
        # "http" is absent from python's list, so HttpThing survives — the
        # difference between the two lists that a shared one would erase.
        # Source order, which is the order the dialect emits in.
        assert [r[0] for r in rows] == ["grpc::RealThing/*", "grpc::HttpThing/*"]

    def test_python_needs_no_gate(self) -> None:
        # Unlike C#, a bare `XxxStub(` is credible on its own.
        assert _extract(".py", "s = AuthServiceStub(channel)\n")

    def test_go_emits_both_roles(self) -> None:
        rows = _extract(
            ".go",
            "pb.RegisterAuthServiceServer(s, &impl{})\nc := pb.NewAuthServiceClient(conn)\n",
        )
        assert [(r[1], r[3]) for r in rows] == [("provider", 0.85), ("consumer", 0.75)]


class TestJavaQualifiedBase:
    def test_a_package_qualified_impl_base_is_a_provider(self) -> None:
        # Online Boutique's adservice: a nested class naming the base by package.
        rows = _extract(
            ".java",
            "public final class AdService {\n"
            "  private static class AdServiceImpl\n"
            "      extends hipstershop.AdServiceGrpc.AdServiceImplBase {}\n"
            "}\n",
        )
        assert [(r[0], r[1], r[2]) for r in rows] == [
            ("grpc::AdService/*", "provider", "java:extends AdServiceGrpc.ImplBase")
        ]

    def test_the_bare_base_still_matches(self) -> None:
        rows = _extract(".java", "class Impl extends AuthServiceGrpc.AuthServiceImplBase {}\n")
        assert [r[0] for r in rows] == ["grpc::AuthService/*"]


class TestNodeAddService:
    _SERVER = (
        "const grpc = require('@grpc/grpc-js');\n"
        "const server = new grpc.Server();\n"
        "this.server.addService(\n"
        "  hipsterShopPackage.PaymentService.service,\n"
        "  { charge: handler }\n"
        ");\n"
        "server.addService(shopProto.CurrencyService.service, {convert});\n"
    )

    def test_each_registered_service_is_a_provider(self) -> None:
        rows = _extract(".js", self._SERVER)
        assert rows == [
            (
                "grpc::PaymentService/*",
                "provider",
                "js:addService(PaymentService.service)",
                0.85,
                (("service", "PaymentService"), ("source", "js_add_service")),
            ),
            (
                "grpc::CurrencyService/*",
                "provider",
                "js:addService(CurrencyService.service)",
                0.85,
                (("service", "CurrencyService"), ("source", "js_add_service")),
            ),
        ]

    def test_the_legacy_grpc_package_and_typescript_imports_count(self) -> None:
        legacy = "const grpc = require('grpc');\nserver.addService(proto.Greeter.service, impl);\n"
        ts = "import * as grpc from '@grpc/grpc-js';\nserver.addService(GreeterService.service, impl);\n"
        assert [r[0] for r in _extract(".js", legacy)] == ["grpc::Greeter/*"]
        assert [r[0] for r in _extract(".ts", ts)] == ["grpc::GreeterService/*"]

    def test_without_a_grpc_import_add_service_is_not_read(self) -> None:
        # `addService(x.service)` is a grpc-js shape, but nothing else says so.
        assert not _extract(".js", "registry.addService(plugins.Billing.service, opts);\n")

    def test_a_lowercase_receiver_is_not_a_service_name(self) -> None:
        # `this.service` is a field, not a proto service; the chain's PascalCase
        # segment is the name even when an earlier segment is called `service`.
        rows = _extract(
            ".js",
            "const grpc = require('@grpc/grpc-js');\n"
            "s.addService(this.service, impl);\n"
            "s.addService(pkg.service.Billing.service, impl);\n",
        )
        assert [r[0] for r in rows] == ["grpc::Billing/*"]


class TestCSharpGeneratedClients:
    """gRPC-dotnet nests the client in its service's class: `Basket.BasketClient`."""

    def test_a_dotted_new_names_the_service(self) -> None:
        # eShop's client app: the stub's namespace, the service class, its client.
        rows = _extract(
            ".cs",
            "using Grpc.Net.Client;\n"
            "_client = new BasketGrpcClient.Basket.BasketClient(_channel);\n"
            "var g = new global::Acme.Greeter.GreeterClient(channel);\n",
        )
        assert [(r[0], r[2]) for r in rows] == [
            ("grpc::Basket/*", "cs:new BasketClient"),
            ("grpc::Greeter/*", "cs:new GreeterClient"),
        ]

    def test_an_add_grpc_client_registration_is_a_consumer(self) -> None:
        rows = _extract(
            ".cs",
            "builder.Services.AddGrpcClient<Basket.BasketClient>(o => o.Address = new(\"http://basket-api\"));\n"
            "services.AddGrpcClient< Shop.Rpc.Greeter.GreeterClient >();\n",
        )
        assert rows == [
            (
                "grpc::Basket/*",
                "consumer",
                "cs:AddGrpcClient<Basket.BasketClient>",
                0.75,
                (("service", "Basket"), ("source", "csharp_add_grpc_client")),
            ),
            (
                "grpc::Greeter/*",
                "consumer",
                "cs:AddGrpcClient<Greeter.GreeterClient>",
                0.75,
                (("service", "Greeter"), ("source", "csharp_add_grpc_client")),
            ),
        ]

    def test_the_class_the_client_is_injected_into_is_a_consumer(self) -> None:
        # eShop's web app aliases the client; the usual shape is a constructor parameter.
        aliased = (
            "using GrpcBasketClient = eShop.Basket.API.Grpc.Basket.BasketClient;\n"
            "public class BasketService(GrpcBasketClient basketClient) {}\n"
        )
        injected = (
            "using Grpc.Core;\n"
            "public Worker(ILogger<Worker> log, Greeter.GreeterClient client) {}\n"
        )
        assert [(r[0], r[2]) for r in _extract(".cs", aliased)] == [
            ("grpc::Basket/*", "cs:Basket.BasketClient")
        ]
        assert [r[0] for r in _extract(".cs", injected)] == ["grpc::Greeter/*"]
        # An attribute before the parameter, or a nullable one.
        attributed = (
            "using Grpc.Core;\n"
            "public Page([FromServices] Greeter.GreeterClient a, Basket.BasketClient? b) {}\n"
        )
        assert [r[0] for r in _extract(".cs", attributed)] == ["grpc::Greeter/*", "grpc::Basket/*"]

    def test_typeof_nameof_and_generics_are_not_injection(self) -> None:
        rows = _extract(
            ".cs",
            "using Grpc.Core;\n"
            "var t = typeof(Greeter.GreeterClient);\nvar n = nameof(Greeter.GreeterClient);\n"
            "var l = new List<Greeter.GreeterClient>();\n",
        )
        assert rows == []

    def test_an_sdk_named_after_its_namespace_is_not_a_grpc_client(self) -> None:
        # `Stripe.StripeClient` has the generated shape; nothing in the file says gRPC.
        assert not _extract(
            ".cs",
            "using Client = Stripe.StripeClient;\n"
            "public Billing(Stripe.StripeClient client) {}\n"
            "var c = new Stripe.StripeClient(key);\n",
        )

    def test_a_type_that_only_starts_like_the_client_is_not_one(self) -> None:
        rows = _extract(
            ".cs",
            "using Grpc.Core;\n"
            "public Svc(Basket.BasketClientFactory f, Basket.BasketClientOptions o) {}\n",
        )
        assert rows == []

    def test_a_primary_constructor_impl_is_a_provider(self) -> None:
        rows = _extract(
            ".cs",
            "public class BasketService(\n"
            "    IBasketRepository repository,\n"
            "    ILogger<BasketService> logger) : Basket.BasketBase\n{\n}\n",
        )
        assert [(r[0], r[1]) for r in rows] == [("grpc::Basket/*", "provider")]
