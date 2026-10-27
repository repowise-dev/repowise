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


def test_java_unknown_member_gets_no_bare_name_edge(tmp_path: Path) -> None:
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

    assert before == []
    assert after == []


def test_java_ambiguous_overload_return_gets_no_edge(tmp_path: Path) -> None:
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

    assert before == after == []


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


def test_only_audited_lanes_are_enabled_by_default() -> None:
    assert (
        frozenset({"apex", "cpp", "csharp", "dart", "go", "java", "kotlin", "python"})
        == PRODUCTION_RETURN_TYPE_CHAIN_LANGUAGES
    )


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
    own = (
        "package a;\npublic class Duration {\n"
        " public static Duration ofSeconds(int s) { return null; }\n"
        " public long toNanos() { return 0; } }\n"
    )
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
        parsed,
        {"b/Use.java": {"a/Duration.java"}},
        repo_path=str(tmp_path),
        return_type_chain_languages=frozenset({"java"}),
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

    resolved = CallResolver(
        parsed, {}, repo_path=str(tmp_path), return_type_chain_languages=frozenset()
    ).resolve_file("Example.java", [call])
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

    resolved = CallResolver(
        parsed, {}, repo_path=str(tmp_path), return_type_chain_languages=frozenset({"java"})
    ).resolve_file("a/Use.java", [call])
    assert len(resolved) == 1
    assert resolved[0].callee_id.endswith("::Duration::toNanos")


def _go_packages(tmp_path: Path, files: dict[str, str]) -> dict[str, ParsedFile]:
    parsed: dict[str, ParsedFile] = {}
    for path, source in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        parsed.update(_parse(tmp_path, path, "go", source))
    # Stands in for the import-resolution phase, which unit tests do not run.
    for imp in parsed["cmd/run.go"].imports:
        imp.resolved_file = "logging/log.go"
    return parsed


def _go_chain(parsed: dict[str, ParsedFile], outer: str):
    return next(
        c for c in parsed["cmd/run.go"].calls if c.target_name == outer and c.receiver_call
    )


_GO_CALLER = (
    'package cmd\n\nimport "example.com/m/logging"\n\n'
    "func run(e error) {\n\tlogging.Error().Err(e)\n}\n"
)


def test_go_chain_on_an_external_return_type_refuses_the_bare_name(tmp_path: Path) -> None:
    """`logging.Error().Err(e)` calls zerolog's `Err`, not the package's own `Err`.

    The imported package declares an unrelated `Err` function, which the
    import-scoped bare-name tier used to bind the chained call to.
    """
    parsed = _go_packages(
        tmp_path,
        {
            "logging/log.go": (
                'package logging\n\nimport "github.com/rs/zerolog"\n\n'
                "func Error() *zerolog.Event { return nil }\n\n"
                "func Err(err error) *zerolog.Event { return nil }\n"
            ),
            "cmd/run.go": _GO_CALLER,
        },
    )
    call = _go_chain(parsed, "Err")
    assert call.receiver_call.receiver_name == "logging"
    imports = {"cmd/run.go": {"logging/log.go"}}

    before = CallResolver(
        parsed, imports, repo_path=str(tmp_path), return_type_chain_languages=frozenset()
    ).resolve_file("cmd/run.go", [call])
    after = CallResolver(parsed, imports, repo_path=str(tmp_path)).resolve_file(
        "cmd/run.go", [call]
    )

    assert [edge.callee_id for edge in before] == ["logging/log.go::Err"]
    assert after == []


def test_go_chain_on_a_repository_return_type_binds_its_method(tmp_path: Path) -> None:
    """The same shape over a type the repository declares resolves to that type's method."""
    parsed = _go_packages(
        tmp_path,
        {
            "logging/log.go": (
                "package logging\n\ntype Event struct{}\n\n"
                "func (e *Event) Err(err error) *Event { return e }\n\n"
                "func Error() *Event { return &Event{} }\n\n"
                "func Err(err error) *Event { return nil }\n"
            ),
            "cmd/run.go": _GO_CALLER,
        },
    )
    call = _go_chain(parsed, "Err")

    resolved = CallResolver(
        parsed, {"cmd/run.go": {"logging/log.go"}}, repo_path=str(tmp_path)
    ).resolve_file("cmd/run.go", [call])

    assert len(resolved) == 1
    assert resolved[0].callee_id == "logging/log.go::Event::Err"
    assert resolved[0].origin.startswith("return_type_")


def test_go_external_return_type_does_not_bind_a_same_named_repo_type(tmp_path: Path) -> None:
    """`*zerolog.Event` is not the repository's own `Event` in an unrelated package.

    Go types are package-scoped, so a repository-wide match on the bare type
    name is a coincidence rather than a binding, the same as java's.
    """
    parsed = _go_packages(
        tmp_path,
        {
            "logging/log.go": (
                'package logging\n\nimport "github.com/rs/zerolog"\n\n'
                "func Error() *zerolog.Event { return nil }\n"
            ),
            "audit/event.go": (
                "package audit\n\ntype Event struct{}\n\n"
                "func (e *Event) Err(err error) *Event { return e }\n"
            ),
            "cmd/run.go": _GO_CALLER,
        },
    )
    call = _go_chain(parsed, "Err")

    resolved = CallResolver(
        parsed, {"cmd/run.go": {"logging/log.go"}}, repo_path=str(tmp_path)
    ).resolve_file("cmd/run.go", [call])

    assert resolved == []


