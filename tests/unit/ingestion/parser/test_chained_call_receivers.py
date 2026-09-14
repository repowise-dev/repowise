from __future__ import annotations

import pytest

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info


@pytest.mark.parametrize(
    ("path", "language", "source", "outer", "inner", "receiver"),
    [
        (
            "Example.java",
            "java",
            b"class Example { void use(Service service) { service.factory(1).run(); } }",
            "run",
            "factory",
            "service",
        ),
        (
            "example.cpp",
            "cpp",
            b"void use(Service service) { service.factory(1).run(); }",
            "run",
            "factory",
            "service",
        ),
        (
            "Example.cs",
            "csharp",
            b"class Example { void Use(Service service) { service.Factory(1).Run(); } }",
            "Run",
            "Factory",
            "service",
        ),
        (
            "example.ts",
            "typescript",
            b"function use(service: Service) { service.factory(1).run(); }",
            "run",
            "factory",
            "service",
        ),
    ],
)
def test_chained_call_carries_structural_inner_call(
    parser: ASTParser,
    path: str,
    language: str,
    source: bytes,
    outer: str,
    inner: str,
    receiver: str,
) -> None:
    parsed = parser.parse_file(_make_file_info(path, language), source)
    call = next(
        c for c in parsed.calls if c.target_name == outer and getattr(c, "receiver_call", None)
    )

    assert call.receiver_name is None
    assert call.receiver_call is not None
    assert call.receiver_call.target_name == inner
    assert call.receiver_call.receiver_name == receiver
    assert call.receiver_call.argument_count == 1
