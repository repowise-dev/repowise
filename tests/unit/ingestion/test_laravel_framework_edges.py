"""Unit tests for Laravel framework edges (F3)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext


def _file_info(rel: str, abs_path: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=abs_path,
        language="php",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _build_parsed(repo: Path) -> dict[str, ParsedFile]:
    parser = ASTParser()
    out: dict[str, ParsedFile] = {}
    for php in repo.rglob("*.php"):
        rel = php.resolve().relative_to(repo.resolve()).as_posix()
        fi = _file_info(rel, str(php.resolve()))
        out[rel] = parser.parse_file(fi, php.read_bytes())
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


def _make_composer(repo: Path, psr4: dict[str, str]) -> None:
    (repo / "composer.json").write_text(
        json.dumps({"autoload": {"psr-4": psr4}})
    )


class TestLaravelRoutes:
    def test_array_syntax_route_to_controller(self, tmp_path: Path) -> None:
        _make_composer(tmp_path, {"App\\": "src/"})
        ctrl_dir = tmp_path / "src" / "Http" / "Controllers"
        ctrl_dir.mkdir(parents=True)
        (ctrl_dir / "UsersController.php").write_text(
            "<?php\nnamespace App\\Http\\Controllers;\nclass UsersController {}\n"
        )
        routes_dir = tmp_path / "routes"
        routes_dir.mkdir()
        (routes_dir / "web.php").write_text(
            "<?php\nuse App\\Http\\Controllers\\UsersController;\n"
            "Route::get('/users', [UsersController::class, 'index']);\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["laravel"])
        assert graph.has_edge(
            "routes/web.php", "src/Http/Controllers/UsersController.php"
        )

    def test_legacy_string_route_to_controller(self, tmp_path: Path) -> None:
        _make_composer(tmp_path, {"App\\": "src/"})
        ctrl_dir = tmp_path / "src"
        ctrl_dir.mkdir(parents=True)
        (ctrl_dir / "UsersController.php").write_text(
            "<?php\nnamespace App;\nclass UsersController {}\n"
        )
        routes_dir = tmp_path / "routes"
        routes_dir.mkdir()
        (routes_dir / "api.php").write_text(
            "<?php\nRoute::get('/users', 'UsersController@index');\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["laravel"])
        assert graph.has_edge("routes/api.php", "src/UsersController.php")


class TestLaravelServiceProvider:
    def test_bind_links_provider_to_classes(self, tmp_path: Path) -> None:
        _make_composer(tmp_path, {"App\\": "src/"})
        src = tmp_path / "src"
        src.mkdir()
        (src / "PaymentInterface.php").write_text(
            "<?php\nnamespace App;\ninterface PaymentInterface {}\n"
        )
        (src / "StripePayment.php").write_text(
            "<?php\nnamespace App;\nclass StripePayment implements PaymentInterface {}\n"
        )
        (src / "AppServiceProvider.php").write_text(
            "<?php\nnamespace App;\nclass AppServiceProvider {\n"
            "  public function register() {\n"
            "    $this->app->bind(PaymentInterface::class, StripePayment::class);\n"
            "  }\n}\n"
        )
        # Need a route file to enable the slice
        (tmp_path / "routes").mkdir()
        (tmp_path / "routes" / "web.php").write_text("<?php\n")
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert graph.has_edge(
            "src/AppServiceProvider.php", "src/PaymentInterface.php"
        )
        assert graph.has_edge(
            "src/AppServiceProvider.php", "src/StripePayment.php"
        )


class TestLaravelEloquent:
    def test_has_many_links_models(self, tmp_path: Path) -> None:
        _make_composer(tmp_path, {"App\\": "src/"})
        models = tmp_path / "src" / "Models"
        models.mkdir(parents=True)
        (models / "User.php").write_text(
            "<?php\nnamespace App\\Models;\n"
            "class User {\n"
            "  public function orders() { return $this->hasMany(Order::class); }\n"
            "}\n"
        )
        (models / "Order.php").write_text(
            "<?php\nnamespace App\\Models;\nclass Order {}\n"
        )
        (tmp_path / "routes").mkdir()
        (tmp_path / "routes" / "web.php").write_text("<?php\n")
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert graph.has_edge("src/Models/User.php", "src/Models/Order.php")


class TestLaravelGate:
    def test_no_route_files_no_edges(self, tmp_path: Path) -> None:
        (tmp_path / "Plain.php").write_text("<?php\nclass Plain {}\n")
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        count = add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert count == 0