_GO_BUILDER = (
    "package m\n\ntype Builder struct{}\n\n"
    "func NewReport(a string) *Builder { return &Builder{} }\n"
    "func MakeValue() Builder { return Builder{} }\n"
    "func (b *Builder) WithPhase(p int) *Builder { return b }\n"
    "func (b Builder) WithClosest(s string) *Builder { return &b }\n"
    "func (b *Builder) Next() Builder { return *b }\n"
    "func (b *Builder) Build() int { return 1 }\n"
)


def _go_builder_edges(tmp_path: Path, test_body: str, extra: dict[str, str] | None = None):
    files = {"m/builder.go": _GO_BUILDER, "m/builder_test.go": f"package m\n\n{test_body}\n"}
    files.update(extra or {})
    parsed: dict[str, ParsedFile] = {}
    for path, source in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        parsed.update(_parse(tmp_path, path, "go", source))
    calls = parsed["m/builder_test.go"].calls
    imports = {path: set() for path in parsed}
    resolved = CallResolver(parsed, imports, repo_path=str(tmp_path)).resolve_file(
        "m/builder_test.go", calls
    )
    return sorted(edge.callee_id for edge in resolved)


def test_go_builder_chain_from_a_constructor_types_every_link(tmp_path: Path) -> None:
    """The type and its methods sit in another file of the package, as with a test file."""
    edges = _go_builder_edges(
        tmp_path,
        'func TestX() {\n\tNewReport("a").\n\t\tWithPhase(3).\n\t\tWithClosest("x").Build()\n}',
    )
    assert edges == [
        "m/builder.go::Builder::Build",
        "m/builder.go::Builder::WithClosest",
        "m/builder.go::Builder::WithPhase",
        "m/builder.go::NewReport",
    ]


def test_go_chain_from_a_method_returning_its_own_type(tmp_path: Path) -> None:
    edges = _go_builder_edges(
        tmp_path, "func TestX(b *Builder) {\n\tb.WithPhase(1).WithPhase(2).Build()\n}"
    )
    assert "m/builder.go::Builder::Build" in edges
    assert "m/builder.go::Builder::WithPhase" in edges


def test_go_chain_types_through_value_and_pointer_receivers(tmp_path: Path) -> None:
    edges = _go_builder_edges(
        tmp_path, 'func TestX() {\n\tMakeValue().WithClosest("a").Next().Build()\n}'
    )
    assert "m/builder.go::Builder::WithClosest" in edges
    assert "m/builder.go::Builder::Next" in edges
    assert "m/builder.go::Builder::Build" in edges


def test_go_builder_chain_on_an_external_type_still_refuses_the_bare_name(
    tmp_path: Path,
) -> None:
    edges = _go_builder_edges(
        tmp_path,
        "func TestX() {\n\tWrap().WithPhase(3).Build()\n}",
        {
            "m/ext.go": (
                'package m\n\nimport "github.com/rs/zerolog"\n\n'
                "func Wrap() *zerolog.Event { return nil }\n"
            )
        },
    )
    assert "m/builder.go::Builder::WithPhase" not in edges


# A fluent C# API: every link returns an interface, and most links are
# extension methods on it or on an interface it extends.
_FLUENT_SYNTAX = (
    "public interface IValidator {}\n"
    "public interface IRuleBuilder<T, out TProperty> {\n"
    "  IRuleBuilderOptions<T, TProperty> SetValidator(IValidator validator);\n}\n"
    "public interface IRuleBuilderInitial<T, out TProperty> : IRuleBuilder<T, TProperty> {}\n"
    "public interface IRuleBuilderOptions<T, out TProperty> : IRuleBuilder<T, TProperty> {}\n"
)
_FLUENT_EXTENSIONS = (
    "public static class Rules {\n"
    "  public static IRuleBuilderOptions<T, P> Must<T, P>"
    "(this IRuleBuilder<T, P> rule, Func<P, bool> check) { return null; }\n"
    "  public static IRuleBuilderOptions<T, P> WithMessage<T, P>"
    "(this IRuleBuilderOptions<T, P> rule, string text) { return rule; }\n"
    "  public static IRuleBuilderOptions<T, P> WithMessage<T, P>"
    "(this IRuleBuilderOptions<T, P> rule, string text, int code, bool log) { return rule; }\n"
    "  public static IRuleBuilderOptions<T, P> When<T, P>"
    "(this IRuleBuilderOptions<T, P> rule, Func<T, bool> check) { return rule; }\n"
    "}\n"
)
_FLUENT_BASE = (
    "public abstract class AbstractValidator<T> {\n"
    "  public IRuleBuilderInitial<T, P> RuleFor<P>(Func<T, P> pick) { return null; }\n"
    "  public void When(Func<T, bool> check, Action body) {}\n"
    "  public void WithMessage(string text) {}\n}\n"
)
_FLUENT_HERITAGE = {
    "Syntax.cs::IRuleBuilderInitial": {"Syntax.cs::IRuleBuilder"},
    "Syntax.cs::IRuleBuilderOptions": {"Syntax.cs::IRuleBuilder"},
    "Validator.cs::ModelValidator": {"Base.cs::AbstractValidator"},
}


