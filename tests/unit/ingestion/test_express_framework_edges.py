"""Unit tests for Express / NestJS framework edges (F4)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion import GraphBuilder
from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext


def _file_info(rel: str, abs_path: str, language: str = "typescript") -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=abs_path,
        language=language,
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
    for src in repo.rglob("*.ts"):
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


class TestExpress:
    def test_app_use_router_links_to_router_file(self, tmp_path: Path) -> None:
        (tmp_path / "users.router.ts").write_text(
            "import { Router } from 'express';\n"
            "export const usersRouter = Router();\n"
        )
        (tmp_path / "app.ts").write_text(
            "import express from 'express';\n"
            "import { usersRouter } from './users.router';\n"
            "const app = express();\n"
            "app.use('/users', usersRouter);\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["express"])
        assert graph.has_edge("app.ts", "users.router.ts")


class TestNestJS:
    def test_module_controllers_array(self, tmp_path: Path) -> None:
        (tmp_path / "users.controller.ts").write_text(
            "import { Controller } from '@nestjs/common';\n"
            "@Controller()\nexport class UsersController {}\n"
        )
        (tmp_path / "users.service.ts").write_text(
            "import { Injectable } from '@nestjs/common';\n"
            "@Injectable()\nexport class UsersService {}\n"
        )
        (tmp_path / "users.module.ts").write_text(
            "import { Module } from '@nestjs/common';\n"
            "import { UsersController } from './users.controller';\n"
            "import { UsersService } from './users.service';\n"
            "@Module({ controllers: [UsersController], providers: [UsersService] })\n"
            "export class UsersModule {}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["nestjs"])
        assert graph.has_edge("users.module.ts", "users.controller.ts")
        assert graph.has_edge("users.module.ts", "users.service.ts")


class TestExpressGate:
    def test_no_express_no_edges(self, tmp_path: Path) -> None:
        (tmp_path / "plain.ts").write_text("export const x = 1;\n")
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        count = add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert count == 0


def _build_graph_with_framework_edges(repo: Path) -> nx.DiGraph:
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for src in sorted([*repo.rglob("*.ts"), *repo.rglob("*.js")]):
        rel = src.resolve().relative_to(repo.resolve()).as_posix()
        language = "javascript" if src.suffix == ".js" else "typescript"
        fi = _file_info(rel, str(src.resolve()), language)
        builder.add_file(parser.parse_file(fi, src.read_bytes()))
    graph = builder.build()
    builder.add_framework_edges(["express"])
    return graph


class TestExpressLocalMiddlewareReads:
    def test_route_post_middleware_get_reads_edges(self, tmp_path: Path) -> None:
        (tmp_path / "routes.ts").write_text(
            "import { Router } from 'express';\n"
            "export function logRequest(req, res, next) { next(); }\n"
            "export function validateRequest(req, res, next) { next(); }\n"
            "export function handler(req, res) { res.send('ok'); }\n"
            "const router = Router();\n"
            "router.post('/example', logRequest, validateRequest, handler);\n"
        )
        graph = _build_graph_with_framework_edges(tmp_path)
        module_sym = "routes.ts::__module__"
        for name in ("logRequest", "validateRequest", "handler"):
            sym_id = f"routes.ts::{name}"
            assert graph.has_edge(module_sym, sym_id), f"missing reads edge to {name}"
            assert graph[module_sym][sym_id]["edge_type"] == "reads"

    def test_app_use_middleware_gets_reads_edge(self, tmp_path: Path) -> None:
        (tmp_path / "mw.ts").write_text(
            "import express from 'express';\n"
            "export function authGuard(req, res, next) { next(); }\n"
            "const app = express();\n"
            "app.use(authGuard);\n"
        )
        graph = _build_graph_with_framework_edges(tmp_path)
        assert graph.has_edge("mw.ts::__module__", "mw.ts::authGuard")
        assert graph["mw.ts::__module__"]["mw.ts::authGuard"]["edge_type"] == "reads"

    def test_defines_edge_preserved(self, tmp_path: Path) -> None:
        (tmp_path / "routes.ts").write_text(
            "import { Router } from 'express';\n"
            "export function handler(req, res) { res.send('ok'); }\n"
            "const router = Router();\n"
            "router.get('/x', handler);\n"
        )
        graph = _build_graph_with_framework_edges(tmp_path)
        assert graph["routes.ts"]["routes.ts::handler"]["edge_type"] == "defines"

    def test_nested_middleware_call_before_handler(self, tmp_path: Path) -> None:
        (tmp_path / "routes.ts").write_text(
            "import { Router } from 'express';\n"
            "export function requireRole(role) { return (req, res, next) => next(); }\n"
            "export function handler(req, res) { res.send('ok'); }\n"
            "const router = Router();\n"
            "router.get('/x', requireRole('admin'), handler);\n"
        )
        graph = _build_graph_with_framework_edges(tmp_path)
        handler = "routes.ts::handler"
        assert graph.has_edge("routes.ts::__module__", handler)
        assert graph["routes.ts::__module__"][handler]["edge_type"] == "reads"
        assert graph.has_edge("routes.ts::__module__", "routes.ts::requireRole")

    def test_commonjs_multiline_route_reads_edges(self, tmp_path: Path) -> None:
        (tmp_path / "routes.js").write_text(
            'const express = require("express");\n'
            "const router = express.Router();\n"
            "\n"
            "function logRequest(req, res, next) { next(); }\n"
            "function validateRequest(req, res, next) { next(); }\n"
            "\n"
            'router.post("/x",\n'
            "  logRequest,\n"
            "  validateRequest,\n"
            "  async (req, res) => { res.send('ok'); }\n"
            ");\n"
            "\n"
            "module.exports = router;\n"
        )
        graph = _build_graph_with_framework_edges(tmp_path)
        module_sym = "routes.js::__module__"
        for name in ("logRequest", "validateRequest"):
            sym_id = f"routes.js::{name}"
            assert graph.has_edge(module_sym, sym_id), f"missing reads edge to {name}"
            assert graph[module_sym][sym_id]["edge_type"] == "reads"

    def test_non_route_receiver_in_express_file_no_reads_edge(self, tmp_path: Path) -> None:
        (tmp_path / "svc.ts").write_text(
            "import express from 'express';\n"
            "export function cleanup() {}\n"
            "const cache = new Map();\n"
            "cache.get(cleanup);\n"
            "const app = express();\n"
        )
        graph = _build_graph_with_framework_edges(tmp_path)
        assert not graph.has_edge("svc.ts::__module__", "svc.ts::cleanup")

    def test_non_express_file_no_reads_edge(self, tmp_path: Path) -> None:
        (tmp_path / "app.ts").write_text(
            "import express from 'express';\nconst app = express();\n"
        )
        (tmp_path / "cache.ts").write_text(
            "export function helper() { return 1; }\n"
            "const store = new Map();\n"
            "store.get(helper);\n"
        )
        graph = _build_graph_with_framework_edges(tmp_path)
        assert not graph.has_edge("cache.ts::__module__", "cache.ts::helper")


class TestExpressDeadCodeRegression:
    def test_local_route_middleware_not_flagged_unused(self, tmp_path: Path) -> None:
        (tmp_path / "routes.ts").write_text(
            "import { Router } from 'express';\n"
            "export function logRequest(req, res, next) { next(); }\n"
            "export function handler(req, res) { res.send('ok'); }\n"
            "const router = Router();\n"
            "router.get('/x', logRequest, handler);\n"
            "export default router;\n"
        )
        (tmp_path / "app.ts").write_text(
            "import express from 'express';\n"
            "import router from './routes';\n"
            "const app = express();\n"
            "app.use('/', router);\n"
        )
        graph = _build_graph_with_framework_edges(tmp_path)
        report = DeadCodeAnalyzer(graph, git_meta_map={}).analyze({"min_confidence": 0.0})
        unused = {
            f.symbol_name
            for f in report.findings
            if f.kind == DeadCodeKind.UNUSED_EXPORT
        }
        assert "logRequest" not in unused
        assert "handler" not in unused
