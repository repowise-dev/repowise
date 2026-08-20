from __future__ import annotations

import pytest

from repowise.core.ingestion.return_types import declared_return_type, normalize_return_type


def test_declared_return_type_reads_stored_signature() -> None:
    assert declared_return_type("factory() -> java.util.List<Item>") == "java.util.List<Item>"
    assert declared_return_type("factory()") is None


@pytest.mark.parametrize(
    ("raw", "language", "expected"),
    [
        ("Result", "java", "Result"),
        ("java.util.Optional<Result>", "java", "Optional"),
        ("const seastar::future<Result>&", "cpp", "future"),
        ("global::System.Threading.Tasks.Task<Result>?", "csharp", "Task"),
        ("Promise<Result> | null", "typescript", "Promise"),
    ],
)
def test_normalize_return_type_keeps_the_declared_wrapper(
    raw: str, language: str, expected: str
) -> None:
    assert normalize_return_type(raw, language) == expected