def _csharp_chain_edges(
    tmp_path: Path, body: str, extra: dict[str, str] | None = None
) -> dict[tuple[str, int | None], tuple[str, str]]:
    """``{(method, argument count): (callee id, origin)}`` for a validator body."""
    files = {
        "Syntax.cs": _FLUENT_SYNTAX,
        "Rules.cs": _FLUENT_EXTENSIONS,
        "Base.cs": _FLUENT_BASE,
        "Validator.cs": (
            "public class ModelValidator : AbstractValidator<Model> {\n"
            "  public ModelValidator() { " + body + " }\n}\n"
        ),
        **(extra or {}),
    }
    parsed: dict[str, ParsedFile] = {}
    for path, source in files.items():
        parsed.update(_parse(tmp_path, path, "csharp", source))
    resolver = CallResolver(parsed, {}, repo_path=str(tmp_path), heritage_parents=_FLUENT_HERITAGE)
    edges = {}
    for call in parsed["Validator.cs"].calls:
        for edge in resolver.resolve_file("Validator.cs", [call]):
            edges[(call.target_name, call.argument_count)] = (edge.callee_id, edge.origin)
    return edges


def test_csharp_chain_binds_the_extensions_its_links_return(tmp_path: Path) -> None:
    """`RuleFor(..).Must(..).WithMessage(..).When(..)` is extension after extension.

    `When` used to bind to the validator's own inherited `When`, as if the call
    had no receiver. `Must` extends the interface `RuleFor`'s result inherits,
    and `When` is typed from `WithMessage`'s result: read with no receiver,
    that link would be the validator's own void `WithMessage`.
    """
    edges = _csharp_chain_edges(
        tmp_path, 'RuleFor(x => x.Text).Must(t => t != null).WithMessage("m").When(m => true);'
    )

    assert edges[("Must", 1)][0] == "Rules.cs::Rules::Must"
    assert edges[("WithMessage", 1)][0] == "Rules.cs::Rules::WithMessage#2"
    assert edges[("When", 1)] == ("Rules.cs::Rules::When", "receiver_extension_global")


def test_csharp_chain_binds_an_inherited_interface_member(tmp_path: Path) -> None:
    edges = _csharp_chain_edges(tmp_path, "RuleFor(x => x.Child).SetValidator(null);")

    assert edges[("SetValidator", 1)] == (
        "Syntax.cs::IRuleBuilder::SetValidator",
        "return_type_import",
    )


def test_csharp_chain_narrows_to_the_overload_its_arguments_select(tmp_path: Path) -> None:
    edges = _csharp_chain_edges(
        tmp_path, 'RuleFor(x => x.Text).Must(t => true).WithMessage("m", 7, true);'
    )

    assert edges[("WithMessage", 3)][0] == "Rules.cs::Rules::WithMessage#4"


def test_csharp_chain_on_a_repository_type_without_the_member_refuses(tmp_path: Path) -> None:
    """A known head type with no such member is not a call on `this`."""
    edges = _csharp_chain_edges(
        tmp_path,
        "Plain.Make().When(m => true, null);",
        {"Plain.cs": "public class Plain { public static Plain Make() { return null; } }\n"},
    )

    assert ("When", 2) not in edges


def test_csharp_chain_on_a_type_with_an_external_base_keeps_the_fallback(tmp_path: Path) -> None:
    """The external base may declare the member, so nothing is proven."""
    edges = _csharp_chain_edges(
        tmp_path,
        "Plain.Make().Flush();",
        {
            "Plain.cs": (
                "public class Plain : Stream { public static Plain Make() { return null; } }\n"
                "public class Sink { public void Flush() {} }\n"
            )
        },
    )

    assert edges[("Flush", 0)][0] == "Plain.cs::Sink::Flush"


def test_csharp_chain_on_a_builtin_type_refuses(tmp_path: Path) -> None:
    edges = _csharp_chain_edges(
        tmp_path,
        "Store.Load().ConfigureAwait(false);",
        {
            "Store.cs": (
                "public class Store { public static Task<int> Load() { return null; }\n"
                "  public void ConfigureAwait(bool flag) {} }\n"
            )
        },
    )

    assert ("ConfigureAwait", 1) not in edges


