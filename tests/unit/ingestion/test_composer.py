"""The shared composer.json reader and the PHP framework facts it feeds."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.ingestion.composer import (
    find_composer_manifests,
    find_vendor_manifests,
    parse_composer,
    read_composer,
)
from repowise.core.ingestion.framework_facts import (
    LARAVEL,
    SYMFONY,
    TYPO3,
    detect_php_framework,
)


def _write(path: Path, data: dict) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "composer.json").write_text(json.dumps(data), encoding="utf-8")


class TestParse:
    def test_reads_every_field_repowise_uses(self) -> None:
        manifest = parse_composer(
            {
                "name": "acme/billing",
                "type": "library",
                "require": {"php": "^8.2", "acme/core": "^1.0"},
                "require-dev": {"phpunit/phpunit": "^11"},
                "autoload": {
                    "psr-4": {"Billing\\": "src/"},
                    "psr-0": {"Legacy_": "lib/"},
                    "classmap": ["database/"],
                },
                "autoload-dev": {"psr-4": {"Billing\\Tests\\": ["tests/", "./spec"]}},
                "repositories": [
                    {"type": "path", "url": "../core"},
                    {"type": "vcs", "url": "https://example.com/x.git"},
                ],
                "extra": {"laravel": {"providers": []}},
            },
            "packages/billing",
        )
        assert manifest is not None
        assert manifest.name == "acme/billing"
        assert manifest.type == "library"
        assert manifest.requires == {
            "php": "^8.2",
            "acme/core": "^1.0",
            "phpunit/phpunit": "^11",
        }
        assert manifest.psr4 == (
            ("Billing\\", ("packages/billing/src",)),
            ("Billing\\Tests\\", ("packages/billing/tests", "packages/billing/spec")),
        )
        assert manifest.psr0 == (("Legacy_", ("packages/billing/lib",)),)
        assert manifest.classmap == ("packages/billing/database",)
        assert manifest.path_repositories == ("../core",)
        assert manifest.extra == {"laravel": {"providers": []}}

    def test_keyed_repositories_form(self) -> None:
        manifest = parse_composer({"repositories": {"core": {"type": "path", "url": "../core"}}})
        assert manifest is not None and manifest.path_repositories == ("../core",)

    def test_root_dir_autoload_is_the_repo_root(self) -> None:
        manifest = parse_composer({"autoload": {"psr-4": {"App\\": ["", "./"]}}})
        assert manifest is not None and manifest.psr4 == (("App\\", ("", "")),)

    def test_non_object_and_bad_json(self, tmp_path: Path) -> None:
        assert parse_composer(["not", "an", "object"]) is None
        (tmp_path / "composer.json").write_text("{nope", encoding="utf-8")
        assert read_composer(tmp_path / "composer.json") is None
        assert read_composer(tmp_path / "missing.json") is None


class TestFind:
    def test_root_first_then_nested_sorted(self, tmp_path: Path) -> None:
        _write(tmp_path, {"name": "acme/app"})
        _write(tmp_path / "packages" / "b", {"name": "acme/b"})
        _write(tmp_path / "packages" / "a", {"name": "acme/a"})
        assert [m.rel_dir for m in find_composer_manifests(tmp_path)] == [
            "",
            "packages/a",
            "packages/b",
        ]

    def test_depth_is_bounded(self, tmp_path: Path) -> None:
        _write(tmp_path / "a" / "b" / "c", {"name": "x/three"})
        _write(tmp_path / "a" / "b" / "c" / "d", {"name": "x/four"})
        assert [m.name for m in find_composer_manifests(tmp_path)] == ["x/three"]

    def test_skips_junk_hidden_and_nested_repos(self, tmp_path: Path) -> None:
        _write(tmp_path / "node_modules" / "evil", {"name": "evil/pkg"})
        _write(tmp_path / ".cache" / "pkg", {"name": "hidden/pkg"})
        nested = tmp_path / "checkout"
        _write(nested, {"name": "other/repo"})
        (nested / ".git").mkdir()
        assert find_composer_manifests(tmp_path) == []

    def test_vendor_is_read_separately(self, tmp_path: Path) -> None:
        _write(tmp_path, {"name": "acme/site"})
        _write(tmp_path / "vendor" / "acme" / "ext", {"name": "acme/ext"})
        assert [m.name for m in find_composer_manifests(tmp_path)] == ["acme/site"]
        assert [m.rel_dir for m in find_vendor_manifests(tmp_path)] == ["vendor/acme/ext"]

    def test_tests_fixtures_and_examples_are_not_packages(self, tmp_path: Path) -> None:
        _write(tmp_path / "tests" / "Fixtures" / "app", {"name": "x/fixture"})
        _write(tmp_path / "examples" / "demo", {"name": "x/demo"})
        _write(tmp_path / "src" / "Testing", {"name": "x/testing"})
        assert [m.name for m in find_composer_manifests(tmp_path)] == ["x/testing"]


class TestFrameworkFacts:
    def test_precedence_typo3_over_symfony(self) -> None:
        manifest = parse_composer(
            {
                "type": "typo3-cms-extension",
                "require": {"typo3/cms-core": "^13", "symfony/framework-bundle": "*"},
            }
        )
        assert manifest is not None and detect_php_framework(manifest) is TYPO3

    def test_each_framework_by_its_package(self) -> None:
        for package, facts in (
            ("laravel/framework", LARAVEL),
            ("symfony/symfony", SYMFONY),
            ("typo3/cms-core", TYPO3),
        ):
            manifest = parse_composer({"require-dev": {package: "*"}})
            assert manifest is not None and detect_php_framework(manifest) is facts
        plain = parse_composer({"require": {"guzzlehttp/guzzle": "^7"}})
        assert plain is not None and detect_php_framework(plain) is None

    def test_entry_files_are_scoped_to_the_app_root(self) -> None:
        paths = {
            "apps/api/routes/api.php",
            "apps/api/app/Providers/AppServiceProvider.php",
            "apps/api/app/Jobs/SendMail.php",
            "routes/api.php",
        }
        assert LARAVEL.entry_files("apps/api", paths) == [
            "apps/api/app/Providers/AppServiceProvider.php",
            "apps/api/routes/api.php",
        ]

    def test_entry_root_is_literal_and_globs_are_case_sensitive(self) -> None:
        paths = {"ext[old]/ext_localconf.php", "exto/ext_localconf.php", "configuration/TCA/x.php"}
        assert TYPO3.entry_files("ext[old]", paths) == ["ext[old]/ext_localconf.php"]
        assert TYPO3.entry_files("", paths) == []
