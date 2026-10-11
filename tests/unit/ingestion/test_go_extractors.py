"""Unit tests for Go heritage (struct embedding) and binding extraction."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "foo.go") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="go",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


@pytest.fixture(scope="module")
def parser() -> ASTParser:
    return ASTParser()


class TestGoSymbols:
    def test_function_and_struct(self, parser: ASTParser) -> None:
        src = b"package x\n\ntype User struct { Name string }\n\nfunc Hello() string { return \"\" }\n"
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "User" in names
        assert "Hello" in names


class TestGoHeritage:
    def test_struct_embedding(self, parser: ASTParser) -> None:
        src = b"package x\n\ntype Base struct{}\n\ntype Foo struct {\n  Base\n  Name string\n}\n"
        result = parser.parse_file(_file(), src)
        parents = {r.parent_name for r in result.heritage}
        assert "Base" in parents

    def test_qualified_embed_keeps_package_qualifier(self, parser: ASTParser) -> None:
        """``io.Reader`` must stay ``io.Reader``, not ``Reader``.

        Stripping the qualifier lets an embed of a stdlib type bind to whatever
        repo-local type shares the short name — and when the enclosing type
        has that same name, the type inherits from itself.
        """
        src = b"package x\n\ntype Reader struct{}\n\ntype Foo struct {\n  io.Reader\n}\n"
        result = parser.parse_file(_file(), src)
        parents = {r.parent_name for r in result.heritage}
        assert "io.Reader" in parents
        assert "Reader" not in parents

    def test_qualified_interface_embed_keeps_package_qualifier(
        self, parser: ASTParser
    ) -> None:
        src = b"package x\n\ntype Reader interface{}\n\ntype Foo interface {\n  io.Reader\n}\n"
        result = parser.parse_file(_file(), src)
        parents = {r.parent_name for r in result.heritage}
        assert "io.Reader" in parents
        assert "Reader" not in parents


class TestGoBindings:
    def test_imports(self, parser: ASTParser) -> None:
        src = b"package x\n\nimport (\n  \"fmt\"\n  \"net/http\"\n)\n"
        result = parser.parse_file(_file(), src)
        modules = [imp.module_path for imp in result.imports]
        assert "fmt" in modules
        assert "net/http" in modules


class TestGoMethodReceiver:
    def test_method_parent_extracted_from_receiver(self, parser: ASTParser) -> None:
        src = b"package x\n\ntype User struct{}\n\nfunc (u *User) Greet() string { return \"\" }\n"
        result = parser.parse_file(_file(), src)
        greet = [s for s in result.symbols if s.name == "Greet"]
        assert greet
        assert greet[0].parent_name == "User"

    def test_unexported_receiver_type_still_parents_the_method(
        self, parser: ASTParser
    ) -> None:
        """Export status says nothing about whether a name is a type."""
        src = b"package x\n\ntype startEnd struct{}\n\nfunc (s *startEnd) add() {}\n"
        result = parser.parse_file(_file(), src)
        add = [s for s in result.symbols if s.name == "add"]
        assert add
        assert add[0].parent_name == "startEnd"

    def test_unnamed_receiver(self, parser: ASTParser) -> None:
        src = b"package x\n\ntype cache struct{}\n\nfunc (*cache) reset() {}\n"
        result = parser.parse_file(_file(), src)
        reset = [s for s in result.symbols if s.name == "reset"]
        assert reset
        assert reset[0].parent_name == "cache"


class TestGoCallThroughField:
    """A method call whose receiver is a field (``o.in.Do()``) must be captured.

    The operand of the selector is itself a selector_expression, which the
    identifier-only method-call pattern did not match — so the call was missing
    from the graph entirely rather than present and unresolved. Capture the
    whole receiver expression; resolution is a separate, measured change.
    """

    def test_field_receiver_call_site_is_captured(self, parser: ASTParser) -> None:
        src = (
            b"package p\n\n"
            b"type inner struct{}\n\n"
            b"func (i inner) Do() {}\n\n"
            b"type outer struct{ in inner }\n\n"
            b"func (o outer) Run() {\n"
            b"\to.in.Do()\n"
            b"}\n"
        )
        result = parser.parse_file(_file(), src)
        do_calls = [c for c in result.calls if c.target_name == "Do"]
        assert len(do_calls) == 1, f"expected one call site for Do, got {do_calls}"
        assert do_calls[0].receiver_name == "o.in"

    def test_plain_method_call_still_captured(self, parser: ASTParser) -> None:
        src = (
            b"package p\n\n"
            b"type inner struct{}\n\n"
            b"func (i inner) Do() {}\n\n"
            b"func (o outer) Run() {\n"
            b"\ti.Do()\n"
            b"}\n"
        )
        result = parser.parse_file(_file(), src)
        do_calls = [c for c in result.calls if c.target_name == "Do"]
        assert len(do_calls) == 1, f"expected one call site for Do, got {do_calls}"
        assert do_calls[0].receiver_name == "i"


class TestGoGenericCallWithTypeArguments:
    """``F[int](1)`` is a ``type_conversion_expression`` to tree-sitter-go, which
    cannot tell it from a conversion, so no ``call_expression`` pattern saw it
    and the call had no edge (#2988)."""

    def _calls(self, parser: ASTParser, body: bytes) -> list[tuple[str | None, str]]:
        src = (
            b"package p\n\n"
            b'import q "example.com/q"\n\n'
            b"func F[T any](x T) T { return x }\n\n"
            b"func Plain(x int) int { return x }\n\n"
            b"type List[T any] []T\n\n"
            b"type MyInt int\n\n"
            b"func Build(xs []int, x int) int {\n" + body + b"\treturn 0\n}\n"
        )
        result = parser.parse_file(_file(), src)
        return [(c.receiver_name, c.target_name) for c in result.calls]

    def test_same_package_generic_call_is_captured(self, parser: ASTParser) -> None:
        calls = self._calls(parser, b"\ta := F[int](1)\n\tb := Plain(2)\n\t_, _ = a, b\n")
        assert sorted(target for _receiver, target in calls) == ["F", "Plain"]

    def test_qualified_generic_call_keeps_its_package(self, parser: ASTParser) -> None:
        calls = self._calls(parser, b"\tv := q.NewQ[string](x)\n\t_ = v\n")
        assert calls == [("q", "NewQ")]

    def test_several_type_arguments(self, parser: ASTParser) -> None:
        calls = self._calls(parser, b"\tm := q.Map[int, string](xs)\n\t_ = m\n")
        assert calls == [("q", "Map")]

    def test_no_argument_and_several_arguments(self, parser: ASTParser) -> None:
        """One type argument with zero or several arguments is an index
        expression to the grammar, not a conversion: the same gap, another node."""
        calls = self._calls(
            parser,
            b"\ta := F[int]()\n\tb := F[int](1, 2)\n\tc := q.NewQ[string]()\n"
            b"\td := q.Pair[int](1, 2)\n\t_, _, _, _ = a, b, c, d\n",
        )
        assert calls == [(None, "F"), (None, "F"), ("q", "NewQ"), ("q", "Pair")]

    def test_several_type_arguments_and_several_arguments_were_already_calls(
        self, parser: ASTParser
    ) -> None:
        calls = self._calls(parser, b"\ta := F[int, string](1, 2)\n\t_ = a\n")
        assert calls == [(None, "F")]

    def test_plain_conversions_add_no_call_site(self, parser: ASTParser) -> None:
        """`int(x)` and `MyInt(x)` were call_expression sites before and are
        unchanged: one site each, no more."""
        calls = self._calls(
            parser, b"\ta := MyInt(x)\n\tb := float64(x)\n\t_, _ = a, b\n"
        )
        assert [target for _receiver, target in calls].count("MyInt") <= 1
        assert [target for _receiver, target in calls].count("float64") <= 1

    def test_a_conversion_to_a_generic_type_is_captured_as_one_site(
        self, parser: ASTParser
    ) -> None:
        """Indistinguishable from a call in the syntax, so it is one site."""
        calls = self._calls(parser, b"\tl := List[int](xs)\n\t_ = l\n")
        assert calls == [(None, "List")]


class TestGoGenericCallEdges:
    """The captured call resolves as its plain form does: same package by name,
    another package through the import."""

    _LIB = ("go", "package q\n\nfunc NewQ[T any](x T) T { return x }\n")

    def _edges(self, tmp_path: Path, body: str) -> set[tuple[str, str]]:
        from tests.unit.ingestion.test_chained_receiver_calls import _edges

        main = (
            "package p\n\n"
            'import q "example.com/q"\n\n'
            "func F[T any](x T) T { return x }\n\n"
            "type List[T any] []T\n\n"
            "type MyInt int\n\n"
            "func Build(xs []int) {\n" + body + "}\n"
        )
        edges = _edges(
            tmp_path,
            {"p/main.go": ("go", main), "q/q.go": self._LIB},
            links={"p/main.go": {"example.com/q": "q/q.go"}},
        )
        return {(caller, callee) for caller, callee, _confidence, _origin in edges}

    @pytest.mark.parametrize("call", ["F[int](1)", "F[int]()", "F[int](1, 2)"])
    def test_a_same_package_generic_call_has_its_edge(self, tmp_path: Path, call: str) -> None:
        assert self._edges(tmp_path, f"\t_ = {call}\n") == {("p/main.go::Build", "p/main.go::F")}

    @pytest.mark.parametrize("call", ['q.NewQ[string]("a")', "q.NewQ[string]()"])
    def test_a_qualified_generic_call_resolves_through_the_import(
        self, tmp_path: Path, call: str
    ) -> None:
        assert self._edges(tmp_path, f"\t_ = {call}\n") == {("p/main.go::Build", "q/q.go::NewQ")}

    def test_a_conversion_to_a_generic_type_resolves_as_a_plain_conversion_does(
        self, tmp_path: Path
    ) -> None:
        """The syntax cannot tell ``List[int](xs)`` from a call. It gets the edge
        to the type that ``MyInt(x)`` has always had, no more and no less."""
        plain = self._edges(tmp_path / "plain", "\t_ = MyInt(len(xs))\n")
        generic = self._edges(tmp_path / "generic", "\t_ = List[int](xs)\n")
        assert plain == {("p/main.go::Build", "p/main.go::MyInt")}
        assert generic == {("p/main.go::Build", "p/main.go::List")}
