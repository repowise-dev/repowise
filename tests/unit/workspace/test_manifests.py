"""The ecosystem table: package-name edges for npm and composer, and service markers."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.workspace.extractors.service_boundary import detect_service_boundaries
from repowise.core.workspace.manifests import (
    ECOSYSTEMS,
    SERVICE_MARKERS,
    detect_package_dependencies,
)


def _json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _deps(repos: dict[str, Path]) -> list[tuple[str, str, str, str, str]]:
    return [
        (d.source_repo, d.target_repo, d.source_manifest, d.kind, d.target_package)
        for d in detect_package_dependencies(repos)
    ]


class TestNpmNames:
    def test_dependency_on_a_sibling_workspace_member(self, tmp_path: Path) -> None:
        lib, web = tmp_path / "lib", tmp_path / "web"
        _json(lib / "package.json", {"name": "lib-root", "workspaces": ["packages/*"]})
        _json(lib / "packages" / "ui" / "package.json", {"name": "@acme/ui"})
        _json(web / "package.json", {"dependencies": {"@acme/ui": "^1.0.0", "react": "^18"}})

        deps = detect_package_dependencies({"lib": lib, "web": web})

        assert [(d.source_repo, d.target_repo, d.kind, d.target_package) for d in deps] == [
            ("web", "lib", "npm_package", "@acme/ui")
        ]
        assert deps[0].target_manifest == "packages/ui/package.json"
        assert deps[0].requested_version == "^1.0.0"

    def test_non_member_copies_publish_nothing(self, tmp_path: Path) -> None:
        # A vendored compiled copy and a fixture are not packages the repo ships.
        lib, web = tmp_path / "lib", tmp_path / "web"
        _json(lib / "package.json", {"name": "lib-root"})
        _json(lib / "src" / "compiled" / "semver" / "package.json", {"name": "semver"})
        _json(lib / "test" / "fixtures" / "app" / "package.json", {"name": "fixture-app"})
        _json(web / "package.json", {"dependencies": {"semver": "^7", "fixture-app": "1"}})
        assert _deps({"lib": lib, "web": web}) == []

    def test_a_name_two_repos_publish_links_neither(self, tmp_path: Path) -> None:
        a, b, web = tmp_path / "a", tmp_path / "b", tmp_path / "web"
        _json(a / "package.json", {"name": "shared"})
        _json(b / "package.json", {"name": "shared"})
        _json(web / "package.json", {"dependencies": {"shared": "^1"}})
        assert _deps({"a": a, "b": b, "web": web}) == []

    def test_pnpm_members_publish_their_names(self, tmp_path: Path) -> None:
        lib, web = tmp_path / "lib", tmp_path / "web"
        (lib / "pnpm-workspace.yaml").parent.mkdir(parents=True)
        (lib / "pnpm-workspace.yaml").write_text("packages:\n  - libs/*\n", encoding="utf-8")
        _json(lib / "package.json", {"name": "lib-root"})
        _json(lib / "libs" / "money" / "package.json", {"name": "@acme/money"})
        _json(web / "package.json", {"devDependencies": {"@acme/money": "^1"}})
        assert _deps({"lib": lib, "web": web}) == [
            ("web", "lib", "package.json", "npm_package", "@acme/money")
        ]

    def test_a_member_outside_the_repo_is_the_siblings_not_ours(self, tmp_path: Path) -> None:
        shared, app, web = tmp_path / "shared", tmp_path / "app", tmp_path / "web"
        _json(shared / "package.json", {"name": "shared", "dependencies": {"left-pad": "1"}})
        _json(app / "package.json", {"name": "app", "workspaces": ["../shared"]})
        _json(web / "package.json", {"dependencies": {"shared": "^1"}})
        deps = _deps({"shared": shared, "app": app, "web": web})
        assert ("web", "shared", "package.json", "npm_package", "shared") in deps
        assert all(source != "app" or kind == "npm_workspace" for source, _t, _m, kind, _p in deps)

    def test_workspace_protocol_is_never_a_sibling(self, tmp_path: Path) -> None:
        lib, web = tmp_path / "lib", tmp_path / "web"
        _json(lib / "package.json", {"name": "@acme/ui"})
        _json(web / "package.json", {"dependencies": {"@acme/ui": "workspace:*"}})
        assert _deps({"lib": lib, "web": web}) == []

    def test_file_path_from_a_workspace_member_resolves_from_its_own_dir(
        self, tmp_path: Path
    ) -> None:
        lib, web = tmp_path / "lib", tmp_path / "web"
        _json(lib / "package.json", {"name": "lib"})
        _json(web / "package.json", {"workspaces": ["apps/*"]})
        _json(
            web / "apps" / "site" / "package.json",
            {"name": "site", "dependencies": {"x": "file:../../../lib"}},
        )
        assert _deps({"lib": lib, "web": web}) == [
            ("web", "lib", "apps/site/package.json", "npm_local_path", "")
        ]


class TestComposer:
    def test_require_of_a_sibling_package_name(self, tmp_path: Path) -> None:
        core, api = tmp_path / "core", tmp_path / "api"
        _json(core / "composer.json", {"name": "acme/core"})
        _json(api / "composer.json", {"require": {"php": "^8.2", "acme/core": "^2.0"}})
        assert _deps({"core": core, "api": api}) == [
            ("api", "core", "composer.json", "composer_package", "acme/core")
        ]

    def test_path_repository_into_a_sibling(self, tmp_path: Path) -> None:
        core, api = tmp_path / "core", tmp_path / "api"
        _json(core / "packages" / "money" / "composer.json", {"name": "acme/money"})
        _json(
            api / "composer.json",
            {"repositories": [{"type": "path", "url": "../core/packages/money"}]},
        )
        assert _deps({"core": core, "api": api}) == [
            ("api", "core", "composer.json", "composer_path", "")
        ]

    def test_a_name_two_repos_publish_links_neither(self, tmp_path: Path) -> None:
        a, b, api = tmp_path / "a", tmp_path / "b", tmp_path / "api"
        _json(a / "composer.json", {"name": "acme/core"})
        _json(b / "composer.json", {"name": "acme/core"})
        _json(api / "composer.json", {"require-dev": {"acme/core": "*"}})
        assert _deps({"a": a, "b": b, "api": api}) == []

    def test_path_repository_inside_the_own_repo_is_not_an_edge(self, tmp_path: Path) -> None:
        core, api = tmp_path / "core", tmp_path / "api"
        _json(core / "composer.json", {"name": "acme/core"})
        _json(api / "packages" / "money" / "composer.json", {"name": "acme/money"})
        _json(api / "composer.json", {"repositories": [{"type": "path", "url": "packages/money"}]})
        assert _deps({"core": core, "api": api}) == []

    def test_nested_package_names_count_but_fixtures_do_not(self, tmp_path: Path) -> None:
        core, api = tmp_path / "core", tmp_path / "api"
        _json(core / "src" / "Money" / "composer.json", {"name": "acme/money"})
        _json(core / "tests" / "fixtures" / "composer.json", {"name": "acme/fixture"})
        _json(api / "composer.json", {"require": {"acme/money": "*", "acme/fixture": "*"}})
        assert _deps({"core": core, "api": api}) == [
            ("api", "core", "composer.json", "composer_package", "acme/money")
        ]


class TestServiceMarkers:
    def test_markers_come_from_the_table(self) -> None:
        assert {m for eco in ECOSYSTEMS for m in eco.markers} == SERVICE_MARKERS
        assert "composer.json" in SERVICE_MARKERS

    def test_a_composer_package_is_a_service_boundary(self, tmp_path: Path) -> None:
        pkg = tmp_path / "services" / "billing"
        _json(pkg / "composer.json", {"name": "acme/billing"})
        (pkg / "Invoice.php").write_text("<?php\n", encoding="utf-8")
        assert [b.service_path for b in detect_service_boundaries(tmp_path)] == [
            "services/billing"
        ]
