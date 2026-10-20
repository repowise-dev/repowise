from __future__ import annotations

import pytest

from repowise.core.ingestion.return_types import (
    declared_return_type,
    go_first_result,
    normalize_return_type,
    signature_parameter_count,
)


def test_declared_return_type_reads_stored_signature() -> None:
    assert declared_return_type("factory() -> java.util.List<Item>") == "java.util.List<Item>"
    assert declared_return_type("factory()") is None


@pytest.mark.parametrize(
    ("signature", "expected"),
    [
        ("function f(cb: () -> Foo) -> Bar", "Bar"),  # Kotlin / Swift function-typed parameter
        ("function f(cb: () -> Foo)", None),
        ("fn f(cb: impl Fn() -> Foo) -> Bar", "Bar"),
        ("func (r *T) Name(a int) -> int", "int"),  # a go receiver list comes first
        ("function make -> Repo", "Repo"),  # a Swift signature with no list
    ],
)
def test_declared_return_type_follows_the_parameter_list(
    signature: str, expected: str | None
) -> None:
    assert declared_return_type(signature) == expected


@pytest.mark.parametrize(
    ("signature", "expected"),
    [
        ("run() -> void", 0),
        ("make(Map<String, Integer> values, int count) -> Product", 2),
        ("broken", None),
    ],
)
def test_signature_parameter_count(signature: str, expected: int | None) -> None:
    assert signature_parameter_count(signature) == expected


@pytest.mark.parametrize(
    ("raw", "language", "expected"),
    [
        ("Result", "java", "Result"),
        # Java keeps its qualifiers: the resolver reads them where they were written.
        ("java.util.Optional<Result>", "java", "java.util.Optional"),
        ("const seastar::future<Result>&", "cpp", "future"),
        ("global::System.Threading.Tasks.Task<Result>?", "csharp", "Task`1"),
        ("IRuleBuilderOptions<T, TProperty>", "csharp", "IRuleBuilderOptions`2"),
        ("Promise<Result> | null", "typescript", "Promise"),
        ("*Event", "go", "Event"),
        ("*zerolog.Event", "go", "Event"),
    ],
)
def test_normalize_return_type_keeps_the_declared_wrapper(
    raw: str, language: str, expected: str
) -> None:
    assert normalize_return_type(raw, language) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("*zerolog.Event", "*zerolog.Event"),
        ("(*Detector, error)", "*Detector"),
        ("(d *Detector, err error)", "*Detector"),
        ("(a, b *Pair, err error)", "*Pair"),
        ("(map[string]int, error)", "map[string]int"),
    ],
)
def test_go_first_result(raw: str, expected: str) -> None:
    assert go_first_result(raw) == expected
