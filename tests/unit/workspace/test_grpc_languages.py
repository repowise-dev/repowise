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
                0.8,
                (("service", "GreeterService"), ("source", "csharp_mapgrpc")),
            ),
            (
                "grpc::Greeter/*",
                "provider",
                # The template says ServiceBase while the regex matched `GreeterBase`.
                # Preserved verbatim from the module this replaced, quirk included.
                "cs:extends Greeter.GreeterServiceBase",
                0.8,
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
                0.8,
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
        assert [(r[1], r[3]) for r in rows] == [("provider", 0.8), ("consumer", 0.7)]
