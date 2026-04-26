"""Unit tests for Rails framework edges (F2)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext


def _file_info(rel: str, abs_path: str, language: str = "ruby") -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=abs_path,
        language=language,
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=rel.startswith("config/"),
        is_api_contract=False,
        is_entry_point=False,
    )


def _build_parsed(repo: Path) -> dict[str, ParsedFile]:
    parser = ASTParser()
    out: dict[str, ParsedFile] = {}
    for rb in repo.rglob("*.rb"):
        rel = rb.resolve().relative_to(repo.resolve()).as_posix()
        fi = _file_info(rel, str(rb.resolve()))
        out[rel] = parser.parse_file(fi, rb.read_bytes())
    return out


def _ctx(repo: Path, parsed: dict[str, ParsedFile]) -> ResolverContext:
    path_set = set(parsed.keys())
    stem_map: dict[str, list[str]] = {}
    for p in path_set:
        stem = Path(p).stem.lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=path_set, stem_map=stem_map, graph=nx.DiGraph(), repo_path=repo
    )


def _make_rails_app(repo: Path) -> None:
    (repo / "config").mkdir(parents=True)
    (repo / "config" / "application.rb").write_text("module App; class Application; end; end\n")


class TestRailsRoutes:
    def test_resources_routes_to_controller(self, tmp_path: Path) -> None:
        _make_rails_app(tmp_path)
        (tmp_path / "config" / "routes.rb").write_text(
            "Rails.application.routes.draw do\n  resources :users\nend\n"
        )
        ctrl_dir = tmp_path / "app" / "controllers"
        ctrl_dir.mkdir(parents=True)
        (ctrl_dir / "users_controller.rb").write_text("class UsersController; end\n")

        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["rails"])
        assert graph.has_edge("config/routes.rb", "app/controllers/users_controller.rb")

    def test_get_to_routes_to_controller(self, tmp_path: Path) -> None:
        _make_rails_app(tmp_path)
        (tmp_path / "config" / "routes.rb").write_text(
            'Rails.application.routes.draw do\n  get "/foo", to: "users#index"\nend\n'
        )
        ctrl_dir = tmp_path / "app" / "controllers"
        ctrl_dir.mkdir(parents=True)
        (ctrl_dir / "users_controller.rb").write_text("class UsersController; end\n")

        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["rails"])
        assert graph.has_edge("config/routes.rb", "app/controllers/users_controller.rb")

    def test_namespace_block_resolves_nested_controller(self, tmp_path: Path) -> None:
        _make_rails_app(tmp_path)
        (tmp_path / "config" / "routes.rb").write_text(
            "Rails.application.routes.draw do\n"
            "  namespace :admin do\n"
            "    resources :reports\n"
            "  end\n"
            "end\n"
        )
        admin_dir = tmp_path / "app" / "controllers" / "admin"
        admin_dir.mkdir(parents=True)
        (admin_dir / "reports_controller.rb").write_text(
            "module Admin; class ReportsController; end; end\n"
        )

        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["rails"])
        assert graph.has_edge(
            "config/routes.rb", "app/controllers/admin/reports_controller.rb"
        )


class TestRailsActiveRecord:
    def test_belongs_to_links_models(self, tmp_path: Path) -> None:
        _make_rails_app(tmp_path)
        models_dir = tmp_path / "app" / "models"
        models_dir.mkdir(parents=True)
        (models_dir / "order.rb").write_text(
            "class Order < ApplicationRecord\n  belongs_to :user\nend\n"
        )
        (models_dir / "user.rb").write_text("class User < ApplicationRecord; end\n")

        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["rails"])
        assert graph.has_edge("app/models/order.rb", "app/models/user.rb")

    def test_has_many_singularizes(self, tmp_path: Path) -> None:
        _make_rails_app(tmp_path)
        models_dir = tmp_path / "app" / "models"
        models_dir.mkdir(parents=True)
        (models_dir / "user.rb").write_text(
            "class User < ApplicationRecord\n  has_many :orders\nend\n"
        )
        (models_dir / "order.rb").write_text("class Order < ApplicationRecord; end\n")

        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["rails"])
        assert graph.has_edge("app/models/user.rb", "app/models/order.rb")


class TestRailsGate:
    def test_non_rails_repo_unaffected(self, tmp_path: Path) -> None:
        # Plain Ruby with similar-looking code but no config/application.rb
        (tmp_path / "models.rb").write_text(
            "class Order; belongs_to :user; end\nclass User; end\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        count = add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert count == 0
