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
    call = next(
        c
        for c in parsed[path].calls
        if c.target_name == outer and getattr(c, "receiver_call", None)
    )

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
        c
        for c in parsed["Example.java"].calls
        if c.target_name == "run" and getattr(c, "receiver_call", None)
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
        if c.target_name == "run" and getattr(c, "receiver_call", None) and c.line == 7
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
        c
        for c in parsed["example.cpp"].calls
        if c.target_name == "get" and getattr(c, "receiver_call", None)
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
    call = next(
        c
        for c in parsed[use_path].calls
        if c.target_name == outer and getattr(c, "receiver_call", None)
    )

    before = CallResolver(parsed, {}, return_type_chain_languages=frozenset()).resolve_file(
        use_path, [call]
    )
    after = CallResolver(
        parsed, {}, return_type_chain_languages=frozenset({language})
    ).resolve_file(use_path, [call])

    assert after == before


def test_only_audited_cpp_lane_is_enabled_by_default() -> None:
    assert frozenset({"cpp"}) == PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES


def test_module_level_chain_keeps_structural_receiver(tmp_path: Path) -> None:
    source = (
        "class Product { run() {} }\n"
        "function make(): Product { return new Product(); }\n"
        "make().run();\n"
    )
    parsed = _parse(tmp_path, "example.ts", "typescript", source)
    call = next(
        c
        for c in parsed["example.ts"].calls
        if c.target_name == "run" and getattr(c, "receiver_call", None)
    )

    result = CallResolver(
        parsed, {}, return_type_chain_languages=frozenset({"typescript"})
    ).resolve_file("example.ts", [call])

    assert result[0].caller_id == "example.ts::__module__"
    assert result[0].callee_id.endswith("::Product::run")


def _java_chain_call(parsed: dict[str, ParsedFile], path: str, outer: str):
    return next(
        c
        for c in parsed[path].calls
        if c.target_name == outer and getattr(c, "receiver_call", None)
    )


def test_java_external_chain_head_refuses_a_bare_name_fallback(tmp_path: Path) -> None:
    """`Duration.ofSeconds(3).toNanos()` is not a call to whatever declares toNanos.

    Java has no extension methods, so an external receiver type implies an
    external callee. That makes the bare-name answer disproved rather than
    merely unevidenced, which is what lets the tier refuse the site outright.
    """
    source = (
        "import java.time.Duration;\n"
        "class Expiry { long toNanos() { return 0; } }\n"
        "class Use { long go() { return Duration.ofSeconds(3).toNanos(); } }\n"
    )
    parsed = _parse(tmp_path, "Example.java", "java", source)
    call = _java_chain_call(parsed, "Example.java", "toNanos")

    before = CallResolver(parsed, {}, repo_path=str(tmp_path)).resolve_file(
        "Example.java", [call]
    )
    assert before == []


def test_java_external_chain_head_is_exempt_when_the_file_rebinds_the_name(
    tmp_path: Path,
) -> None:
    """A file importing the repository's own `Duration` means that one.

    The same call in the same table entry, differing only in the import, which
    is the claim stated as a test rather than as prose. Java imports resolve to
    repository files, so the file's import list is what separates the two.
    """
    own = "package a;\npublic class Duration { public long toNanos() { return 0; } }\n"
    use = (
        "package b;\nimport a.Duration;\n"
        "class Use { long go() { return Duration.ofSeconds(3).toNanos(); } }\n"
    )
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    parsed = {}
    parsed.update(_parse(tmp_path, "a/Duration.java", "java", own))
    parsed.update(_parse(tmp_path, "b/Use.java", "java", use))
    # Stands in for the import-resolution phase, which unit tests do not run.
    for imp in parsed["b/Use.java"].imports:
        imp.resolved_file = "a/Duration.java"
    call = _java_chain_call(parsed, "b/Use.java", "toNanos")

    resolved = CallResolver(
        parsed, {"b/Use.java": {"a/Duration.java"}}, repo_path=str(tmp_path)
    ).resolve_file("b/Use.java", [call])
    assert len(resolved) == 1
    assert resolved[0].callee_id.endswith("::Duration::toNanos")


def test_a_table_admits_only_its_own_lane(tmp_path: Path) -> None:
    """The table reaches the tier; it does not switch on the inferred lane too.

    Without this, adding a table to a language would silently enable return-type
    inference over every chained site it has, which is a different and much
    larger population than the one measured.
    """
    source = (
        "class Product { void run() {} }\nclass Wrong { void run() {} }\n"
        "class Use {\n Product make() { return new Product(); }\n"
        " void go() { make().run(); }\n}\n"
    )
    parsed = _parse(tmp_path, "Example.java", "java", source)
    call = _java_chain_call(parsed, "Example.java", "run")

    resolved = CallResolver(parsed, {}, repo_path=str(tmp_path)).resolve_file(
        "Example.java", [call]
    )
    assert not any(edge.origin.startswith("return_type_") for edge in resolved)


def test_java_is_the_only_language_carrying_a_chain_head_table() -> None:
    """The no-op-elsewhere argument, enforced rather than remembered."""
    from repowise.core.ingestion.languages.registry import REGISTRY

    populated = {
        spec.tag for spec in REGISTRY.all_specs() if spec.external_return_types
    }
    assert populated == {"java"}


def test_java_external_chain_head_is_exempt_when_the_repo_declares_the_name(
    tmp_path: Path,
) -> None:
    """A same-package repository type needs no import, so the import list misses it.

    The table records the JDK's return type, which is the wrong answer for a
    repository type whose factory returns something else, so refusing on it
    would drop a correct edge. Any repo-declared receiver name is exempted.
    """
    own = "package a;\nclass Duration { static Duration ofSeconds(int s) { return null; }\n long toNanos() { return 0; } }\n"
    use = "package a;\nclass Use { long go() { return Duration.ofSeconds(3).toNanos(); } }\n"
    (tmp_path / "a").mkdir()
    parsed = {}
    parsed.update(_parse(tmp_path, "a/Duration.java", "java", own))
    parsed.update(_parse(tmp_path, "a/Use.java", "java", use))
    call = _java_chain_call(parsed, "a/Use.java", "toNanos")

    resolved = CallResolver(parsed, {}, repo_path=str(tmp_path)).resolve_file(
        "a/Use.java", [call]
    )
    assert len(resolved) == 1
    assert resolved[0].callee_id.endswith("::Duration::toNanos")
