"""Unit tests for JVM call resolution — companion objects, object singletons,
Java method references."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

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