def test_csharp_chain_on_a_type_parameter_keeps_the_fallback(tmp_path: Path) -> None:
    """`TBuilder` is no type the repository declares; its members are its constraint's.

    Nothing proves the bare-name answer wrong, so the fallback still runs.
    """
    edges = _csharp_chain_edges(
        tmp_path,
        "Builder.Create().Add().Build();",
        {
            "Builder.cs": (
                "public class Builder { public static Builder Create() { return null; }\n"
                "  public object Build() { return null; } }\n"
                "public static class BuilderExtensions {\n"
                "  public static TBuilder Add<TBuilder>(this TBuilder builder) { return builder; }\n}\n"
            )
        },
    )

    assert edges[("Build", 0)][0] == "Builder.cs::Builder::Build"


def test_csharp_chain_links_that_name_each_other_end_unresolved(tmp_path: Path) -> None:
    """Two chains on one line whose links key onto each other cannot recurse."""
    edges = _csharp_chain_edges(tmp_path, "var a = First().Second(); var b = Second().First();")

    assert edges == {}


def test_csharp_chain_link_whose_declarations_disagree_has_no_type(tmp_path: Path) -> None:
    """Two same-arity `Then` overloads share one id but return different types.

    Neither return type is the link's, so the next link is not refused on
    whichever declaration the id happened to keep.
    """
    edges = _csharp_chain_edges(
        tmp_path,
        'RuleFor(x => x.Text).Must(t => true).Then(1).WithMessage("m");',
        {
            "Steps.cs": (
                "public static class Steps {\n"
                "  public static IRuleBuilderOptions<T, P> Then<T, P>"
                "(this IRuleBuilderOptions<T, P> rule, int n) { return rule; }\n"
                "  public static IRuleBuilderInitial<T, P> Then<T, P>"
                "(this IRuleBuilderInitial<T, P> rule, int n) { return rule; }\n}\n"
            )
        },
    )

    assert ("WithMessage", 1) in edges


_DART_REPO = (
    "class Item {\n  void save() {}\n}\n"
    "class Repo {\n"
    "  Repo();\n"
    "  // Repo.ghost(1) is only a comment.\n"
    "  Repo.fromJson(Object json);\n"
    "  factory Repo.load(String path) => Repo();\n"
    "  factory Repo.copy() {\n    return Repo.other();\n  }\n"
    "  static Repo parse(String text) => Repo();\n"
    "  Item item(int i) => Item();\n"
    "  Repo self() => this;\n"
    "  Item? maybe() => null;\n"
    "  Future<Item> later() async => Item();\n"
    "  void close() {}\n"
    "}\n"
)

# The methods a wrongly typed chain could land on.
_DART_OUTER = frozenset({"repo.dart::Item::save", "repo.dart::Repo::close"})


def _dart_edges(tmp_path: Path, body: str) -> set[tuple[int, str, str]]:
    """``(line, callee, origin)`` for the calls of ``App.run`` holding *body*."""
    app = (
        "import 'repo.dart';\n"
        "class App {\n  final Repo repo;\n  App(this.repo);\n"
        f"  void run(Repo r) {{\n{body}\n  }}\n"
        "  Repo helper() => Repo();\n}\n"
    )
    parsed = _parse(tmp_path, "repo.dart", "dart", _DART_REPO)
    parsed |= _parse(tmp_path, "app.dart", "dart", app)
    for imp in parsed["app.dart"].imports:
        imp.resolved_file = "repo.dart"
    resolver = CallResolver(parsed, {"app.dart": {"repo.dart"}, "repo.dart": set()})
    return {
        (rc.line - 5, rc.callee_id, rc.origin)
        for rc in resolver.resolve_file("app.dart", parsed["app.dart"].calls)
        if rc.caller_id == "app.dart::App::run"
    }


def test_dart_signature_carries_the_declared_return_type(tmp_path: Path) -> None:
    parsed = _parse(tmp_path, "repo.dart", "dart", _DART_REPO)
    signatures = {s.name: s.signature for s in parsed["repo.dart"].symbols}

    assert signatures["item"] == "item(int i) -> Item"
    assert signatures["maybe"] == "maybe() -> Item?"
    assert signatures["close"] == "close() -> void"


def test_dart_named_and_factory_constructors_bind_their_class(tmp_path: Path) -> None:
    edges = _dart_edges(tmp_path, "Repo.fromJson(1);\nRepo.load('x');\nRepo.parse('y');")

    assert edges == {
        (1, "repo.dart::Repo", "import_merged"),
        (2, "repo.dart::Repo", "import_merged"),
        (3, "repo.dart::Repo::parse", "receiver_import"),
    }


@pytest.mark.parametrize(
    "body",
    [
        "Repo.nope();",  # declared nowhere
        "Repo.ghost(1);",  # named only in a comment
        "Repo.other();",  # called in the class body, never declared
    ],
)
def test_dart_constructor_the_class_does_not_declare_binds_nothing(
    tmp_path: Path, body: str
) -> None:
    assert _dart_edges(tmp_path, body) == set()


