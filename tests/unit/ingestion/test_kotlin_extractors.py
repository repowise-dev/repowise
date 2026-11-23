"""Unit tests for Kotlin symbol extraction.

Phase 1 baseline coverage — covers class, object, and interface
declarations. In ``tree-sitter-kotlin`` the ``interface Foo {}`` form
parses as a ``class_declaration`` with ``interface`` as the leading
keyword child (NOT a separate ``interface_declaration`` node), so the
existing ``class_declaration`` capture in ``kotlin.scm`` already picks
interfaces up. These tests pin that behavior so any future grammar
upgrade or query change is caught immediately.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser


def _file(path: str = "Foo.kt") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="kotlin",
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


class TestKotlinSymbols:
    def test_interface_captured_as_class_declaration(self, parser: ASTParser) -> None:
        # tree-sitter-kotlin parses `interface Foo {}` as class_declaration
        # with `interface` as a keyword child; the existing
        # class_declaration capture covers it (kind="class").
        src = b"""\
package com.example

interface Greeter {
    fun greet(): String
}
"""
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "Greeter" in names

    def test_class_declaration_still_captured(self, parser: ASTParser) -> None:
        src = b"""\
package com.example

class Foo {
    fun bar(): Int = 1
}
"""
        result = parser.parse_file(_file(), src)
        names = {(s.name, s.kind) for s in result.symbols}
        assert ("Foo", "class") in names

    def test_object_declaration_still_captured(self, parser: ASTParser) -> None:
        src = b"""\
package com.example

object Singleton {
    fun work() {}
}
"""
        result = parser.parse_file(_file(), src)
        names = {(s.name, s.kind) for s in result.symbols}
        assert ("Singleton", "class") in names

    def test_interface_alongside_class(self, parser: ASTParser) -> None:
        src = b"""\
package com.example

interface Repo
class UserRepo : Repo
"""
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert "Repo" in names
        assert "UserRepo" in names


class TestKotlinWildcardImports:
    def test_wildcard_import_keeps_star(self, parser: ASTParser) -> None:
        # The `*` is an anonymous sibling token of the qualified identifier
        # in the Kotlin grammar; extraction must restore it for package fan-out.
        src = b"package x\nimport com.example.util.*\nclass App\n"
        result = parser.parse_file(_file(), src)
        modules = [imp.module_path for imp in result.imports]
        assert "com.example.util.*" in modules

    def test_plain_import_unchanged(self, parser: ASTParser) -> None:
        src = b"package x\nimport com.example.Foo\nclass App\n"
        result = parser.parse_file(_file(), src)
        modules = [imp.module_path for imp in result.imports]
        assert modules == ["com.example.Foo"]

    def test_wildcard_import_binds_only_the_wildcard(self, parser: ASTParser) -> None:
        src = b"package x\nimport io.ktor.server.routing.*\nclass App\n"
        (imp,) = parser.parse_file(_file(), src).imports
        assert imp.imported_names == ["*"]
        assert [(b.local_name, b.exported_name) for b in imp.bindings] == [("*", None)]


class TestKotlinAliasImports:
    def test_alias_binds_alias_to_original_name(self, parser: ASTParser) -> None:
        src = b"package x\nimport com.example.Foo as Bar\nclass App\n"
        (imp,) = parser.parse_file(_file(), src).imports
        assert imp.module_path == "com.example.Foo"
        assert imp.imported_names == ["Foo"]
        assert [(b.local_name, b.exported_name) for b in imp.bindings] == [("Bar", "Foo")]
        assert imp.local_names == ["Bar"]

    def test_plain_import_binds_its_own_name(self, parser: ASTParser) -> None:
        src = b"package x\nimport com.example.Foo\nclass App\n"
        (imp,) = parser.parse_file(_file(), src).imports
        assert [(b.local_name, b.exported_name) for b in imp.bindings] == [("Foo", "Foo")]

    def test_dangling_as_takes_no_alias_from_the_next_line(self, parser: ASTParser) -> None:
        src = b"package x\nimport com.example.Foo as\nclass App\n"
        imp = parser.parse_file(_file(), src).imports[0]
        assert [(b.local_name, b.exported_name) for b in imp.bindings] == [("Foo", "Foo")]


class TestKotlinDetachedAnnotation:
    """A lone one-argument annotation the grammar reads as an expression.

    At top level, ``@Suppress("unused")`` on its own line before a bare ``fun``
    or ``object`` parses as an ``annotated_expression`` (the argument list read
    as a parenthesized expression) beside a declaration with no modifiers.
    """

    @staticmethod
    def _decorators(parser: ASTParser, src: bytes) -> dict[str, list[str]]:
        return {s.name: s.decorators for s in parser.parse_file(_file(), src).symbols}

    def test_function_keeps_its_annotation(self, parser: ASTParser) -> None:
        src = b'package x\n\n@Suppress("unused")\nfun a(): Int = 1\n\nfun z() {}\n'
        decorators = self._decorators(parser, src)
        assert decorators["a"] == ['@Suppress("unused")']
        assert decorators["z"] == []

    def test_extension_function_keeps_its_annotation(self, parser: ASTParser) -> None:
        src = b'package x\n\n@Deprecated("old")\nfun String.a(): String = this\n\nfun z() {}\n'
        assert self._decorators(parser, src)["a"] == ['@Deprecated("old")']

    def test_object_keeps_its_annotation(self, parser: ASTParser) -> None:
        src = b'package x\n\n@Suppress("UNUSED")\nobject A\n\nfun z() {}\n'
        assert self._decorators(parser, src)["A"] == ['@Suppress("UNUSED")']

    def test_annotation_stack_ending_in_one_argument(self, parser: ASTParser) -> None:
        src = b'package x\n\n@JvmStatic\n@Suppress("unused")\nfun a(x: Int) {}\n\nfun z() {}\n'
        assert self._decorators(parser, src)["a"] == ['@JvmStatic\n@Suppress("unused")']

    def test_comment_between_annotation_and_declaration(self, parser: ASTParser) -> None:
        src = b'package x\n\n@JvmName("b")\n// kept for Java callers\nfun a() {}\n\nfun z() {}\n'
        assert self._decorators(parser, src)["a"] == ['@JvmName("b")']

    def test_annotation_inside_modifiers_is_not_doubled(self, parser: ASTParser) -> None:
        src = b'package x\n\n@Suppress("unused", "X")\nfun a() {}\n\nfun z() {}\n'
        assert self._decorators(parser, src)["a"] == ['@Suppress("unused", "X")']

    def test_file_annotation_is_not_taken(self, parser: ASTParser) -> None:
        src = b'@file:Suppress("unused")\npackage x\n\nfun a() {}\n\nfun z() {}\n'
        assert self._decorators(parser, src)["a"] == []


class TestKotlinMultiAnnotationDeclarations:
    """A top-level declaration with two annotations can parse as an expression."""

    _SCREEN = b"""\
