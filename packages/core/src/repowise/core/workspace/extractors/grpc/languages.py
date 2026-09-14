"""The generated-stub shapes gRPC produces, one table row per language.

Every language dialect was the same loop over a provider regex and a consumer
regex, differing only in the pattern, the display wording and the confidence.
The three real per-language facts are declared instead of coded: C# has two
provider shapes, TypeScript has no consumer shape and is the only one whose
match names a method, and a bare ``new XxxClient(...)`` needs a gRPC marker in
the file before it means anything.

``.proto`` is not here — it is an IDL with a real grammar, not a stub shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..base import line_at
from ..langs import CSHARP, GO, JAVA, JS_TS, PYTHON
from .dialect import make_grpc_contract

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


@dataclass(frozen=True)
class _Rule:
    """One recognisable shape. Group 1 is always the service name."""

    role: str
    pattern: re.Pattern[str]
    symbol: str  # display template over {service} and {method}
    source: str  # meta["source"], the tag that says which shape matched
    confidence: float
    method_group: int | None = None  # only a shape that names a method sets this


@dataclass(frozen=True)
class _Language:
    name: str
    extensions: frozenset[str]
    rules: tuple[_Rule, ...]
    #: A marker the file must carry before any consumer rule runs.
    consumer_gate: re.Pattern[str] | None = None
    #: Stub-name prefixes that are test doubles rather than clients.
    false_prefixes: tuple[str, ...] = field(default=())


LANGUAGES: tuple[_Language, ...] = (
    _Language(
        name="go",
        extensions=GO,
        rules=(
            # pb.RegisterAuthServiceServer(grpcServer, &impl{})
            _Rule("provider", re.compile(r"\.Register(\w+)Server\s*\("),
                  "go:Register{service}Server", "go_register", 0.8),
            # pb.NewAuthServiceClient(conn)
            _Rule("consumer", re.compile(r"\.New(\w+)Client\s*\("),
                  "go:New{service}Client", "go_client", 0.7),
        ),
    ),
    _Language(
        name="java",
        extensions=JAVA,
        rules=(
            # extends AuthServiceGrpc.AuthServiceImplBase
            _Rule("provider", re.compile(r"extends\s+(\w+)Grpc\.(\w+)ImplBase"),
                  "java:extends {service}Grpc.ImplBase", "java_extends", 0.8),
            # AuthServiceGrpc.newBlockingStub(channel)
            _Rule("consumer", re.compile(r"(\w+)Grpc\.new(?:Blocking|Future)?Stub\s*\("),
                  "java:{service}Grpc.newStub", "java_stub", 0.7),
        ),
    ),
    _Language(
        name="python",
        extensions=PYTHON,
        rules=(
            # add_AuthServiceServicer_to_server(servicer, server)
            _Rule("provider", re.compile(r"add_(\w+?)Servicer_to_server\s*\("),
                  "py:add_{service}Servicer_to_server", "py_servicer", 0.8),
            # AuthServiceStub(channel)
            _Rule("consumer", re.compile(r"(\w+)Stub\s*\("),
                  "py:{service}Stub", "py_stub", 0.7),
        ),
        false_prefixes=("mock", "test", "fake"),
    ),
    _Language(
        name="typescript",
        extensions=JS_TS,
        rules=(
            # @GrpcMethod('AuthService', 'Login') — the one shape naming a method.
            _Rule("provider", re.compile(r"@GrpcMethod\s*\(\s*'(\w+)'\s*,\s*'(\w+)'\s*\)"),
                  "ts:@GrpcMethod('{service}', '{method}')", "ts_decorator", 0.8,
                  method_group=2),
        ),
    ),
    _Language(
        name="csharp",
        extensions=CSHARP,
        rules=(
            # app.MapGrpcService<GreeterService>()
            _Rule("provider", re.compile(r"\.\s*MapGrpcService\s*<\s*(\w+)\s*>"),
                  "cs:MapGrpcService<{service}>", "csharp_mapgrpc", 0.8),
            # class Impl : ServiceName.ServiceNameBase
            _Rule("provider",
                  re.compile(r"class\s+\w+\s*:\s*(?:[\w.]+\.)?(\w+?)\s*\.\s*\1Base\b"),
                  "cs:extends {service}.{service}ServiceBase", "csharp_base", 0.8),
            # new AuthServiceClient(channel)
            _Rule("consumer", re.compile(r"\bnew\s+(\w+)Client\s*\("),
                  "cs:new {service}Client", "csharp_client", 0.65),
        ),
        consumer_gate=re.compile(
            r"\bGrpcChannel\b|\bGrpc\.(?:Net|Core)\b|\bCallInvoker\b|\bChannelBase\b"
            r"|\bAddGrpcClient\b|using\s+Grpc\b"
        ),
        false_prefixes=("mock", "test", "fake", "http"),
    ),
)


class LanguageGrpcDialect:
    """The one dialect every entry in :data:`LANGUAGES` is served by."""

    def __init__(self, language: _Language) -> None:
        self._lang = language
        self.name = language.name
        self.extensions = language.extensions

    def extract(self, ctx: ScanContext) -> list[Contract]:
        lang = self._lang
        gated = lang.consumer_gate is not None and not lang.consumer_gate.search(ctx.content)
        out: list[Contract] = []
        for rule in lang.rules:
            if rule.role == "consumer" and gated:
                continue
            for m in rule.pattern.finditer(ctx.content):
                service = m.group(1)
                if rule.role == "consumer" and service.lower().startswith(lang.false_prefixes):
                    continue
                method = m.group(rule.method_group) if rule.method_group else None
                out.append(
                    make_grpc_contract(
                        ctx,
                        contract_id=f"grpc::{service}/{method or '*'}",
                        role=rule.role,
                        symbol_name=rule.symbol.format(service=service, method=method),
                        confidence=rule.confidence,
                        meta={
                            "service": service,
                            **({"method": method} if method else {}),
                            "source": rule.source,
                        },
                        line=line_at(ctx.content, m.start()),
                    )
                )
        return out
