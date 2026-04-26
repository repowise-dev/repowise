"""Unit tests for Express / NestJS framework edges (F4)."""

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
        language="typescript",
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
