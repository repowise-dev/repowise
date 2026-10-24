"""The generated-stub shapes gRPC produces, one table row per language.

Every language dialect was the same loop over a provider regex and a consumer
regex, differing only in the pattern, the display wording and the origin.
The real per-language facts are declared instead of coded: C# has two
provider shapes, TypeScript has no consumer shape and one shape that names a
method, and a shape other libraries share (a bare ``new XxxClient(...)``, an
``addService(...)``) declares a ``gate``: a marker the file must carry before
that shape means anything.

``.proto`` is not here — it is an IDL with a real grammar, not a stub shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from repowise.core.workspace.origins import ContractOrigin

from ..base import line_at
from ..calls import code_matches
from ..dialect import build_contract
from ..langs import CSHARP, GO, JAVA, JS_TS, PYTHON

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


_ROUTE_REGISTRATION = ContractOrigin.ROUTE_REGISTRATION
_CLIENT_CALL = ContractOrigin.CLIENT_CALL
# `new XClient(` names any class so; nothing confirms it is a gRPC stub.
_NAME_HEURISTIC = ContractOrigin.NAME_HEURISTIC

#: A C# file that talks gRPC: a channel, a call invoker, a DI registration, or a
#: `using` of a namespace with a `Grpc` segment (`Grpc.Core`, `Shop.Basket.Grpc`).
_CSHARP_GRPC_GATE = re.compile(
    r"\bGrpcChannel\b|\bGrpc\.(?:Net|Core)\b|\bCallInvoker\b|\bChannelBase\b"
    r"|\bAddGrpcClient\b|\busing\s+(?:\w+\s*=\s*)?(?:[\w.]+\.)?Grpc\b"
)
#: The client class gRPC-dotnet generates, nested in its service's class:
#: `Basket.BasketClient`, namespace-qualified or not. Group 1 is the service.
_CSHARP_CLIENT_TYPE = r"(?:global::)?(?:[\w.]+\.)?(\w+)\.\1Client"

@dataclass(frozen=True)
class _Rule:
    """One recognisable shape. Group 1 is always the service name."""

    role: str
    pattern: re.Pattern[str]
    symbol: str  # display template over {service} and {method}
    source: str  # meta["source"], the tag that says which shape matched
    origin: ContractOrigin
    method_group: int | None = None  # only a shape that names a method sets this
    #: A marker the file must carry before this rule runs.
    gate: re.Pattern[str] | None = None


@dataclass(frozen=True)
class _Language:
    name: str
    extensions: frozenset[str]
    rules: tuple[_Rule, ...]
    #: Tokens one of which a file must contain before any rule runs: every
    #: rule's pattern needs one, so the gate drops no match.
    requires: tuple[str, ...] = ()
    #: Stub-name prefixes that are test doubles rather than clients.
    false_prefixes: tuple[str, ...] = field(default=())


LANGUAGES: tuple[_Language, ...] = (
    _Language(
        name="go",
        extensions=GO,
        rules=(
            # pb.RegisterAuthServiceServer(grpcServer, &impl{})
            _Rule("provider", re.compile(r"\.Register(\w+)Server\s*\("),
                  "go:Register{service}Server", "go_register", _ROUTE_REGISTRATION),
            # pb.NewAuthServiceClient(conn)
            _Rule("consumer", re.compile(r"\.New(\w+)Client\s*\("),
                  "go:New{service}Client", "go_client", _CLIENT_CALL),
        ),
        requires=("Register", "Client"),
    ),
    _Language(
        name="java",
        extensions=JAVA,
        rules=(
            # extends AuthServiceGrpc.AuthServiceImplBase, package-qualified or not
            _Rule("provider", re.compile(r"extends\s+(?:[\w.]+\.)?(\w+)Grpc\.(\w+)ImplBase"),
                  "java:extends {service}Grpc.ImplBase", "java_extends", _ROUTE_REGISTRATION),
            # AuthServiceGrpc.newBlockingStub(channel)
            _Rule("consumer", re.compile(r"(\w+)Grpc\.new(?:Blocking|Future)?Stub\s*\("),
                  "java:{service}Grpc.newStub", "java_stub", _CLIENT_CALL),
        ),
        requires=("Grpc.",),
    ),
    _Language(
        name="python",
        extensions=PYTHON,
        rules=(
            # add_AuthServiceServicer_to_server(servicer, server)
            _Rule("provider", re.compile(r"add_(\w+?)Servicer_to_server\s*\("),
                  "py:add_{service}Servicer_to_server", "py_servicer", _ROUTE_REGISTRATION),
            # AuthServiceStub(channel)
            _Rule("consumer", re.compile(r"(\w+)Stub\s*\("),
                  "py:{service}Stub", "py_stub", _CLIENT_CALL),
        ),
        requires=("_to_server", "Stub"),
        false_prefixes=("mock", "test", "fake"),
    ),
    _Language(
        name="typescript",
        extensions=JS_TS,
        rules=(
            # @GrpcMethod('AuthService', 'Login') — the one shape naming a method.
            _Rule("provider", re.compile(r"@GrpcMethod\s*\(\s*'(\w+)'\s*,\s*'(\w+)'\s*\)"),
                  "ts:@GrpcMethod('{service}', '{method}')", "ts_decorator", _ROUTE_REGISTRATION,
                  method_group=2),
            # server.addService(pkg.PaymentService.service, {charge}): grpc-js or the
            # legacy `grpc` package, the proto often loaded at runtime. A proto service
            # name is PascalCase, so `this.service` names nothing.
            _Rule("provider",
                  re.compile(r"\.addService\s*\(\s*(?:[\w$]+\.)*?([A-Z]\w*)\.service\b"),
                  "js:addService({service}.service)", "js_add_service", _ROUTE_REGISTRATION,
                  gate=re.compile(r"""['"](?:@grpc/grpc-js|grpc)['"]""")),
        ),
        requires=("@GrpcMethod", "addService"),
    ),
    _Language(
        name="csharp",
        extensions=CSHARP,
        rules=(
            # app.MapGrpcService<GreeterService>()
            _Rule("provider", re.compile(r"\.\s*MapGrpcService\s*<\s*(\w+)\s*>"),
                  "cs:MapGrpcService<{service}>", "csharp_mapgrpc", _ROUTE_REGISTRATION),
            # class Impl : ServiceName.ServiceNameBase, or class Impl(ILogger log) : ...
            _Rule("provider",
                  re.compile(r"class\s+\w+\s*(?:\([^()]*\)\s*)?:\s*(?:[\w.]+\.)?(\w+?)\s*\.\s*\1Base\b"),
                  "cs:extends {service}.{service}ServiceBase", "csharp_base", _ROUTE_REGISTRATION),
            # new AuthServiceClient(channel), new Shop.Basket.BasketClient(channel):
            # TLS and HTTP classes match too, hence the gate.
            _Rule("consumer", re.compile(r"\bnew\s+(?:global::)?(?:[\w.]+\.)?(\w+)Client\s*\("),
                  "cs:new {service}Client", "csharp_client", _NAME_HEURISTIC,
                  gate=_CSHARP_GRPC_GATE),
            # services.AddGrpcClient<Basket.BasketClient>(o => ...): the DI registration.
            _Rule("consumer", re.compile(r"\bAddGrpcClient\s*<\s*" + _CSHARP_CLIENT_TYPE + r"\s*>"),
                  "cs:AddGrpcClient<{service}.{service}Client>", "csharp_add_grpc_client",
                  _CLIENT_CALL),
            # The class the client is injected into: a constructor parameter typed by
            # the generated client, or a `using` alias of it.
            _Rule("consumer",
                  re.compile(r"(?:\busing\s+\w+\s*=|[(,\]])\s*" + _CSHARP_CLIENT_TYPE
                             + r"(?:\s*;|\??\s+\w)"),
                  "cs:{service}.{service}Client", "csharp_client_type", _NAME_HEURISTIC,
                  gate=_CSHARP_GRPC_GATE),
        ),
        # The client gate's markers all hold `Grpc`, `CallInvoker` or `Base`.
        requires=("MapGrpcService", "Base", "Grpc", "CallInvoker"),
        false_prefixes=("mock", "test", "fake", "http"),
    ),
)


