"""Unit tests for Next.js / Hono / Remix / tRPC framework edges (Phase 3)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

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
    for src in list(repo.rglob("*.ts")) + list(repo.rglob("*.tsx")):
        rel = src.resolve().relative_to(repo.resolve()).as_posix()
        lang = "typescript"
        out[rel] = parser.parse_file(_file_info(rel, str(src.resolve()), lang), src.read_bytes())
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


class TestNextAppRouter:
    def test_app_page_links_to_imported_helper(self, tmp_path: Path) -> None:
        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "db.ts").write_text("export const db = {};\n")
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "page.tsx").write_text(
            "import { db } from '../lib/db';\nexport default function() { return db; }\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["next"])
        assert graph.has_edge("app/page.tsx", "lib/db.ts")


class TestHonoRouter:
    def test_app_get_links_handler(self, tmp_path: Path) -> None:
        (tmp_path / "handlers.ts").write_text(
            "export function userHandler() { return 'ok'; }\n"
        )
        (tmp_path / "app.ts").write_text(
            "import { Hono } from 'hono';\n"
            "import { userHandler } from './handlers';\n"
            "const app = new Hono();\n"
            "app.get('/users', userHandler);\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["hono"])
        assert graph.has_edge("app.ts", "handlers.ts")


class TestRemixConvention:
    def test_route_file_links_to_helper(self, tmp_path: Path) -> None:
        (tmp_path / "utils").mkdir()
        (tmp_path / "utils" / "auth.ts").write_text("export const auth = {};\n")
        (tmp_path / "app" / "routes").mkdir(parents=True)
        (tmp_path / "app" / "routes" / "_index.tsx").write_text(
            "import { auth } from '../../utils/auth';\nexport function loader() { return auth; }\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["remix"])
        assert graph.has_edge("app/routes/_index.tsx", "utils/auth.ts")


class TestTrpc:
    def test_procedure_query_links_handler(self, tmp_path: Path) -> None:
        (tmp_path / "users.ts").write_text(
            "export function getUserHandler() { return null; }\n"
        )
        (tmp_path / "router.ts").write_text(
            "import { publicProcedure, router } from '@trpc/server';\n"
            "import { getUserHandler } from './users';\n"
            "export const appRouter = router({\n"
            "  getUser: publicProcedure.query(getUserHandler),\n"
            "});\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["trpc"])
        assert graph.has_edge("router.ts", "users.ts")
