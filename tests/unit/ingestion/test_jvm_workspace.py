"""Unit tests for the JVM workspace index."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.jvm_workspace import (
    JvmWorkspaceIndex,
    build_jvm_workspace_index,
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


class TestJvmWorkspaceIndex:
    def test_groups_files_by_package(self, tmp_path: Path) -> None:
        a = _make_java(tmp_path, "src/main/java/com/foo/A.java", "com.foo", "A")
        b = _make_java(tmp_path, "src/main/java/com/foo/B.java", "com.foo", "B")
        c = _make_java(tmp_path, "src/main/java/com/bar/C.java", "com.bar", "C")

        ctx = _ctx(tmp_path, [a, b, c])
        index = build_jvm_workspace_index(ctx)

        assert "com.foo" in index.packages
        assert len(index.packages["com.foo"].files) == 2
        assert "com.bar" in index.packages
        assert len(index.packages["com.bar"].files) == 1

    def test_mixed_java_kotlin_same_package(self, tmp_path: Path) -> None:
        j = _make_java(tmp_path, "src/main/java/com/foo/Foo.java", "com.foo", "Foo")
        k = _make_kotlin(tmp_path, "src/main/java/com/foo/Bar.kt", "com.foo", "Bar")

        ctx = _ctx(tmp_path, [j, k])
        index = build_jvm_workspace_index(ctx)

        pkg = index.packages["com.foo"]
        assert j in pkg.files
        assert k in pkg.files

    def test_files_for_fqn(self, tmp_path: Path) -> None:
        a = _make_java(tmp_path, "src/main/java/com/foo/MyClass.java", "com.foo", "MyClass")

        ctx = _ctx(tmp_path, [a])
        index = build_jvm_workspace_index(ctx)

        assert index.files_for_fqn("com.foo.MyClass") == (a,)

    def test_wildcard_expand(self, tmp_path: Path) -> None:
        a = _make_java(tmp_path, "src/main/java/com/foo/A.java", "com.foo", "A")
        b = _make_java(tmp_path, "src/main/java/com/foo/B.java", "com.foo", "B")

        ctx = _ctx(tmp_path, [a, b])
        index = build_jvm_workspace_index(ctx)

        files = index.wildcard_expand("com.foo")
        assert len(files) == 2

    def test_same_package_files(self, tmp_path: Path) -> None:
        a = _make_java(tmp_path, "src/main/java/com/foo/A.java", "com.foo", "A")
        b = _make_java(tmp_path, "src/main/java/com/foo/B.java", "com.foo", "B")

        ctx = _ctx(tmp_path, [a, b])
        index = build_jvm_workspace_index(ctx)

        siblings = index.same_package_files(a)
        assert b in siblings
        assert a not in siblings

    def test_is_java_lang(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, [])
        index = build_jvm_workspace_index(ctx)

        assert index.is_java_lang("java.lang.String")
        assert index.is_java_lang("java.lang.Object")
        assert not index.is_java_lang("com.foo.Bar")
        assert not index.is_java_lang("java.util.List")

    def test_file_to_package(self, tmp_path: Path) -> None:
        a = _make_java(tmp_path, "src/main/java/com/foo/A.java", "com.foo", "A")

        ctx = _ctx(tmp_path, [a])
        index = build_jvm_workspace_index(ctx)

        assert index.package_for_file(a) == "com.foo"

    def test_exported_top_level_types(self, tmp_path: Path) -> None:
        a = _make_java(tmp_path, "src/main/java/com/foo/Foo.java", "com.foo", "Foo")
        b = _make_kotlin(tmp_path, "src/main/kotlin/com/foo/Bar.kt", "com.foo", "Bar")

        ctx = _ctx(tmp_path, [a, b])
        index = build_jvm_workspace_index(ctx)

        pkg = index.packages["com.foo"]
        assert "Foo" in pkg.exported_top_level
        assert "Bar" in pkg.exported_top_level

    def test_meta_inf_services(self, tmp_path: Path) -> None:
        services_dir = tmp_path / "src" / "main" / "resources" / "META-INF" / "services"
        services_dir.mkdir(parents=True)
        (services_dir / "com.example.Plugin").write_text(
            "com.example.impl.PluginA\n# comment\ncom.example.impl.PluginB\n"
        )
        a = _make_java(tmp_path, "src/main/java/com/example/impl/PluginA.java",
                       "com.example.impl", "PluginA")

        ctx = _ctx(tmp_path, [a])
        index = build_jvm_workspace_index(ctx)

        assert "com.example.Plugin" in index.services
        impls = index.services["com.example.Plugin"]
        assert "com.example.impl.PluginA" in impls
        assert "com.example.impl.PluginB" in impls

    def test_spring_autoconfig_imports(self, tmp_path: Path) -> None:
        imports_dir = tmp_path / "src" / "main" / "resources" / "META-INF" / "spring"
        imports_dir.mkdir(parents=True)
        (imports_dir / "org.springframework.boot.autoconfigure.AutoConfiguration.imports").write_text(
            "com.example.MyAutoConfig\n# another\ncom.example.OtherConfig\n"
        )
        a = _make_java(tmp_path, "src/main/java/com/example/MyAutoConfig.java",
                       "com.example", "MyAutoConfig")

        ctx = _ctx(tmp_path, [a])
        index = build_jvm_workspace_index(ctx)

        assert len(index.autoconfig_imports) == 1
        key = list(index.autoconfig_imports.keys())[0]
        fqns = index.autoconfig_imports[key]
        assert "com.example.MyAutoConfig" in fqns
        assert "com.example.OtherConfig" in fqns


class TestMemberFqnResolution:
    def test_kotlin_companion_member_import_resolves_to_declaring_type(
        self, tmp_path: Path
    ) -> None:
        # okio regression: ``import okio.ByteString.Companion.encodeUtf8``
        # names a companion member — strip trailing segments until the
        # prefix is a local type.
        b = _make_kotlin(tmp_path, "src/main/java/okio/ByteString.kt", "okio", "ByteString")
        ctx = _ctx(tmp_path, [b])
        index = build_jvm_workspace_index(ctx)
        assert index.files_for_member_fqn("okio.ByteString.Companion.encodeUtf8") == (b,)
        assert index.files_for_member_fqn("okio.ByteString.Companion") == (b,)

    def test_java_static_member_import_resolves(self, tmp_path: Path) -> None:
        a = _make_java(tmp_path, "src/main/java/com/foo/Bar.java", "com.foo", "Bar")
        ctx = _ctx(tmp_path, [a])
        index = build_jvm_workspace_index(ctx)
        assert index.files_for_member_fqn("com.foo.Bar.CONSTANT") == (a,)

    def test_unknown_prefix_returns_empty(self, tmp_path: Path) -> None:
        a = _make_java(tmp_path, "src/main/java/com/foo/Bar.java", "com.foo", "Bar")
        ctx = _ctx(tmp_path, [a])
        index = build_jvm_workspace_index(ctx)
        assert index.files_for_member_fqn("org.junit.Assert.assertEquals") == ()

    def test_kotlin_resolver_emits_member_import_edge(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.resolvers.kotlin import resolve_kotlin_import_all

        b = _make_kotlin(tmp_path, "src/main/java/okio/ByteString.kt", "okio", "ByteString")
        u = _make_kotlin(tmp_path, "src/main/java/okio/User.kt", "okio", "User")
        ctx = _ctx(tmp_path, [b, u])
        targets = resolve_kotlin_import_all(
            "okio.ByteString.Companion.encodeUtf8", u, ctx
        )
        assert targets == (b,)

    def test_kotlin_member_wildcard_import_resolves(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.resolvers.kotlin import resolve_kotlin_import_all

        b = _make_kotlin(tmp_path, "src/main/java/okio/ByteString.kt", "okio", "ByteString")
        u = _make_kotlin(tmp_path, "src/main/java/okio/User.kt", "okio", "User")
        ctx = _ctx(tmp_path, [b, u])
        # ``import okio.ByteString.Companion.*`` — the prefix is a type, not
        # a package.
        targets = resolve_kotlin_import_all("okio.ByteString.Companion.*", u, ctx)
        assert targets == (b,)
