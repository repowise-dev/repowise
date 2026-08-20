from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.call_resolver import (
    PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES,
    CallResolver,
)
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _parse(tmp_path: Path, path: str, language: str, source: str) -> dict[str, ParsedFile]:
    absolute = tmp_path / path
    absolute.write_text(source, encoding="utf-8")
    info = FileInfo(
        path=path,
        abs_path=str(absolute),
        language=language,  # type: ignore[arg-type]
        size_bytes=len(source),
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return {path: parse_file(info, source.encode())}


@pytest.mark.parametrize(
    ("path", "language", "source", "outer", "expected_type"),
    [
        (
            "Example.java",
            "java",
            "class Product { void run() {} }\nclass Wrong { void run() {} }\n"
            "class Use {\n Product make() { return new Product(); }\n void go() { make().run(); }\n}",
            "run",
            "Product",
        ),
        (
            "example.cpp",
            "cpp",
            "template<typename T> struct future { T get() {} };\n"
            "struct Wrong { int get() {} };\n"
            "future<int> make() { return {}; }\nvoid use() { make().get(); }",
            "get",
            "future",
        ),
        (
            "Example.cs",
            "csharp",
            "class Product { public void Run() {} }\nclass Wrong { public void Run() {} }\n"
            "class Use {\n Product Make() { return new Product(); }\n void Go() { Make().Run(); }\n}",
            "Run",
            "Product",
        ),
        (
            "example.ts",
            "typescript",
            "class Product { run() {} }\nclass Wrong { run() {} }\n"
            "function make(): Product { return new Product(); }\nfunction use() { make().run(); }",
            "run",
            "Product",
        ),
    ],
)
def test_return_type_chain_retargets_to_declared_type(
    tmp_path: Path,
    path: str,
    language: str,
    source: str,
    outer: str,
    expected_type: str,
) -> None:
    parsed = _parse(tmp_path, path, language, source)
    call = next(c for c in parsed[path].calls if c.target_name == outer and c.receiver_call)

    control = CallResolver(
        parsed, {}, repo_path=str(tmp_path), return_type_chain_languages=frozenset()
    )
    treatment = CallResolver(
        parsed, {}, repo_path=str(tmp_path), return_type_chain_languages=frozenset({language})
    )
    before = control.resolve_file(path, [call])
    after = treatment.resolve_file(path, [call])

    assert not any(edge.origin.startswith("return_type_") for edge in before)
    assert len(after) == 1
    assert after[0].callee_id.endswith(f"::{expected_type}::{outer}")
    assert after[0].origin.startswith("return_type_")


def test_java_unknown_member_preserves_legacy_fallback(tmp_path: Path) -> None:
    source = (
        "class Product {} class Wrong { void run() {} } "
        "class Use { Product make() { return new Product(); } void go() { make().run(); } }"
    )
    parsed = _parse(tmp_path, "Example.java", "java", source)
    call = next(
        c for c in parsed["Example.java"].calls if c.target_name == "run" and c.receiver_call
    )

    before = CallResolver(parsed, {}, return_type_chain_languages=frozenset()).resolve_file(
        "Example.java", [call]
    )
    after = CallResolver(parsed, {}, return_type_chain_languages=frozenset({"java"})).resolve_file(
        "Example.java", [call]
    )

    assert len(before) == 1
    assert after == before


def test_java_ambiguous_overload_return_preserves_legacy_fallback(tmp_path: Path) -> None:
    source = (
        "class First { void run() {} }\nclass Second { void run() {} }\n"
        "class Factory {\n First make(int value) { return new First(); }\n"
        " Second make(String value) { return new Second(); }\n}\n"
        "class Use { void go() { new Factory().make(1).run(); } }\n"
    )
    parsed = _parse(tmp_path, "Example.java", "java", source)
    call = next(
        c
        for c in parsed["Example.java"].calls
        if c.target_name == "run" and c.receiver_call and c.line == 7
    )

    before = CallResolver(parsed, {}, return_type_chain_languages=frozenset()).resolve_file(
        "Example.java", [call]
    )
    after = CallResolver(parsed, {}, return_type_chain_languages=frozenset({"java"})).resolve_file(
        "Example.java", [call]
    )

    assert after == before


def test_cpp_future_get_can_refuse_a_bare_name_fallback(tmp_path: Path) -> None:
    source = (
        "template<typename T> struct future {}; struct Wrong { int get() {} }; "
        "future<int> make() { return {}; } void use() { make().get(); }"
    )
    parsed = _parse(tmp_path, "example.cpp", "cpp", source)
    call = next(
        c for c in parsed["example.cpp"].calls if c.target_name == "get" and c.receiver_call
    )

    before = CallResolver(parsed, {}, return_type_chain_languages=frozenset()).resolve_file(
        "example.cpp", [call]
    )
    after = CallResolver(parsed, {}, return_type_chain_languages=frozenset({"cpp"})).resolve_file(
        "example.cpp", [call]
    )

    assert len(before) == 1
    assert after == []


@pytest.mark.parametrize(
    ("language", "product_source", "use_source", "outer"),
    [
        (
            "csharp",
            "class Product { public void Run() {} }",
            "class Use { Product Make() { return new Product(); } void Go() { Make().Run(); } }",
            "Run",
        ),
        (
            "typescript",
            "export class Product { run() {} }",
            "function make(): Product { return null as any; } function use() { make().run(); }",
            "run",
        ),
    ],
)
def test_unbound_global_return_type_preserves_legacy_fallback(
    tmp_path: Path,
    language: str,
    product_source: str,
    use_source: str,
    outer: str,
) -> None:
    extension = "cs" if language == "csharp" else "ts"
    parsed = {}
    parsed.update(_parse(tmp_path, f"Product.{extension}", language, product_source))
    parsed.update(_parse(tmp_path, f"Use.{extension}", language, use_source))
    use_path = f"Use.{extension}"
    call = next(c for c in parsed[use_path].calls if c.target_name == outer and c.receiver_call)

    before = CallResolver(parsed, {}, return_type_chain_languages=frozenset()).resolve_file(
        use_path, [call]
    )
    after = CallResolver(
        parsed, {}, return_type_chain_languages=frozenset({language})
    ).resolve_file(use_path, [call])

    assert after == before


def test_only_audited_cpp_lane_is_enabled_by_default() -> None:
    assert frozenset({"cpp"}) == PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES
