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