class LanguageGrpcDialect:
    """The one dialect every entry in :data:`LANGUAGES` is served by."""

    def __init__(self, language: _Language) -> None:
        self._lang = language
        self.name = language.name
        self.extensions = language.extensions
        self.requires = language.requires

    def extract(self, ctx: ScanContext) -> list[Contract]:
        lang = self._lang
        out: list[Contract] = []
        gates: dict[re.Pattern[str], bool] = {}  # a gate several rules share is searched once
        for rule in lang.rules:
            if rule.gate is not None:
                if rule.gate not in gates:
                    gates[rule.gate] = rule.gate.search(ctx.content) is not None
                if not gates[rule.gate]:
                    continue
            for m in code_matches(ctx, rule.pattern):
                service = m.group(1)
                if rule.role == "consumer" and service.lower().startswith(lang.false_prefixes):
                    continue
                method = m.group(rule.method_group) if rule.method_group else None
                out.append(
                    build_contract(
                        ctx,
                        contract_type="grpc",
                        contract_id=f"grpc::{service}/{method or '*'}",
                        role=rule.role,
                        symbol_name=rule.symbol.format(service=service, method=method),
                        origin=rule.origin,
                        meta={
                            "service": service,
                            **({"method": method} if method else {}),
                            "source": rule.source,
                        },
                        line=line_at(ctx.content, m.start()),
                    )
                )
        return out
