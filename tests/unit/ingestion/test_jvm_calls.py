"""Unit tests for JVM call resolution — companion objects, object singletons,
Java method references."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.call_resolver import CallResolver
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import parse_file


def _file_info(rel: str, abs_: Path, lang: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=str(abs_),
        language=lang,  # type: ignore[arg-type]
        size_bytes=abs_.stat().st_size,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _parse_all(tmp_path: Path, files: dict[str, tuple[str, str]]) -> dict[str, ParsedFile]:
    out: dict[str, ParsedFile] = {}
    for rel, (lang, content) in files.items():
        abs_ = tmp_path / rel
        abs_.parent.mkdir(parents=True, exist_ok=True)
        abs_.write_text(content)
        fi = _file_info(rel, abs_, lang)
        out[rel] = parse_file(fi, content.encode("utf-8"))
    return out


def _resolve_calls(
    parsed: dict[str, ParsedFile],
    tmp_path: Path,
    import_targets: dict[str, set[str]] | None = None,
):
    resolver = CallResolver(
        parsed,
        import_targets or {p: set() for p in parsed},
        repo_path=str(tmp_path),
    )
    edges: list[tuple[str, str, float]] = []
    for path, pf in parsed.items():
        for rc in resolver.resolve_file(path, pf.calls):
            edges.append((rc.caller_id, rc.callee_id, rc.confidence))
    return edges


class TestKotlinCompanionAndObject:
    def test_companion_object_method_call_resolves(self, tmp_path: Path) -> None:
        files = {
            "src/main/kotlin/com/foo/Caller.kt": (
                "kotlin",
                "package com.foo\n"
                "class Caller {\n"
                "  fun run() { Foo.build() }\n"
                "}\n",
            ),
            "src/main/kotlin/com/foo/Foo.kt": (
                "kotlin",
                "package com.foo\n"
                "class Foo {\n"
                "  val x = 1\n"
                "  companion object {\n"
                "    fun build(): Int = 1\n"
                "  }\n"
                "}\n",
            ),
        }
        parsed = _parse_all(tmp_path, files)
        edges = _resolve_calls(parsed, tmp_path)
        assert any(
            "Caller.kt::Caller::run" in u
            and "Foo.kt::Foo::build" in v
            for u, v, _ in edges
        ), edges

    def test_object_singleton_method_call_resolves(self, tmp_path: Path) -> None:
        files = {
            "src/main/kotlin/com/foo/Caller.kt": (
                "kotlin",
                "package com.foo\n"
                "class Caller {\n"
                "  fun run() { Single.do_it() }\n"
                "}\n",
            ),
            "src/main/kotlin/com/foo/Single.kt": (
                "kotlin",
                "package com.foo\n"
                "object Single { fun do_it(): Int = 1 }\n",
            ),
        }
        parsed = _parse_all(tmp_path, files)
        edges = _resolve_calls(parsed, tmp_path)
        assert any(
            "Caller.kt::Caller::run" in u
            and "Single.kt::Single::do_it" in v
            for u, v, _ in edges
        ), edges


class TestJavaMethodReference:
    def test_method_reference_is_captured_as_call(self, tmp_path: Path) -> None:
        """``Foo::bar`` should appear in CallSite list as receiver=Foo / target=bar."""
        files = {
            "src/main/java/com/foo/C.java": (
                "java",
                "package com.foo;\n"
                "import java.util.stream.Stream;\n"
                "class C {\n"
                "  void m() { Stream.of(1).map(Foo::bar); }\n"
                "}\n",
            ),
        }
        parsed = _parse_all(tmp_path, files)
        calls = parsed["src/main/java/com/foo/C.java"].calls
        assert any(
            c.receiver_name == "Foo" and c.target_name == "bar"
            for c in calls
        ), calls

    def test_method_reference_resolves_to_target_method(self, tmp_path: Path) -> None:
        files = {
            "src/main/java/com/foo/C.java": (
                "java",
                "package com.foo;\n"
                "import java.util.stream.Stream;\n"
                "class C {\n"
                "  void m() { Stream.of(1).map(Foo::bar); }\n"
                "}\n",
            ),
            "src/main/java/com/foo/Foo.java": (
                "java",
                "package com.foo;\n"
                "public class Foo {\n"
                "  public static Integer bar(Integer x) { return x; }\n"
                "}\n",
            ),
        }
        parsed = _parse_all(tmp_path, files)
        edges = _resolve_calls(parsed, tmp_path)
        assert any(
            "C.java::C::m" in u and "Foo.java::Foo::bar" in v
            for u, v, _ in edges
        ), edges


class TestKotlinImportBindings:
    """Kotlin ``as`` aliases and ``.*`` wildcards, through the call resolver."""

    def _parse_with_imports(
        self, tmp_path: Path, files: dict[str, tuple[str, str]], resolved: dict[str, str]
    ) -> dict[str, ParsedFile]:
        # Stand in for the graph builder: pin each import to its target file.
        parsed = _parse_all(tmp_path, files)
        for pf in parsed.values():
            for imp in pf.imports:
                imp.resolved_file = resolved.get(imp.module_path)
        return parsed

    def test_aliased_call_resolves_to_original_symbol(self, tmp_path: Path) -> None:
        caller = "src/main/kotlin/com/app/App.kt"
        lib = "src/main/kotlin/com/lib/Greeting.kt"
        files = {
            caller: (
                "kotlin",
                "package com.app\n"
                "import com.lib.greet as hello\n"
                "fun run() { hello() }\n",
            ),
            lib: ("kotlin", "package com.lib\nfun greet(): Int = 1\n"),
        }
        parsed = self._parse_with_imports(tmp_path, files, {"com.lib.greet": lib})
        edges = _resolve_calls(parsed, tmp_path, {caller: {lib}, lib: set()})
        assert any(
            u.endswith("App.kt::run") and v.endswith("Greeting.kt::greet") for u, v, _ in edges
        ), edges

    def test_backticked_keyword_alias_resolves(self, tmp_path: Path) -> None:
        # Call targets keep their backticks, so the alias must keep them too.
        caller = "src/main/kotlin/com/app/App.kt"
        lib = "src/main/kotlin/com/lib/Greeting.kt"
        files = {
            caller: (
                "kotlin",
                "package com.app\n"
                "import com.lib.greet as `in`\n"
                "fun run() { `in`() }\n",
            ),
            lib: ("kotlin", "package com.lib\nfun greet(): Int = 1\n"),
        }
        parsed = self._parse_with_imports(tmp_path, files, {"com.lib.greet": lib})
        edges = _resolve_calls(parsed, tmp_path, {caller: {lib}, lib: set()})
        assert any(
            u.endswith("App.kt::run") and v.endswith("Greeting.kt::greet") for u, v, _ in edges
        ), edges

    def test_wildcard_import_binds_no_package_name(self, tmp_path: Path) -> None:
        caller = "src/main/kotlin/com/app/App.kt"
        helper = "src/main/kotlin/com/lib/routing/Helper.kt"
        routing = "src/main/kotlin/com/lib/routing/Routing.kt"
        files = {
            caller: (
                "kotlin",
                "package com.app\n"
                "import com.lib.routing.*\n"
                "fun run() { routing() }\n",
            ),
            helper: ("kotlin", "package com.lib.routing\nfun helper(): Int = 1\n"),
            routing: ("kotlin", "package com.lib.routing\nfun routing(): Int = 1\n"),
        }
        # The wildcard resolves to one representative file of the package,
        # which is not the file declaring ``routing``.
        parsed = self._parse_with_imports(tmp_path, files, {"com.lib.routing.*": helper})
        (imp,) = parsed[caller].imports
        assert imp.imported_names == ["*"]
        assert [b.local_name for b in imp.bindings] == ["*"]
        edges = _resolve_calls(
            parsed, tmp_path, {caller: {helper, routing}, helper: set(), routing: set()}
        )
        assert any(
            u.endswith("App.kt::run") and v.endswith("Routing.kt::routing") for u, v, _ in edges
        ), edges


class TestJavaAnnotationElements:
    def test_an_element_call_binds_to_the_annotation_type(self, tmp_path: Path) -> None:
        """``since.value()`` reads an element of the ``@interface``, a method of it."""
        files = {
            "p/Since.java": ("java", "package p;\npublic @interface Since {\n    double value();\n}\n"),
            "p/Use.java": (
                "java",
                "package p;\nclass Use {\n"
                "    boolean ok(Since since) {\n        return since.value() > 1;\n    }\n}\n",
            ),
        }
        edges = _resolve_calls(_parse_all(tmp_path, files), tmp_path)
        assert [(u, v) for u, v, _ in edges] == [("p/Use.java::Use::ok", "p/Since.java::Since::value")]
