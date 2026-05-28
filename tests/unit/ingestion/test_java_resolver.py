"""Unit tests for the Java import resolver."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.java import (
    resolve_java_import,
    resolve_java_import_all,
)


def _ctx(repo: Path, paths: list[str]) -> ResolverContext:
    path_set = set(paths)
    stem_map: dict[str, list[str]] = {}
    for p in paths:
        stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=path_set,
        stem_map=stem_map,
        graph=nx.DiGraph(),
        repo_path=repo,
    )


def _make_java(repo: Path, rel_path: str, package: str, class_name: str) -> str:
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(f"package {package};\n\npublic class {class_name} {{}}\n")
    return rel_path


def _make_kotlin(repo: Path, rel_path: str, package: str, class_name: str) -> str:
    full = repo / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(f"package {package}\n\nclass {class_name}\n")
    return rel_path


class TestJavaImportResolution:
    def test_single_class_import(self, tmp_path: Path) -> None:
        a = _make_java(tmp_path, "src/main/java/com/foo/Bar.java", "com.foo", "Bar")
        ctx = _ctx(tmp_path, [a])
        result = resolve_java_import("com.foo.Bar", "src/main/java/Main.java", ctx)
        assert result == a

    def test_wildcard_import_fans_out(self, tmp_path: Path) -> None:
        a = _make_java(tmp_path, "src/main/java/com/foo/A.java", "com.foo", "A")
        b = _make_java(tmp_path, "src/main/java/com/foo/B.java", "com.foo", "B")
        ctx = _ctx(tmp_path, [a, b])
        targets = resolve_java_import_all("com.foo.*", "src/main/java/Main.java", ctx)
        assert len(targets) == 2
        assert a in targets
        assert b in targets

    def test_java_lang_filtered(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, [])
        result = resolve_java_import("java.lang.String", "Main.java", ctx)
        assert result is None

    def test_java_lang_object_filtered(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, [])
        targets = resolve_java_import_all("java.lang.Object", "Main.java", ctx)
        assert targets == ()

    def test_cross_language_java_to_kotlin(self, tmp_path: Path) -> None:
        kt = _make_kotlin(tmp_path, "src/main/java/com/foo/Service.kt", "com.foo", "Service")
        ctx = _ctx(tmp_path, [kt])
        result = resolve_java_import("com.foo.Service", "src/main/java/Main.java", ctx)
        assert result == kt

    def test_external_package(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, [])
        result = resolve_java_import("org.springframework.boot.SpringApplication", "Main.java", ctx)
        assert result is not None
        assert result.startswith("external:")

    def test_static_wildcard(self, tmp_path: Path) -> None:
        a = _make_java(tmp_path, "src/main/java/com/foo/Constants.java", "com.foo", "Constants")
        ctx = _ctx(tmp_path, [a])
        # import static com.foo.Constants.* → resolve to Constants file
        targets = resolve_java_import_all("com.foo.Constants", "Main.java", ctx)
        assert a in targets

    def test_stem_fallback(self, tmp_path: Path) -> None:
        a = _make_java(tmp_path, "src/Util.java", "com.foo", "Util")
        ctx = _ctx(tmp_path, [a])
        result = resolve_java_import("com.bar.Util", "Main.java", ctx)
        assert result == a

    def test_package_fan_out_single_import(self, tmp_path: Path) -> None:
        """A single-class import resolves to the specific file, not all package files."""
        a = _make_java(tmp_path, "src/main/java/com/foo/A.java", "com.foo", "A")
        b = _make_java(tmp_path, "src/main/java/com/foo/B.java", "com.foo", "B")
        ctx = _ctx(tmp_path, [a, b])
        targets = resolve_java_import_all("com.foo.A", "Main.java", ctx)
        assert targets == (a,)