package app

@Preview
@Composable
private fun TwoAnnotationsPreview() {
    helper()
}

@Composable
private fun OneAnnotation() {
    helper()
}

@Preview @Composable private fun SameLine() {
    helper()
}

@Target(AnnotationTarget.FUNCTION)
@Retention(AnnotationRetention.BINARY)
annotation class ThemePreviews

@RunWith(JUnit4::class)
@Config(sdk = [35])
class ScreenTest {
    fun check() = helper()
}

@A @B private val p = 1

@A @B private object O {}

fun helper() {}
"""

    def test_two_annotation_declarations_become_symbols(self, parser: ASTParser) -> None:
        result = parser.parse_file(_file("app/Screen.kt"), self._SCREEN)
        by_name = {s.name: s for s in result.symbols}
        for name in ("TwoAnnotationsPreview", "SameLine", "ThemePreviews", "ScreenTest", "p", "O"):
            assert name in by_name, name
        assert by_name["check"].parent_name == "ScreenTest"

    def test_recovered_symbol_starts_at_first_annotation(self, parser: ASTParser) -> None:
        result = parser.parse_file(_file("app/Screen.kt"), self._SCREEN)
        by_name = {s.name: s for s in result.symbols}
        assert by_name["TwoAnnotationsPreview"].start_line == 3
        assert by_name["SameLine"].start_line == 14
        assert by_name["ThemePreviews"].start_line == 18
        assert by_name["ScreenTest"].start_line == 22

    def test_recovered_symbol_carries_both_annotations(self, parser: ASTParser) -> None:
        result = parser.parse_file(_file("app/Screen.kt"), self._SCREEN)
        by_name = {s.name: s for s in result.symbols}
        text = " ".join(by_name["TwoAnnotationsPreview"].decorators)
        assert "@Preview" in text and "@Composable" in text
        text = " ".join(by_name["ScreenTest"].decorators)
        assert "@RunWith(JUnit4::class)" in text and "@Config(sdk = [35])" in text
        assert by_name["TwoAnnotationsPreview"].visibility == "private"

    def test_calls_attributed_to_recovered_symbol_not_module(self, parser: ASTParser) -> None:
        result = parser.parse_file(_file("app/Screen.kt"), self._SCREEN)
        callers = {c.caller_symbol_id for c in result.calls if c.target_name == "helper"}
        assert "app/Screen.kt::TwoAnnotationsPreview" in callers
        assert "app/Screen.kt::SameLine" in callers
        assert "app/Screen.kt::__module__" not in callers

    def test_shape_as_last_declaration_parses_as_today(self, parser: ASTParser) -> None:
        src = b"package a\n\nfun helper() {}\n\n@Preview\n@Composable\nprivate fun Last() {}\n"
        names = {s.name for s in parser.parse_file(_file(), src).symbols}
        assert {"helper", "Last"} <= names

    def test_shapes_that_already_parse_are_unchanged(self, parser: ASTParser) -> None:
        src = b"""\
