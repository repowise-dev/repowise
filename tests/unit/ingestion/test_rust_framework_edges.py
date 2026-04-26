"""Unit tests for Axum/Actix framework edges (F6)."""

from __future__ import annotations

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
        language="rust",
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
    for src in repo.rglob("*.rs"):
        rel = src.resolve().relative_to(repo.resolve()).as_posix()
        fi = _file_info(rel, str(src.resolve()))
        out[rel] = parser.parse_file(fi, src.read_bytes())
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


class TestAxum:
    def test_route_to_handler(self, tmp_path: Path) -> None:
        (tmp_path / "handlers.rs").write_text(
            "pub async fn list_users() -> &'static str { \"ok\" }\n"
        )
        (tmp_path / "main.rs").write_text(
            "use axum::{Router, routing::get};\n"
            "mod handlers;\n"
            "fn app() -> Router {\n"
            "  Router::new().route(\"/users\", get(list_users))\n"
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["axum"])
        assert graph.has_edge("main.rs", "handlers.rs")


class TestActix:
    def test_web_get_to_handler(self, tmp_path: Path) -> None:
        (tmp_path / "handlers.rs").write_text(
            "pub async fn index() -> &'static str { \"ok\" }\n"
        )
        (tmp_path / "main.rs").write_text(
            "use actix_web::{web, App};\n"
            "fn config(cfg: &mut web::ServiceConfig) {\n"
            "  cfg.route(\"/\", web::get().to(index));\n"
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["actix"])
        assert graph.has_edge("main.rs", "handlers.rs")


class TestRustGate:
    def test_non_router_unaffected(self, tmp_path: Path) -> None:
        (tmp_path / "main.rs").write_text("fn main() {}\n")
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        count = add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert count == 0
