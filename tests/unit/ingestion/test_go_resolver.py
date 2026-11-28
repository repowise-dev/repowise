"""Unit tests for the Go multi-module resolver."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.go import (
    read_go_modules,
    resolve_go_import,
    resolve_go_import_all,
)
from repowise.core.ingestion.resolvers.go_workspace import (
    build_go_package_index,
    exported_go_files,
)
from repowise.core.ingestion.extractors.bindings.go import (
    build_go_directory_package_map,
)


def _ctx(repo: Path, paths: list[str], go_modules: tuple = ()) -> ResolverContext:
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
        go_modules=go_modules,
    )


class TestReadGoModules:
    def test_finds_root_and_nested_modules(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module github.com/me/root\n")
        (tmp_path / "services" / "foo").mkdir(parents=True)
        (tmp_path / "services" / "foo" / "go.mod").write_text(
            "module github.com/me/foo\n"
        )
        modules = read_go_modules(tmp_path)
        # Longest first: foo's path is shorter than root's? Both equal length
        # — but ``github.com/me/foo`` (19) vs ``github.com/me/root`` (20).
        # We just assert both modules are present.
        names = [m for _, m in modules]
        assert "github.com/me/root" in names
        assert "github.com/me/foo" in names

    def test_skips_vendor(self, tmp_path: Path) -> None:
        (tmp_path / "vendor" / "x").mkdir(parents=True)
        (tmp_path / "vendor" / "x" / "go.mod").write_text("module vendored\n")
        assert read_go_modules(tmp_path) == ()


class TestResolveGoImport:
    def test_multi_module_picks_nearest_module(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            ["services/foo/handler.go", "libs/bar/util.go"],
            go_modules=(
                ("services/foo", "github.com/me/foo"),
                ("libs/bar", "github.com/me/bar"),
            ),
        )
        # Sort longest-first as the real builder does.
        ctx.go_modules = tuple(sorted(ctx.go_modules, key=lambda t: len(t[1]), reverse=True))
        result = resolve_go_import("github.com/me/bar", "services/foo/handler.go", ctx)
        assert result == "libs/bar/util.go"

    def test_single_module_back_compat(self, tmp_path: Path) -> None:
        # Build via legacy ``go_module_path`` only (no go_modules tuple).
        path_set = {"pkg/util.go"}
        stem_map = {"util": ["pkg/util.go"]}
        ctx = ResolverContext(
            path_set=path_set,
            stem_map=stem_map,
            graph=nx.DiGraph(),
            repo_path=tmp_path,
            go_module_path="github.com/me/root",
        )
        result = resolve_go_import("github.com/me/root/pkg", "main.go", ctx)
        assert result == "pkg/util.go"

    def test_external_falls_back(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["pkg/util.go"])
        result = resolve_go_import("github.com/external/lib", "main.go", ctx)
        assert result == "external:github.com/external/lib"


class TestResolveGoImportAll:
    def _write(self, repo: Path, rel: str, body: str = "package p\n") -> None:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    def test_returns_all_files_in_package(self, tmp_path: Path) -> None:
        files = [
            "cache/filecache/filecache.go",
            "cache/filecache/filecache_config.go",
            "cache/filecache/filecache_pruner.go",
        ]
        for f in files:
            self._write(tmp_path, f, "package filecache\n")
        ctx = _ctx(tmp_path, files, go_modules=(("", "github.com/gohugoio/hugo"),))
        result = resolve_go_import_all(
            "github.com/gohugoio/hugo/cache/filecache", "main.go", ctx
        )
        assert set(result) == set(files)

    def test_external_is_single_node(self, tmp_path: Path) -> None:
        self._write(tmp_path, "main.go", "package main\n")
        ctx = _ctx(tmp_path, ["main.go"], go_modules=(("", "github.com/me/app"),))
        result = resolve_go_import_all("github.com/spf13/cobra", "main.go", ctx)
        assert result == ("external:github.com/spf13/cobra",)


class TestLegacyModulePathFallback:
    def test_fallback_resolves_imports_but_exports_nothing(self, tmp_path: Path) -> None:
        # Legacy ``go_module_path`` only: no go_modules tuple and no go.mod on disk.
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "util.go").write_text(
            "package pkg\n\nfunc Util() {}\n", encoding="utf-8"
        )
        ctx = ResolverContext(
            path_set={"pkg/util.go"},
            stem_map={"util": ["pkg/util.go"]},
            graph=nx.DiGraph(),
            repo_path=tmp_path,
            go_module_path="github.com/me/root",
        )
        index = build_go_package_index(ctx)
        # The fallback still maps import paths...
        assert index.import_path_to_dir == {"github.com/me/root/pkg": "pkg"}
        # ...but it is not a real module, so nothing counts as exported.
        assert index.modules == ()
        assert exported_go_files(index) == []


# ---------------------------------------------------------------------------
# Calls through an interface-typed variable
# ---------------------------------------------------------------------------

_IFACE_SOURCES: dict[str, str] = {
    "go.mod": "module example.com/md\n\ngo 1.22\n",
    "text/reader.go": (
        "package text\n\n"
        'import "io"\n\n'
        "type Base interface{ Close() error }\n\n"
        "type Reader interface {\n\tio.RuneReader\n\tBase\n\tPeek() byte\n\tAdvance(n int)\n}\n\n"
        "type reader struct{ src []byte; pos int }\n\n"
        "func (r *reader) Peek() byte { return r.src[r.pos] }\n"
        "func (r *reader) Advance(n int) { r.pos += n }\n\n"
        "func NewReader(src []byte) Reader { return &reader{src: src} }\n"
    ),
    "text/block.go": (
        "package text\n\n"
        "type blockReader struct{ pos int }\n\n"
        "func (b *blockReader) Peek() byte { return 0 }\n"
        "func (b *blockReader) Advance(n int) { b.pos += n }\n"
    ),
    "parser/parser.go": (
        "package parser\n\n"
        'import "example.com/md/text"\n\n'
        "func parseParam(reader text.Reader) byte {\n\treader.Advance(1)\n\treturn reader.Peek()\n}\n\n"
        "func parseVar(src []byte) byte {\n"
        "\tvar reader text.Reader = text.NewReader(src)\n\treturn reader.Peek()\n}\n\n"
        "func parseOther(r text.Reader) byte { return r.Peek() }\n\n"
        "func promotedInRepo(r text.Reader) error { return r.Close() }\n\n"
        "func promotedExternal(r text.Reader) { r.ReadRune() }\n"
    ),
}


def _call_edges(repo: Path, sources: dict[str, str]) -> set[tuple[str, str]]:
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for rel, body in sources.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    for rel in sources:
        if not rel.endswith(".go"):
            continue
        info = FileInfo(
            path=rel,
            abs_path=str((repo / rel).resolve()),
            language="go",
            size_bytes=100,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        builder.add_file(parser.parse_file(info, (repo / rel).read_bytes()))
    graph = builder.build()
    return {
        (u, v) for u, v, d in graph.edges(data=True) if d.get("edge_type") == "calls"
    }


class TestInterfaceTypedReceiverCalls:
    def test_a_parameter_named_like_a_struct_binds_to_the_interface(self, tmp_path: Path) -> None:
        edges = _call_edges(tmp_path, _IFACE_SOURCES)
        caller = "parser/parser.go::parseParam"
        assert (caller, "text/reader.go::Reader::Peek") in edges
        assert (caller, "text/reader.go::Reader::Advance") in edges

    def test_a_var_named_like_a_struct_binds_to_the_interface(self, tmp_path: Path) -> None:
        edges = _call_edges(tmp_path, _IFACE_SOURCES)
        assert ("parser/parser.go::parseVar", "text/reader.go::Reader::Peek") in edges

    def test_an_interface_variable_with_any_other_name_binds_to_the_interface(
        self, tmp_path: Path
    ) -> None:
        edges = _call_edges(tmp_path, _IFACE_SOURCES)
        assert ("parser/parser.go::parseOther", "text/reader.go::Reader::Peek") in edges

    def test_no_call_binds_to_an_implementer(self, tmp_path: Path) -> None:
        edges = _call_edges(tmp_path, _IFACE_SOURCES)
        for caller, callee in edges:
            if caller.startswith("parser/parser.go::"):
                assert "::reader::" not in callee, (caller, callee)
                assert "::blockReader::" not in callee, (caller, callee)

    def test_a_method_promoted_from_an_in_repo_embedded_interface_binds_its_spec(
        self, tmp_path: Path
    ) -> None:
        edges = _call_edges(tmp_path, _IFACE_SOURCES)
        assert ("parser/parser.go::promotedInRepo", "text/reader.go::Base::Close") in edges

    def test_a_method_promoted_from_an_external_interface_binds_nothing(
        self, tmp_path: Path
    ) -> None:
        edges = _call_edges(tmp_path, _IFACE_SOURCES)
        assert not {e for e in edges if e[0] == "parser/parser.go::promotedExternal"}

    def test_a_concrete_receiver_still_binds_to_its_own_method(self, tmp_path: Path) -> None:
        sources = dict(_IFACE_SOURCES)
        sources["parser/concrete.go"] = (
            "package parser\n\n"
            'import "example.com/md/text"\n\n'
            "func viaStruct(reader *text.Impl) byte { return reader.Peek() }\n"
        )
        sources["text/impl.go"] = (
            "package text\n\ntype Impl struct{}\n\nfunc (i *Impl) Peek() byte { return 0 }\n"
        )
        edges = _call_edges(tmp_path, sources)
        assert ("parser/concrete.go::viaStruct", "text/impl.go::Impl::Peek") in edges

    def test_a_spec_is_not_paired_with_a_same_named_type_in_another_package(
        self, tmp_path: Path
    ) -> None:
        sources = {
            "go.mod": "module example.com/app\n\ngo 1.22\n",
            "detect/store.go": (
                "package detect\n\ntype Store interface{ Save() int }\n\n"
                "func Open() Store { return nil }\n"
            ),
            "other/store.go": (
                "package other\n\ntype Store struct{}\n\n"
                "func (s *Store) Save() int { return 1 }\n"
            ),
            "cmd/run.go": (
                'package cmd\n\nimport "example.com/app/detect"\n\n'
                "func run() {\n\ts := detect.Open()\n\ts.Save()\n}\n"
            ),
        }
        edges = _call_edges(tmp_path, sources)
        assert ("cmd/run.go::run", "detect/store.go::Store::Save") in edges
        assert ("cmd/run.go::run", "other/store.go::Store::Save") not in edges


class TestPackageClauseImportBindings:
    def test_explicit_alias_flag_is_recorded_on_binding(self) -> None:
        parser = ASTParser()
        content = b'package main\n\nimport alias "example.com/m/pkg"\n'
        info = FileInfo(
            path="main.go",
            abs_path="/tmp/main.go",
            language="go",
            size_bytes=len(content),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed = parser.parse_file(info, content)
        (imp,) = parsed.imports
        assert imp.bindings[0].is_explicit_alias is True

    def test_directory_package_map_resolves_each_directory_once(self, tmp_path: Path) -> None:
        sources = {
            "go.mod": "module example.com/m\n\ngo 1.22\n",
            "db/db.go": "package db\n\nfunc Open() {}\n",
            "db/db_test.go": "package db_test\n",
        }
        for rel, body in sources.items():
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")

        parsed = {}
        parser = ASTParser()
        for rel in ("db/db.go", "db/db_test.go"):
            info = FileInfo(
                path=rel,
                abs_path=str((tmp_path / rel).resolve()),
                language="go",
                size_bytes=100,
                git_hash="",
                last_modified=datetime.now(),
                is_test=rel.endswith("_test.go"),
                is_config=False,
                is_api_contract=False,
                is_entry_point=False,
            )
            parsed[rel] = parser.parse_file(info, (tmp_path / rel).read_bytes())

        assert build_go_directory_package_map(parsed) == {"db": "db"}

    def test_go_foo_declaring_package_foo_gains_calls_edge(self, tmp_path: Path) -> None:
        sources = {
            "go.mod": "module example.com/m\n\ngo 1.22\n",
            "go-foo/foo.go": "package foo\n\nfunc Do() {}\n",
            "app/main.go": (
                "package main\n\n"
                'import "example.com/m/go-foo"\n\n'
                "func Run() {\n"
                "\tfoo.Do()\n"
                "}\n"
            ),
        }
        edges = _call_edges(tmp_path, sources)
        assert ("app/main.go::Run", "go-foo/foo.go::Do") in edges

    def test_explicit_alias_still_wins_over_package_clause(self, tmp_path: Path) -> None:
        sources = {
            "go.mod": "module example.com/m\n\ngo 1.22\n",
            "go-foo/foo.go": "package foo\n\nfunc Do() {}\n",
            "app/main.go": (
                "package main\n\n"
                'import myalias "example.com/m/go-foo"\n\n'
                "func Run() {\n"
                "\tmyalias.Do()\n"
                "}\n"
            ),
        }
        edges = _call_edges(tmp_path, sources)
        assert ("app/main.go::Run", "go-foo/foo.go::Do") in edges

    def test_external_version_suffixes_stripped_in_bindings(self) -> None:
        parser = ASTParser()
        content = (
            b"package main\n\n"
            b'import "gopkg.in/yaml.v3"\n'
            b'import "example.com/m/v2"\n'
            b'import custom "gopkg.in/check.v1"\n'
            b'import . "example.com/other/v3"\n'
            b'import _ "example.com/blank/v4"\n'
        )
        info = FileInfo(
            path="app/main.go",
            abs_path="/tmp/app/main.go",
            language="go",
            size_bytes=len(content),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed = parser.parse_file(info, content)
        by_path = {imp.module_path: imp for imp in parsed.imports}

        # yaml.v3 binds yaml
        yaml_imp = by_path["gopkg.in/yaml.v3"]
        assert yaml_imp.local_names == ["yaml"]
        assert yaml_imp.bindings[0].local_name == "yaml"

        # example.com/m/v2 binds m
        v2_imp = by_path["example.com/m/v2"]
        assert v2_imp.local_names == ["m"]
        assert v2_imp.bindings[0].local_name == "m"

        # Explicit alias custom wins
        custom_imp = by_path["gopkg.in/check.v1"]
        assert custom_imp.local_names == ["custom"]
        assert custom_imp.bindings[0].local_name == "custom"

        # Dot import binds *
        dot_imp = by_path["example.com/other/v3"]
        assert dot_imp.local_names == ["*"]
        assert dot_imp.bindings[0].local_name == "*"

        # Blank import binds nothing
        blank_imp = by_path["example.com/blank/v4"]
        assert blank_imp.local_names == []
        assert blank_imp.bindings == []

    def test_package_matching_path_element_unchanged(self, tmp_path: Path) -> None:
        sources = {
            "go.mod": "module example.com/m\n\ngo 1.22\n",
            "pkg/util.go": "package pkg\n\nfunc Helper() {}\n",
            "app/main.go": (
                "package main\n\n"
                'import "example.com/m/pkg"\n\n'
                "func Run() {\n"
                "\tpkg.Helper()\n"
                "}\n"
            ),
        }
        edges = _call_edges(tmp_path, sources)
        assert ("app/main.go::Run", "pkg/util.go::Helper") in edges

    def test_bare_major_version_import_fallback_to_version_element(self) -> None:
        parser = ASTParser()
        content = b'package main\n\nimport "gopkg.in/v2"\n'
        info = FileInfo(
            path="app/main.go",
            abs_path="/tmp/app/main.go",
            language="go",
            size_bytes=len(content),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed = parser.parse_file(info, content)
        (imp,) = parsed.imports
        # Does not leave dotted "gopkg.in"; falls back to valid identifier "v2"
        assert imp.local_names == ["v2"]
        assert imp.bindings[0].local_name == "v2"

    def test_sibling_package_clause_wins_over_first_main_file(self, tmp_path: Path) -> None:
        sources = {
            "go.mod": "module example.com/m\n\ngo 1.22\n",
            # Alphabetically first file has package main (e.g. generator/script)
            "db/0_gen.go": "package main\n\nfunc Gen() {}\n",
            "db/db.go": "package db\n\nfunc Open() {}\n",
            "app/main.go": (
                "package main\n\n"
                'import "example.com/m/db"\n\n'
                "func Run() {\n"
                "\tdb.Open()\n"
                "}\n"
            ),
        }
        edges = _call_edges(tmp_path, sources)
        assert ("app/main.go::Run", "db/db.go::Open") in edges