def test_dart_untyped_member_call_binds_no_bare_name(tmp_path: Path) -> None:
    """A Dart member call has no bare-name reading: an untyped receiver draws no edge."""
    assert _dart_edges(tmp_path, "var a = unknown();\na.save();") == set()


@pytest.mark.parametrize(
    ("body", "callee"),
    [
        ("r.item(1).save();", "repo.dart::Item::save"),  # a parameter
        ("repo.item(1).save();", "repo.dart::Item::save"),  # a field
        ("r.maybe()?.save();", "repo.dart::Item::save"),  # a nullable return
        ("this.helper().close();", "repo.dart::Repo::close"),  # on this
        ("Repo().close();", "repo.dart::Repo::close"),  # on a constructor
        ("Repo.fromJson(2).close();", "repo.dart::Repo::close"),  # on a named one
    ],
)
def test_dart_chain_binds_the_method_of_the_inner_return_type(
    tmp_path: Path, body: str, callee: str
) -> None:
    assert (1, callee, "return_type_import") in _dart_edges(tmp_path, body)


@pytest.mark.parametrize(
    "body",
    [
        "r.later().save();",  # a Future is not the class it wraps
        "r.item(1).close();",  # the returned class lacks the method
        "r.self().later().save();",  # a Future mid-chain binds nothing
        "r.self().item(1).close();",  # class lacks method on third link
        "unknown().save();",  # nothing types the inner call
        "r..item(1)..close();",  # a cascade is not a chain
        "r?.item(1).save();",  # a null-aware first link is not captured
    ],
)
def test_dart_chain_without_a_typed_method_binds_no_bare_name(tmp_path: Path, body: str) -> None:
    assert {e for e in _dart_edges(tmp_path, body) if e[1] in _DART_OUTER} == set()


def test_dart_third_link_of_a_chain_binds_by_return_type(tmp_path: Path) -> None:
    edges = _dart_edges(tmp_path, "r.self().self().close();")

    assert (1, "repo.dart::Repo::close", "return_type_import") in edges


def test_dart_third_link_on_constructor_binds_by_return_type(tmp_path: Path) -> None:
    edges = _dart_edges(tmp_path, "Repo().self().self().close();")

    assert (1, "repo.dart::Repo::close", "return_type_import") in edges


def test_dart_third_link_type_changes_mid_chain(tmp_path: Path) -> None:
    edges = _dart_edges(tmp_path, "r.self().item(1).save();")

    assert (1, "repo.dart::Item::save", "return_type_import") in edges


def test_dart_three_links_parser_yields_three_sites(tmp_path: Path) -> None:
    parsed = _parse(tmp_path, "app.dart", "dart", "void run(Repo r) { r.self().self().close(); }")
    assert len(parsed["app.dart"].calls) == 3


def test_dart_third_link_of_a_chain_is_dropped_not_misbound(tmp_path: Path) -> None:
    edges = _dart_edges(tmp_path, "r.item(1).save().close();")

    assert {e[1] for e in edges if e[1] in _DART_OUTER} == {"repo.dart::Item::save"}



def test_dart_constructor_initializers_are_not_calls(tmp_path: Path) -> None:
    source = (
        "class A {\n  A.named(int x);\n}\n"
        "class B extends A {\n  B.x() : super.named(1);\n  B.y() : this.x();\n}\n"
    )
    parsed = _parse(tmp_path, "b.dart", "dart", source)

    assert parsed["b.dart"].calls == []


@pytest.mark.parametrize(
    "body",
    [
        "var a = r.item(1);\na.save();",
        "final a = await r.later();\na.save();",  # an awaited Future yields its type
        "final a = Repo.load('x');\na.close();",  # a factory constructor
    ],
)
def test_dart_local_takes_the_type_its_call_returns(tmp_path: Path, body: str) -> None:
    assert {e[1] for e in _dart_edges(tmp_path, body) if e[0] == 2} & _DART_OUTER


@pytest.mark.parametrize(
    "body",
    [
        "final a = r.later();\na.save();",  # an unawaited Future is not an Item
        "var a = r.item(1);\na.close();",  # the returned class lacks the method
        "final a = r.item(1)\n    .other();\na.save();",  # the call is not the whole value
    ],
)
def test_dart_local_without_a_typed_call_binds_nothing(tmp_path: Path, body: str) -> None:
    assert not {e for e in _dart_edges(tmp_path, body) if e[1] in _DART_OUTER}


def test_kotlin_signature_carries_the_declared_return_type(tmp_path: Path) -> None:
    source = "class Repo\nfun make(x: Int): Repo? = null\nfun none() = Repo()\n"
    parsed = _parse(tmp_path, "a.kt", "kotlin", source)
    signatures = {s.name: s.signature for s in parsed["a.kt"].symbols}

    assert signatures["make"] == "function make(x: Int) -> Repo?"
    assert signatures["none"] == "function none()"