package a

class Host {
    @A
    @B
    private fun member() {}
}

@A @B private fun withParam(x: Int) {}

@A @B suspend fun susp() {}

fun after() {}
"""
        result = parser.parse_file(_file(), src)
        by_name = {s.name: s for s in result.symbols}
        assert {"Host", "member", "withParam", "susp", "after"} <= set(by_name)
        assert by_name["member"].parent_name == "Host"
        assert by_name["withParam"].start_line == 9

    def test_real_expression_in_body_is_not_rewritten(self, parser: ASTParser) -> None:
        src = b"""\
package a

fun outer() {
    @Suppress("x") foo()
}

fun after() {}
"""
        result = parser.parse_file(_file(), src)
        assert {s.name for s in result.symbols} == {"outer", "after"}


class TestKotlinMisparsedSpanRecovery:
    """Shapes tree-sitter-kotlin reads as an ERROR swallowing the class body."""

    def _parse(self, parser: ASTParser, src: bytes):
        return parser.parse_file(_file(), src)

    def test_primary_constructor_modifiers_on_their_own_line(self, parser: ASTParser) -> None:
        src = b"""\
package a

actual open class Bytes
internal actual constructor(
  internal actual val data: ByteArray,
) : Comparable<Bytes> {
  actual fun hex(): String = render()
}
"""
        result = self._parse(parser, src)
        assert result.parse_errors == []
        hex_ = next(s for s in result.symbols if s.name == "hex")
        assert hex_.parent_name == "Bytes"
        assert hex_.start_line == 7
        assert [c.target_name for c in result.calls] == ["render"]

    @pytest.mark.parametrize(
        "kw", ["final", "open", "abstract", "sealed", "override", "lateinit", "internal"]
    )
    def test_property_named_like_modifier_assigned_in_statement(
        self, parser: ASTParser, kw: str
    ) -> None:
        src = f"""\
package acme
class Job {{
    private var {kw} = false
    private var count = 0
    fun finish() {{
        {kw} = true
        bump()
    }}
    fun bump() {{ count += 1 }}
}}
""".encode()
        result = self._parse(parser, src)
        assert result.parse_errors == []
        names = {s.name for s in result.symbols}
        assert names == {"Job", kw, "count", "finish", "bump"}
        assert [c.target_name for c in result.calls] == ["bump"]

    def test_property_named_final_yields_call_edge_in_graph(
        self, parser: ASTParser, tmp_path
    ) -> None:
        from repowise.core.ingestion.graph import GraphBuilder

        src = b"""\
package acme
class Job {
    private var final = false
    private var count = 0
    fun finish() {
        final = true
        bump()
    }
    fun bump() { count += 1 }
}
"""
        fi = _file("src/main/kotlin/acme/Job.kt")
        result = parser.parse_file(fi, src)
        builder = GraphBuilder(repo_path=tmp_path)
        builder.add_file(result)
        graph = builder.build()
        edge = graph.get_edge_data(
            "src/main/kotlin/acme/Job.kt::Job::finish",
            "src/main/kotlin/acme/Job.kt::Job::bump",
        )
        assert edge is not None
        assert edge["edge_type"] == "calls"
        assert edge["confidence"] == 0.95

    def test_annotated_accessor_of_an_untyped_property(self, parser: ASTParser) -> None:
        src = b"""\
package a

class Bytes {
  actual val size
    @JvmName("size")
    get() = getSize()

  fun after() = 1
}
"""
        result = self._parse(parser, src)
        assert result.parse_errors == []
        assert "after" in {s.name for s in result.symbols}

    def test_get_call_statement_under_a_val(self, parser: ASTParser) -> None:
        src = b"""\
package a

class Bytes {
  fun of(): Bytes {
    val copy = ByteArray(remaining())
    get(copy)
    return Bytes(copy)
  }

  fun after() = 1
}
"""
        result = self._parse(parser, src)
        assert result.parse_errors == []
        assert "after" in {s.name for s in result.symbols}
        assert ("get", 6) in {(c.target_name, c.line) for c in result.calls}

    def test_route_get_calls_are_left_alone(self, parser: ASTParser) -> None:
        src = b"""\
package a

fun Route.listing() {
    routing {
        get("/") {
            respond()
        }
    }
}
"""
        result = self._parse(parser, src)
        assert result.parse_errors == []
        assert {"get", "respond"} <= {c.target_name for c in result.calls}

    def test_spans_outside_the_error_keep_their_text(self, parser: ASTParser) -> None:
        src = b"""\
package a

class Doc {
  /** Reads @Cached get(key) first. */
  fun doc() = 1
}

class Bytes {
  actual val size
    @JvmName("size")
    get() = getSize()
}
"""
        result = self._parse(parser, src)
        assert result.parse_errors == []
        doc = next(s for s in result.symbols if s.name == "doc")
        assert "@Cached get(key)" in (doc.docstring or "")
