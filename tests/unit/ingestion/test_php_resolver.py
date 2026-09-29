"""Unit tests for the PHP / composer.json-aware import resolver."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.php import resolve_php_import
from repowise.core.ingestion.resolvers.php_composer import (
    get_or_build_psr4_map,
    resolve_via_psr4,
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


def _write_composer(repo: Path, autoload: dict, autoload_dev: dict | None = None) -> None:
    data: dict = {"autoload": {"psr-4": autoload}}
    if autoload_dev is not None:
        data["autoload-dev"] = {"psr-4": autoload_dev}
    (repo / "composer.json").write_text(json.dumps(data))


class TestPsr4Map:
    def test_psr4_single_string_value(self, tmp_path: Path) -> None:
        _write_composer(tmp_path, {"App\\": "src/"})
        assert get_or_build_psr4_map(_ctx(tmp_path, [])) == {"App\\": ["src"]}

    def test_psr4_list_value(self, tmp_path: Path) -> None:
        _write_composer(tmp_path, {"App\\": ["src/", "lib/"]})
        assert get_or_build_psr4_map(_ctx(tmp_path, [])) == {"App\\": ["src", "lib"]}

    def test_psr4_merges_autoload_dev(self, tmp_path: Path) -> None:
        _write_composer(tmp_path, {"App\\": "src/"}, autoload_dev={"Tests\\": "tests/"})
        psr4 = get_or_build_psr4_map(_ctx(tmp_path, []))
        assert psr4 == {"App\\": ["src"], "Tests\\": ["tests"]}

    def test_missing_composer(self, tmp_path: Path) -> None:
        assert get_or_build_psr4_map(_ctx(tmp_path, [])) == {}

    def test_nested_manifest_is_rebased_after_the_root(self, tmp_path: Path) -> None:
        _write_composer(tmp_path, {"App\\": "app/"})
        pkg = tmp_path / "packages" / "billing"
        pkg.mkdir(parents=True)
        _write_composer(pkg, {"Billing\\": "src/", "App\\": "extra/"})
        psr4 = get_or_build_psr4_map(_ctx(tmp_path, []))
        assert psr4 == {
            "App\\": ["app", "packages/billing/extra"],
            "Billing\\": ["packages/billing/src"],
        }

    def test_vendor_packages_are_left_out(self, tmp_path: Path) -> None:
        _write_composer(tmp_path, {"App\\": "app/"})
        dep = tmp_path / "vendor" / "acme" / "lib"
        dep.mkdir(parents=True)
        _write_composer(dep, {"Acme\\": "src/"})
        assert get_or_build_psr4_map(_ctx(tmp_path, [])) == {"App\\": ["app"]}


class TestPsr4Resolution:
    def test_longest_prefix_wins(self, tmp_path: Path) -> None:
        _write_composer(tmp_path, {"App\\": "src/", "App\\Foo\\": "lib/"})
        ctx = _ctx(tmp_path, ["src/Bar.php", "lib/Baz.php"])
        # App\Foo\Baz should hit the longer prefix and resolve under lib/.
        assert resolve_via_psr4("App\\Foo\\Baz", ctx) == "lib/Baz.php"

    def test_psr4_resolves_nested_namespace(self, tmp_path: Path) -> None:
        _write_composer(tmp_path, {"App\\": "src/"})
        ctx = _ctx(tmp_path, ["src/Models/User.php"])
        assert resolve_via_psr4("App\\Models\\User", ctx) == "src/Models/User.php"

    def test_shorter_prefix_is_tried_when_the_longest_misses(self, tmp_path: Path) -> None:
        # Composer's loader falls back to shorter prefixes; laravel/framework
        # maps Illuminate\Support\ to four dirs and leaves Str to Illuminate\.
        _write_composer(
            tmp_path,
            {
                "Illuminate\\": "src/Illuminate/",
                "Illuminate\\Support\\": ["src/Illuminate/Macroable/"],
            },
        )
        ctx = _ctx(tmp_path, ["src/Illuminate/Support/Str.php", "types/Support/Str.php"])
        got = resolve_via_psr4("Illuminate\\Support\\Str", ctx)
        assert got == "src/Illuminate/Support/Str.php"

    def test_nested_package_prefix_resolves(self, tmp_path: Path) -> None:
        pkg = tmp_path / "packages" / "billing"
        pkg.mkdir(parents=True)
        _write_composer(pkg, {"Billing\\": "src/"})
        ctx = _ctx(tmp_path, ["packages/billing/src/Invoice.php"])
        assert resolve_via_psr4("Billing\\Invoice", ctx) == "packages/billing/src/Invoice.php"

    def test_falls_through_when_no_match(self, tmp_path: Path) -> None:
        _write_composer(tmp_path, {"App\\": "src/"})
        ctx = _ctx(tmp_path, ["src/Foo.php"])
        assert resolve_via_psr4("Vendor\\Lib\\Thing", ctx) is None


class TestPhpResolverIntegration:
    def test_psr4_match_takes_priority_over_stem(self, tmp_path: Path) -> None:
        # Two ``Foo.php`` files exist; PSR-4 should pick the one under src/.
        _write_composer(tmp_path, {"App\\": "src/"})
        ctx = _ctx(tmp_path, ["src/Models/Foo.php", "vendor/other/Foo.php"])
        result = resolve_php_import("App\\Models\\Foo", "src/Models/Foo.php", ctx)
        assert result == "src/Models/Foo.php"

    def test_missing_composer_falls_through_to_stem_lookup(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["lib/Foo.php"])
        result = resolve_php_import("Foo", "lib/Foo.php", ctx)
        assert result == "lib/Foo.php"

    def test_unknown_namespace_becomes_external(self, tmp_path: Path) -> None:
        _write_composer(tmp_path, {"App\\": "src/"})
        ctx = _ctx(tmp_path, ["src/Foo.php"])
        result = resolve_php_import("Vendor\\Lib\\Missing", "src/Foo.php", ctx)
        assert result == "external:Vendor\\Lib\\Missing"

    def test_dependency_namespace_does_not_stem_match_a_local_file(self, tmp_path: Path) -> None:
        # Illuminate\Http\Request is a vendor class; the local Request.php
        # and config/request.php must not claim it.
        _write_composer(tmp_path, {"App\\": "app/"})
        ctx = _ctx(tmp_path, ["app/Http/Requests/Request.php", "config/request.php"])
        result = resolve_php_import("Illuminate\\Http\\Request", "app/Http/Kernel.php", ctx)
        assert result == "external:Illuminate\\Http\\Request"

    def test_catch_all_prefix_claims_every_namespace(self, tmp_path: Path) -> None:
        _write_composer(tmp_path, {"": "src/"})
        ctx = _ctx(tmp_path, ["legacy/Mailer.php"])
        got = resolve_php_import("Foo\\Mailer", "src/X.php", ctx)
        assert got == "legacy/Mailer.php"

    def test_classmapped_file_may_still_claim_an_unprefixed_class(self, tmp_path: Path) -> None:
        (tmp_path / "composer.json").write_text(
            json.dumps({"autoload": {"psr-4": {"App\\": "app/"}, "classmap": ["legacy/"]}})
        )
        ctx = _ctx(tmp_path, ["legacy/Mailer.php", "other/Request.php"])
        assert resolve_php_import("Old\\Mailer", "app/X.php", ctx) == "legacy/Mailer.php"
        assert resolve_php_import("Http\\Request", "app/X.php", ctx) == "external:Http\\Request"

    def test_name_fallback_needs_the_whole_file_name(self, tmp_path: Path) -> None:
        # No composer: the class-name fallback must not take LoginRequest.php
        # for Request.
        ctx = _ctx(tmp_path, ["app/Http/Requests/Auth/LoginRequest.php"])
        got = resolve_php_import("Illuminate\\Http\\Request", "app/X.php", ctx)
        assert got == "external:Illuminate\\Http\\Request"

    def test_first_party_namespace_keeps_the_stem_fallback(self, tmp_path: Path) -> None:
        # A claimed prefix whose directory layout PSR-4 cannot reach still
        # falls back to the class name.
        _write_composer(tmp_path, {"App\\": "app/"})
        ctx = _ctx(tmp_path, ["legacy/Mailer.php"])
        got = resolve_php_import("App\\Services\\Mailer", "app/X.php", ctx)
        assert got == "legacy/Mailer.php"


class TestFileBasedRequires:
    def test_importer_relative_require(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["legacy/index.php", "legacy/inc/db.php"])
        got = resolve_php_import("inc/db.php", "legacy/index.php", ctx)
        assert got == "legacy/inc/db.php"

    def test_dir_concatenation_leading_slash(self, tmp_path: Path) -> None:
        # require __DIR__ . '/inc/db.php' captures '/inc/db.php' —
        # importer-relative by construction.
        ctx = _ctx(tmp_path, ["legacy/index.php", "legacy/inc/db.php"])
        got = resolve_php_import("/inc/db.php", "legacy/index.php", ctx)
        assert got == "legacy/inc/db.php"

    def test_repo_root_relative_require(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["app/bootstrap.php", "lib/helpers.php"])
        got = resolve_php_import("lib/helpers.php", "app/bootstrap.php", ctx)
        assert got == "lib/helpers.php"

    def test_parent_relative_require(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["app/sub/page.php", "app/config.php"])
        got = resolve_php_import("../config.php", "app/sub/page.php", ctx)
        assert got == "app/config.php"

    def test_unresolved_literal_is_external_not_fuzzy(self, tmp_path: Path) -> None:
        # A literal path matching nothing must NOT stem-match a same-named
        # file elsewhere.
        ctx = _ctx(tmp_path, ["other/place/db.php", "index.php"])
        got = resolve_php_import("inc/db.php", "index.php", ctx)
        assert got == "external:inc/db.php"

    def test_psr4_use_unaffected(self, tmp_path: Path) -> None:
        # Namespace imports don't end in .php and keep the PSR-4 path.
        (tmp_path / "composer.json").write_text(
            '{"autoload": {"psr-4": {"App\\\\": "src/"}}}'
        )
        src = tmp_path / "src" / "Service"
        src.mkdir(parents=True)
        (src / "Mailer.php").write_text("<?php namespace App\\Service; class Mailer {}\n")
        ctx = _ctx(tmp_path, ["src/Service/Mailer.php"])
        got = resolve_php_import("App\\Service\\Mailer", "index.php", ctx)
        assert got == "src/Service/Mailer.php"


class TestRequireExtraction:
    def test_all_require_shapes_extract(self) -> None:
        from datetime import datetime

        from repowise.core.ingestion.models import FileInfo
        from repowise.core.ingestion.parser import ASTParser

        fi = FileInfo(
            path="index.php", abs_path="/tmp/index.php", language="php",
            size_bytes=1, git_hash="", last_modified=datetime.now(),
            is_test=False, is_config=False, is_api_contract=False,
            is_entry_point=False,
        )
        src = (
            b"<?php\n"
            b"require 'lib/helpers.php';\n"           # single-quoted (string node)
            b'require_once "config/app.php";\n'       # double-quoted (encapsed)
            b"include __DIR__ . '/inc/db.php';\n"     # __DIR__ concatenation
            b'require __DIR__ . "/inc/auth.php";\n'
        )
        pf = ASTParser().parse_file(fi, src)
        modules = sorted(i.module_path for i in pf.imports)
        assert modules == [
            "/inc/auth.php", "/inc/db.php", "config/app.php", "lib/helpers.php",
        ]


class TestUseDeclarations:
    @staticmethod
    def _imports(src: bytes) -> list[tuple[str, list[str]]]:
        from datetime import datetime

        from repowise.core.ingestion.models import FileInfo
        from repowise.core.ingestion.parser import ASTParser

        fi = FileInfo(
            path="a.php", abs_path="/tmp/a.php", language="php",
            size_bytes=1, git_hash="", last_modified=datetime.now(),
            is_test=False, is_config=False, is_api_contract=False,
            is_entry_point=False,
        )
        pf = ASTParser().parse_file(fi, src)
        return [(i.module_path, i.imported_names) for i in pf.imports]

    def test_grouped_use_becomes_one_import_per_class(self) -> None:
        got = self._imports(b"<?php\nuse App\\Models\\{User, Http\\Post as P};\n")
        assert got == [
            ("App\\Models\\User", ["User"]),
            ("App\\Models\\Http\\Post", ["P"]),
        ]

    def test_function_and_const_imports_bind_no_class(self) -> None:
        got = self._imports(
            b"<?php\nuse function App\\fmt;\nuse const App\\LIMIT;\n"
            b"use App\\{function b, const C, D};\n"
        )
        assert got == [("App\\D", ["D"])]

    def test_comma_separated_use_keeps_every_clause(self) -> None:
        got = self._imports(b"<?php\nuse \\App\\X, App\\Y as Z;\n")
        assert got == [("App\\X", ["X"]), ("App\\Y", ["Z"])]