_KOTLIN_LOCALS = (
    "class Repo {\n    fun load() {}\n}\n"
    "class Other {\n    fun load() {}\n}\n"
    "fun makeRepo(): Repo = Repo()\n"
    "fun build(name: String, block: () -> Unit): Repo = Repo()\n"
    "fun mk(a: Int): Repo = Repo()\nfun mk(a: String): Other = Other()\n"
    "fun <T> gen(): T = TODO()\n"
    "object Factory {\n    fun other(): Other = Other()\n}\n"
    "class Use {\n    fun go() {\n{body}\n    }\n}\n"
)
_SWIFT_LOCALS = (
    "class Repo {\n    func load() {}\n}\n"
    "class Other {\n    func load() {}\n}\n"
    "func makeRepo() throws -> Repo { return Repo() }\n"
    "func findRepo() -> Repo? { return nil }\n"
    "func mk(a: Int) -> Repo { return Repo() }\nfunc mk(a: String) -> Other { return Other() }\n"
    "func fetch() async -> Repo { return Repo() }\n"
    "enum Factory {\n    static func other() async -> Other { return Other() }\n}\n"
    "class Use {\n    func go() async throws {\n{body}\n    }\n}\n"
)


_LOCALS = {"kotlin": ("a.kt", _KOTLIN_LOCALS), "swift": ("a.swift", _SWIFT_LOCALS)}


def _last_line_callees(tmp_path: Path, language: str, body: str) -> set[str]:
    """Callees on the last line of *body*, held in ``Use.go``."""
    path, source = _LOCALS[language]
    text = source.replace("{body}", body)
    parsed = _parse(tmp_path, path, language, text)
    line = len(text.split("\n")) - 3
    resolver = CallResolver(parsed, {path: set()})
    return {
        rc.callee_id
        for rc in resolver.resolve_file(path, parsed[path].calls)
        if rc.line == line
    }


@pytest.mark.parametrize(
    ("language", "body", "callee"),
    [
        ("kotlin", "val r = makeRepo()\nr.load()", "a.kt::Repo::load"),
        ("kotlin", "val r = Factory.other()\nr.load()", "a.kt::Other::load"),
        ("kotlin", 'val r = build("a") { }\nr.load()', "a.kt::Repo::load"),
        ("kotlin", 'val r = build("a") {\n    println("x")\n}\nr.load()', "a.kt::Repo::load"),
        ("swift", "let r = try makeRepo()\nr.load()", "a.swift::Repo::load"),
        ("swift", "let r = await Factory.other()\nr.load()", "a.swift::Other::load"),
        ("swift", "let r = try? makeRepo()\nr?.load()", "a.swift::Repo::load"),
        ("swift", "async let r = fetch()\nawait r.load()", "a.swift::Repo::load"),
        ("swift", "if let r = findRepo() {\nr.load()", "a.swift::Repo::load"),
        ("swift", "if var r = findRepo() {\nr.load()", "a.swift::Repo::load"),
        ("swift", "guard let r = findRepo() else { return }\nr.load()", "a.swift::Repo::load"),
        ("swift", "while let r = findRepo() {\nr.load()", "a.swift::Repo::load"),
        ("swift", "if let o = makeRepo(), let r = findRepo() {\nr.load()", "a.swift::Repo::load"),
    ],
)
def test_kotlin_and_swift_local_takes_the_type_its_call_returns(
    tmp_path: Path, language: str, body: str, callee: str
) -> None:
    assert _last_line_callees(tmp_path, language, body) == {callee}


@pytest.mark.parametrize(
    ("language", "body"),
    [
        ("kotlin", "val r = makeRepo()\n    .let { Other() }"),
        ("kotlin", "val r = makeRepo()\n    ?.let { Other() }"),
        ("swift", "var r = try makeRepo()\n    .other()"),
        ("kotlin", "val r = makeRepo()\n    ?: Other()"),
        ("kotlin", "val r = makeRepo()\n    as Other"),
        ("kotlin", 'val r = build("a") { }.let { Other() }'),
        ("kotlin", 'val r = build("a") { } ?: Other()'),
        ("swift", "let r = try makeRepo()\n    as! Other"),
        ("swift", "let r = makeRepo() { Other() }"),
        ("swift", "if case let .some(r) = findRepo() {"),
        ("kotlin", "val r = mk(1)"),  # overloads of one arity that return different types
        ("swift", "let r = mk(a: 1)"),
        ("kotlin", "val r = gen()"),  # a type parameter is no class
    ],
)
def test_kotlin_and_swift_local_without_a_typed_call_binds_nothing(
    tmp_path: Path, language: str, body: str
) -> None:
    callees = _last_line_callees(tmp_path, language, body + "\nr.load()")

    assert not {callee for callee in callees if callee.endswith("::load")}


