"""Unit tests for the PHP / composer.json-aware import resolver."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.php import resolve_php_import
from repowise.core.ingestion.resolvers.php_composer import (
    read_composer_psr4,
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


class TestComposerParsing:
    def test_psr4_single_string_value(self, tmp_path: Path) -> None:
        _write_composer(tmp_path, {"App\\": "src/"})
        psr4 = read_composer_psr4(tmp_path)
        assert psr4 == {"App\\": ["src"]}

    def test_psr4_list_value(self, tmp_path: Path) -> None:
        _write_composer(tmp_path, {"App\\": ["src/", "lib/"]})
        psr4 = read_composer_psr4(tmp_path)
        assert psr4 == {"App\\": ["src", "lib"]}

    def test_psr4_merges_autoload_dev(self, tmp_path: Path) -> None:
        _write_composer(tmp_path, {"App\\": "src/"}, autoload_dev={"Tests\\": "tests/"})
        psr4 = read_composer_psr4(tmp_path)
        assert psr4 == {"App\\": ["src"], "Tests\\": ["tests"]}

    def test_missing_composer(self, tmp_path: Path) -> None:
        assert read_composer_psr4(tmp_path) == {}


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
