"""Unit tests for the Ruby Rails / Zeitwerk-aware import resolver."""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.ruby import resolve_ruby_import
from repowise.core.ingestion.resolvers.ruby_rails import (
    build_rails_index,
    camel_to_snake,
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


def _make_rails_repo(repo: Path) -> None:
    (repo / "config").mkdir()
    (repo / "config" / "application.rb").write_text("module App; class Application; end; end\n")


class TestRailsDetection:
    def test_returns_none_when_not_rails(self, tmp_path: Path) -> None:
        assert build_rails_index(tmp_path) is None

    def test_detects_via_application_rb(self, tmp_path: Path) -> None:
        _make_rails_repo(tmp_path)
        index = build_rails_index(tmp_path)
        assert index is not None


class TestRailsConstantLookup:
    def test_simple_constant(self, tmp_path: Path) -> None:
        _make_rails_repo(tmp_path)
        ctrl = tmp_path / "app" / "controllers"
        ctrl.mkdir(parents=True)
        (ctrl / "users_controller.rb").write_text("class UsersController; end\n")
        index = build_rails_index(tmp_path)
        assert index is not None
        assert index.lookup("UsersController") == "app/controllers/users_controller.rb"

    def test_namespaced_constant(self, tmp_path: Path) -> None:
        _make_rails_repo(tmp_path)
        admin = tmp_path / "app" / "controllers" / "admin"
        admin.mkdir(parents=True)
        (admin / "reports_controller.rb").write_text(
            "module Admin; class ReportsController; end; end\n"
        )
        index = build_rails_index(tmp_path)
        assert index is not None
        result = index.lookup("Admin::ReportsController")
        assert result == "app/controllers/admin/reports_controller.rb"

    def test_camel_to_snake(self) -> None:
        assert camel_to_snake("UserController") == "user_controller"
        assert camel_to_snake("Foo") == "foo"


class TestRubyResolverIntegration:
    def test_require_relative_unaffected(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["lib/foo.rb"])
        result = resolve_ruby_import("./foo", "lib/main.rb", ctx)
        assert result == "lib/foo.rb"

    def test_rails_path_style_require(self, tmp_path: Path) -> None:
        _make_rails_repo(tmp_path)
        svc = tmp_path / "app" / "services"
        svc.mkdir(parents=True)
        (svc / "user_service.rb").write_text("class UserService; end\n")
        ctx = _ctx(tmp_path, ["app/services/user_service.rb", "config/application.rb"])
        result = resolve_ruby_import("app/services/user_service", "main.rb", ctx)
        assert result == "app/services/user_service.rb"

    def test_non_rails_unaffected(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, ["lib/foo.rb"])
        result = resolve_ruby_import("foo", "main.rb", ctx)
        assert result == "lib/foo.rb"