@pytest.mark.parametrize("imported", ["", "import java.net.Socket\n"])
def test_kotlin_return_type_an_external_import_names_is_not_the_repo_class(
    tmp_path: Path, imported: str
) -> None:
    """``Socket`` in a file importing ``java.net.Socket`` is not the repo's ``Socket``."""
    source = (
        f"package okio\n\n{imported}\ninterface Socket {{\n    fun source()\n}}\n"
        "fun socket(): Socket = TODO()\n\n"
        "class Use {\n    fun go() {\n        val s = socket()\n        s.source()\n    }\n}\n"
    )
    parsed = _parse(tmp_path, "Use.kt", "kotlin", source)
    resolver = CallResolver(parsed, {"Use.kt": set()})
    callees = {rc.callee_id for rc in resolver.resolve_file("Use.kt", parsed["Use.kt"].calls)}

    assert ("Use.kt::Socket::source" in callees) == (not imported)



_KOTLIN_CHAINS = (
    "class Request {\n    fun send(): Int = 0\n}\n"
    "class Other {\n    fun send(): Int = 0\n}\n"
    "class Client {\n"
    "    fun request(): Request = Request()\n"
    "    fun mk(a: Int): Request = Request()\n"
    "    fun mk(a: String): Other = Other()\n"
    "    fun <T> gen(): T = TODO()\n"
    "}\n"
    "class Response {\n"
    "    class Builder {\n"
    "        fun setHeaders(h: String): Builder = this\n"
    "        fun makeUnsafe(): Builder = this\n"
    "        fun build(): Response = Response()\n"
    "    }\n"
    "}\n"
    "fun Request.extra(): Int = 0\n"
    "class Use {\n    fun go(client: Client) {\n{body}\n    }\n}\n"
)


def _kotlin_chain_callees(tmp_path: Path, body: str) -> set[str]:
    """Callees of every call in *body*, held in ``Use.go``."""
    text = _KOTLIN_CHAINS.replace("{body}", body)
    parsed = _parse(tmp_path, "a.kt", "kotlin", text)
    resolver = CallResolver(parsed, {"a.kt": set()})
    return {
        rc.callee_id
        for rc in resolver.resolve_file("a.kt", parsed["a.kt"].calls)
        if rc.caller_id.endswith("::go")
    }


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("client.request().send()", {"a.kt::Client::request", "a.kt::Request::send"}),
        ("client.request()?.send()", {"a.kt::Client::request", "a.kt::Request::send"}),
        (
            "Response.Builder()\n        .setHeaders(\"a\")\n        .makeUnsafe()\n        .build()",
            {
                "a.kt::Response::Builder",
                "a.kt::Builder::setHeaders",
                "a.kt::Builder::makeUnsafe",
                "a.kt::Builder::build",
            },
        ),
        ("Response.Builder().build()", {"a.kt::Response::Builder", "a.kt::Builder::build"}),
    ],
)
def test_kotlin_call_chained_on_a_call_takes_the_type_the_inner_call_returns(
    tmp_path: Path, body: str, expected: set[str]
) -> None:
    assert _kotlin_chain_callees(tmp_path, body) == expected


def test_kotlin_chained_call_reaches_an_extension_on_the_returned_type(tmp_path: Path) -> None:
    callees = _kotlin_chain_callees(tmp_path, "client.request().extra()")

    assert any(callee.endswith("::extra") for callee in callees)


@pytest.mark.parametrize(
    "body",
    [
        "client.mk(1).send()",  # overloads of one arity that return different types
        "client.gen().send()",  # a type parameter is no class
        "client.request().let { it }.send()",  # a scope function is external
        "client.request().apply { }.send()",
        "remote.fetch().send()",  # the head is not declared in the repo
        "remote.fetch()?.send()",
    ],
)
def test_kotlin_chained_call_without_a_typed_inner_call_binds_nothing(
    tmp_path: Path, body: str
) -> None:
    callees = _kotlin_chain_callees(tmp_path, body)

    assert not {callee for callee in callees if callee.endswith("::send")}


@pytest.mark.parametrize(
    ("body", "inner"),
    [
        ("client.request().send()", ("request", "client", 0)),
        ("client.request()?.send()", ("request", "client", 0)),
        ("client.mk(1).send()", ("mk", "client", 1)),
        ("Response.Builder().build()", ("Builder", "Response", 0)),
        ("this.request().send()", ("request", "this", 0)),
        ("make().send()", ("make", None, 0)),
    ],
)
def test_kotlin_outer_call_of_a_chain_is_a_site_carrying_its_inner_call(
    tmp_path: Path, body: str, inner: tuple[str, str | None, int]
) -> None:
    text = _KOTLIN_CHAINS.replace("{body}", body)
    parsed = _parse(tmp_path, "a.kt", "kotlin", text)
    outer = next(c for c in parsed["a.kt"].calls if c.receiver_call and c.target_name != "extra")

    assert (
        outer.receiver_name,
        outer.receiver_call.target_name,
        outer.receiver_call.receiver_name,
        outer.receiver_call.argument_count,
    ) == (None, *inner)


@pytest.mark.parametrize(
    "body",
    [
        "a.b.request().send()",  # a dotted path is not a receiver the resolver can type
        "client.request()!!.send()",
        "client.request().let { it }.send()",
        "client.request() { }.send()",
    ],
)
def test_kotlin_chain_whose_inner_call_cannot_be_described_is_not_a_site(
    tmp_path: Path, body: str
) -> None:
    text = _KOTLIN_CHAINS.replace("{body}", body)
    parsed = _parse(tmp_path, "a.kt", "kotlin", text)

    assert not [c for c in parsed["a.kt"].calls if c.target_name == "send"]


_DART_FIELD_REPO = (
    "class Item {\n  void save() {}\n}\n"
    "class Repo {\n"
    "  Repo();\n"
    "  Repo.fromJson(Object json);\n"
    "  factory Repo.load(String path) => Repo();\n"
    "  static Repo parse(String text) => Repo();\n"
    "  final Item item = Item();\n"
    "  Item get current => Item();\n"
    "  void close() {}\n"
    "}\n"
    # Same-named free functions: a mis-read of the receiver would land here.
    "void parse() {}\nvoid save() {}\nvoid close() {}\n"
)
_DART_PREFIXED = "import 'repo.dart' as p;"


def _dart_field_edges(
    tmp_path: Path, body: str, imports: str = "import 'repo.dart';"
) -> set[tuple[int, str, str]]:
    """``(line, callee, origin)`` for the calls of ``App.run`` holding *body*."""
    app = (
        f"{imports}\n"
        "class App {\n  final Repo repo;\n  App(this.repo);\n"
        f"  void run(Repo r, List<Repo> repos) {{\n{body}\n  }}\n}}\n"
    )
    parsed = _parse(tmp_path, "repo.dart", "dart", _DART_FIELD_REPO)
    parsed |= _parse(tmp_path, "app.dart", "dart", app)
    for imp in parsed["app.dart"].imports:
        imp.resolved_file = "repo.dart"
    resolver = CallResolver(parsed, {"app.dart": {"repo.dart"}, "repo.dart": set()})
    return {
        (rc.line - 5, rc.callee_id, rc.origin)
        for rc in resolver.resolve_file("app.dart", parsed["app.dart"].calls)
        if rc.caller_id == "app.dart::App::run"
    }


@pytest.mark.parametrize(
    "body",
    [
        "r.item.save();",  # a field of a parameter
        "repo.item.save();",  # a field of the caller's class
        "this.repo.item.save();",  # a field of a field
        "r.current.save();",  # a getter, typed by its declared return type
        "repo.current.save();",
    ],
)
def test_dart_field_path_binds_the_method_of_the_last_fields_class(
    tmp_path: Path, body: str
) -> None:
    edges = _dart_field_edges(tmp_path, body)

    assert (1, "repo.dart::Item::save", "receiver_chain_import") in edges


@pytest.mark.parametrize(
    "body",
    [
        "r.item.close();",  # the field's class lacks the method
        "r.unknown.save();",  # no such field
        "r.item.item.save();",  # a second hop that is untyped
        "r.item?.save();",  # a null-aware hop is not captured
        "r?.item.save();",
        "repos[0].item.save();",  # an index is no field
        "r.close.save();",  # a method is not a field
        "r.parse.save();",
    ],
)
def test_dart_field_path_without_a_typed_hop_binds_nothing(tmp_path: Path, body: str) -> None:
    """No hop untyped, and no free function of the method's name is a stand-in."""
    assert _dart_field_edges(tmp_path, body) == set()


@pytest.mark.parametrize("method", ["fromJson", "load"])
def test_dart_prefixed_named_constructor_binds_its_class(tmp_path: Path, method: str) -> None:
    edges = _dart_field_edges(tmp_path, f"p.Repo.{method}(1);", _DART_PREFIXED)

    assert edges == {(1, "repo.dart::Repo", "module_alias")}


@pytest.mark.parametrize(
    "body",
    [
        "p.Repo.nope();",  # no such constructor
        "q.Repo.fromJson(1);",  # ``q`` is neither a prefix nor a typed name
        "p.Repo.parse('y');",  # a static member never binds to the free ``parse``
        "p.Nope.fromJson(1);",  # the prefixed library has no such class
        "p.item.save();",  # a prefix is not a variable the field walk starts from
        "q.Repo.close();",
    ],
)
def test_dart_prefixed_call_that_names_no_constructor_binds_nothing(
    tmp_path: Path, body: str
) -> None:
    assert _dart_field_edges(tmp_path, body, _DART_PREFIXED) == set()


def test_dart_unprefixed_named_constructor_and_prefixed_plain_one_still_bind(
    tmp_path: Path,
) -> None:
    edges = _dart_field_edges(tmp_path, "Repo.fromJson(1);\np.Repo();", _DART_PREFIXED)

    assert (2, "repo.dart::Repo", "module_alias") in edges
