"""TS/JS framework handlers share one import map per file per framework pass.

The Express, Hono, tRPC, Next, Remix and Angular handlers resolve a file's
imports through ``_build_ts_var_to_file``. It is memoised on the pass's
``ResolverContext``, Express builds it only for files whose text names an
identifier to look up, and Express, Hono and tRPC read the bytes ingestion
already holds. None of that may change the graph.

The pinned edge set below was taken from the code before the map was shared,
on a repository covering route registration across files, path aliases,
barrels, re-exports and CRLF sources, and again after each mutation (edit, add,
remove, rename, tsconfig change; each a fresh build, as every framework pass
gets a fresh context). The memo and the source-map read are also switched off
and the full graph compared, which isolates those two from the handler logic.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import networkx as nx
import pytest

from repowise.core.ingestion import GraphBuilder, wire_tsconfig_resolver
from repowise.core.ingestion.framework_edges import add_framework_edges, express, hono, trpc
from repowise.core.ingestion.framework_edges import base as fe_base
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext

_STACK = ["express", "nestjs", "hono", "remix"]

_FILES: dict[str, str] = {
    "tsconfig.json": json.dumps(
        {"compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"], "~/*": ["app/*"]}}}
    ),
    # Express: a router reached through an alias, a barrel and an `as` rename.
    "src/server.ts": (
        "import express from 'express';\n"
        "import { usersRouter, adminRouter as admin } from '@/routes';\n"
        "import { audit } from './middleware/audit';\n"
        "const app = express();\n"
        "app.use('/users', usersRouter);\n"
        "app.use(admin);\n"
        "app.use(audit);\n"
    ),
    "src/routes/index.ts": (
        "export { usersRouter } from './users';\nexport { adminRouter } from './admin';\n"
    ),
    "src/routes/users.ts": (
        "import { Router } from 'express';\n"
        "export const usersRouter = Router();\n"
        "function listUsers(req: any, res: any) { res.send([]); }\n"
        "usersRouter.get('/', listUsers);\n"
    ),
    "src/routes/admin.ts": (
        "import { Router } from 'express';\nexport const adminRouter = Router();\n"
    ),
    "src/middleware/audit.ts": "export function audit(req: any, res: any, next: any) { next(); }\n",
    # CRLF source: the source-map read must decode it the way a disk read does.
    "src/crlf.ts": (
        "import express from 'express';\r\n"
        "import { audit } from './middleware/audit';\r\n"
        "const app = express();\r\n"
        "app.use(audit);\r\n"
    ),
    # Never says "express": only the NestJS @Module scan resolves its imports.
    "src/users.module.ts": (
        "import { Module } from '@nestjs/common';\n"
        "import { UsersController } from '@/users.controller';\n"
        "@Module({ controllers: [UsersController], providers: [UsersService] })\n"
        "export class UsersModule {}\n"
    ),
    "src/users.controller.ts": "export class UsersController {}\n",
    "src/users.service.ts": "export class UsersService {}\n",
    # Hono: relative and aliased handlers, a namespace import, a non-route file.
    "src/api/app.ts": (
        "import { Hono } from 'hono';\n"
        "import { listItems } from '../handlers/items';\n"
        "import { getItem } from '@/handlers/item';\n"
        "import * as misc from '@/handlers/misc';\n"
        "const app = new Hono();\n"
        "app.get('/items', listItems);\n"
        "app.get('/items/:id', getItem);\n"
        "app.route('/misc', misc);\n"
    ),
    "src/api/notes.ts": "// Mentions hono but registers nothing.\nexport const n = 1;\n",
    # The only route names the second, renamed binding of a shared import.
    "src/api/second.ts": (
        "import { Hono } from 'hono';\n"
        "import { first, second as picked } from '../pair/handlers';\n"
        "export const app = new Hono();\n"
        "app.get('/picked', picked);\n"
    ),
    "src/pair/handlers.ts": "export const first = 1;\nexport function second() {}\n",
    "src/handlers/items.ts": "export function listItems() { return []; }\n",
    "src/handlers/item.ts": "export function getItem() { return {}; }\n",
    "src/handlers/misc.ts": "export const ping = () => 'pong';\n",
    # Remix: a convention route importing through `~/` and a relative path.
    "app/routes/dashboard.tsx": (
        "import { db } from '~/utils/db';\n"
        "import { fmt } from '../utils/fmt';\n"
        "export async function loader() { return fmt(db); }\n"
        "export default function Dashboard() { return null; }\n"
    ),
    "app/utils/db.ts": "export const db = {};\n",
    "app/utils/fmt.ts": "export function fmt(x: unknown) { return String(x); }\n",
    # Imports something but is no route and names no framework: nothing resolves it.
    "src/lib/plain.ts": "import { db } from '~/utils/db';\nexport const wrapped = db;\n",
}

# What the handlers emit into a graph holding only file nodes, so no import
# edge pre-empts them. Pinned from the code before the import map was shared.
_EXPECTED: set[tuple[str, str]] = {
    ("app/routes/dashboard.tsx", "app/utils/db.ts"),
    ("app/routes/dashboard.tsx", "app/utils/fmt.ts"),
    ("src/api/app.ts", "src/handlers/item.ts"),
    ("src/api/app.ts", "src/handlers/items.ts"),
    ("src/api/app.ts", "src/handlers/misc.ts"),
    ("src/api/second.ts", "src/pair/handlers.ts"),
    ("src/crlf.ts", "src/middleware/audit.ts"),
    ("src/routes/index.ts", "src/routes/admin.ts"),
    ("src/routes/index.ts", "src/routes/users.ts"),
    ("src/server.ts", "src/middleware/audit.ts"),
    ("src/server.ts", "src/routes/index.ts"),
    ("src/users.module.ts", "src/users.controller.ts"),
    ("src/users.module.ts", "src/users.service.ts"),
}


def _write(repo: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        dest = repo / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(text.encode("utf-8"))


def _parse(repo: Path) -> tuple[dict, dict[str, bytes]]:
    parser = ASTParser()
    parsed: dict = {}
    sources: dict[str, bytes] = {}
    for src in sorted(p for p in repo.rglob("*") if p.suffix in (".ts", ".tsx")):
        rel = src.relative_to(repo).as_posix()
        raw = src.read_bytes()
        fi = FileInfo(
            path=rel,
            abs_path=str(src),
            language="typescript",
            size_bytes=len(raw),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed[rel] = parser.parse_file(fi, raw)
        sources[rel] = raw
    return parsed, sources


def _builder(repo: Path) -> GraphBuilder:
    parsed, sources = _parse(repo)
    builder = GraphBuilder(repo_path=repo)
    for pf in parsed.values():
        builder.add_file(pf)
    wire_tsconfig_resolver(builder, repo)
    builder.set_source_map(sources)
    return builder


def _snapshot(graph: nx.DiGraph) -> tuple[list, list]:
    def dump(d: dict) -> str:
        return json.dumps(d, sort_keys=True, default=str)

    return (
        sorted((n, dump(d)) for n, d in graph.nodes(data=True)),
        sorted((u, v, dump(d)) for u, v, d in graph.edges(data=True)),
    )


def _full_graph(repo: Path) -> tuple[list, list]:
    builder = _builder(repo)
    graph = builder.build()
    builder.add_framework_edges(_STACK)
    return _snapshot(graph)


def _bare_edges(repo: Path) -> set[tuple[str, str]]:
    """Framework edges into a graph of file nodes only, as the handler tests use."""
    builder = _builder(repo)
    builder.build()
    parsed = builder._parsed_files
    ctx = ResolverContext(
        path_set=set(parsed),
        stem_map={},
        graph=nx.DiGraph(),
        repo_path=repo,
        tsconfig_resolver=builder._tsconfig_resolver,
        source_map=builder._source_map,
    )
    graph = nx.DiGraph()
    graph.add_nodes_from(parsed)
    add_framework_edges(graph, parsed, ctx, _STACK)
    return {(u, v) for u, v in graph.edges() if "::" not in u}


@pytest.fixture()
def legacy(monkeypatch) -> Callable[[], None]:
    """Switch to the unshared, eager, disk-reading resolution the memo replaced."""

    def enable() -> None:
        def eager_express(graph, parsed_files, ctx, path_set):
            # Express used to build every TS/JS file's map up front.
            for path, parsed in parsed_files.items():
                if parsed.file_info.language in ("typescript", "javascript") and fe_base.read_text(parsed):
                    fe_base._build_ts_var_to_file(parsed, path, ctx, path_set)
            return real_express(graph, parsed_files, ctx, path_set)

        def disk(_path, parsed, _source_map):
            return fe_base.read_text(parsed)

        real_express = express._add_express_edges
        monkeypatch.setattr(fe_base, "_ts_var_memo", lambda _ctx, _paths: {})
        monkeypatch.setattr(express, "_add_express_edges", eager_express)
        for module in (express, hono, trpc):
            monkeypatch.setattr(module, "read_text_from", disk)

    return enable


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _write(root, _FILES)
    return root


def _edit(repo: Path) -> set[tuple[str, str]]:
    (repo / "src/api/app.ts").write_text(
        _FILES["src/api/app.ts"].replace("app.get('/items/:id', getItem);\n", ""),
        encoding="utf-8",
    )
    return _EXPECTED - {("src/api/app.ts", "src/handlers/item.ts")}


def _add(repo: Path) -> set[tuple[str, str]]:
    _write(
        repo,
        {
            "src/api/orders.ts": (
                "import { Hono } from 'hono';\n"
                "import { listOrders } from '@/handlers/orders';\n"
                "export const orders = new Hono();\n"
                "orders.get('/', listOrders);\n"
            ),
            "src/handlers/orders.ts": "export function listOrders() { return []; }\n",
        },
    )
    return _EXPECTED | {("src/api/orders.ts", "src/handlers/orders.ts")}


def _remove(repo: Path) -> set[tuple[str, str]]:
    (repo / "src/handlers/items.ts").unlink()
    return _EXPECTED - {("src/api/app.ts", "src/handlers/items.ts")}


def _rename(repo: Path) -> set[tuple[str, str]]:
    (repo / "app/utils/db.ts").rename(repo / "app/utils/database.ts")
    (repo / "app/routes/dashboard.tsx").write_text(
        _FILES["app/routes/dashboard.tsx"].replace("~/utils/db", "~/utils/database"),
        encoding="utf-8",
    )
    return (_EXPECTED - {("app/routes/dashboard.tsx", "app/utils/db.ts")}) | {
        ("app/routes/dashboard.tsx", "app/utils/database.ts")
    }


def _tsconfig(repo: Path) -> set[tuple[str, str]]:
    # `@/*` now maps to a directory with no files, though no TS file changed:
    # every aliased import goes external. The Nest controller still binds
    # through its class name.
    (repo / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"paths": {"@/*": ["lib/*"], "~/*": ["app/*"]}}}),
        encoding="utf-8",
    )
    return _EXPECTED - {
        ("src/server.ts", "src/routes/index.ts"),
        ("src/api/app.ts", "src/handlers/item.ts"),
        ("src/api/app.ts", "src/handlers/misc.ts"),
    }


_MUTATIONS = [None, _edit, _add, _remove, _rename, _tsconfig]


@pytest.mark.parametrize("mutate", _MUTATIONS, ids=lambda m: m.__name__ if m else "initial")
def test_edges_match_the_pinned_set(repo: Path, mutate) -> None:
    expected = mutate(repo) if mutate else _EXPECTED
    assert _bare_edges(repo) == expected


@pytest.mark.parametrize("mutate", _MUTATIONS, ids=lambda m: m.__name__ if m else "initial")
def test_graph_equals_unshared_resolution(repo: Path, mutate, legacy) -> None:
    if mutate:
        mutate(repo)
    shared = _full_graph(repo)
    shared_bare = _bare_edges(repo)
    legacy()
    assert _full_graph(repo) == shared
    assert _bare_edges(repo) == shared_bare


@pytest.mark.parametrize(
    "raw",
    [
        b"a\r\nb\rc\n\xff\xfed\r\n\xef\xbb\xbfe\r",
        b"\r\r\n\n\r",
        # CR, CRLF and multi-byte characters across the text reader's chunk edges.
        b"x" * 8191 + b"\r\n" + "é".encode() * 5000 + b"\r" + b"\xe2\x82" + b"\r\n",
        b"",
    ],
    ids=["mixed", "bare-cr", "chunk-edges", "empty"],
)
def test_source_map_text_equals_disk_read(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "f.ts"
    path.write_bytes(raw)
    parsed = SimpleNamespace(file_info=SimpleNamespace(abs_path=str(path)))

    disk = fe_base.read_text(parsed)
    assert fe_base.read_text_from("f.ts", parsed, {"f.ts": raw}) == disk
    assert fe_base.read_text_from("f.ts", parsed, None) == disk
    assert fe_base.read_text_from("f.ts", parsed, {}) == disk


def test_each_import_resolves_once_per_pass(repo: Path, monkeypatch) -> None:
    from repowise.core.ingestion import resolvers

    builder = _builder(repo)
    builder.build()
    calls: list[tuple[str, str]] = []
    real = resolvers.resolve_import

    def counting(module_path, importer_path, language, ctx):
        calls.append((importer_path, module_path))
        return real(module_path, importer_path, language, ctx)

    monkeypatch.setattr(resolvers, "resolve_import", counting)
    builder.add_framework_edges(_STACK)

    assert calls, "the TS handlers resolved nothing"
    # `usersRouter` and `adminRouter as admin` share one resolution.
    assert len(calls) == len(set(calls))
    # Express builds no map for a file that names nothing to look up.
    assert not any(importer == "src/lib/plain.ts" for importer, _ in calls)


def _context(builder: GraphBuilder, repo: Path) -> ResolverContext:
    return ResolverContext(
        path_set=set(builder._parsed_files),
        stem_map={},
        graph=nx.DiGraph(),
        repo_path=repo,
        tsconfig_resolver=builder._tsconfig_resolver,
    )


def test_memo_follows_the_file_set_and_the_parsed_file(repo: Path) -> None:
    builder = _builder(repo)
    ctx = _context(builder, repo)
    server = builder._parsed_files["src/server.ts"]

    first = fe_base._build_ts_var_to_file(server, "src/server.ts", ctx, ctx.path_set)
    assert first["usersRouter"] == "src/routes/index.ts"
    assert first["admin"] == "src/routes/index.ts"
    assert fe_base._build_ts_var_to_file(server, "src/server.ts", ctx, ctx.path_set) is first

    # A different parse of the same path, same file set, must not answer from the memo.
    reparsed = copy.copy(server)
    reparsed.imports = [imp for imp in server.imports if imp.module_path != "@/routes"]
    assert "usersRouter" not in fe_base._build_ts_var_to_file(
        reparsed, "src/server.ts", ctx, ctx.path_set
    )

    # Nor may a different file set (the barrel is gone).
    smaller = ctx.path_set - {"src/routes/index.ts"}
    assert "usersRouter" not in fe_base._build_ts_var_to_file(server, "src/server.ts", ctx, smaller)


def test_reused_context_gives_identical_edges(repo: Path) -> None:
    builder = _builder(repo)
    parsed = builder._parsed_files
    ctx = _context(builder, repo)
    seen = []
    for _ in range(2):
        graph = nx.DiGraph()
        graph.add_nodes_from(parsed)
        add_framework_edges(graph, parsed, ctx, _STACK)
        seen.append({(u, v) for u, v in graph.edges() if "::" not in u})
    assert seen[0] == seen[1] == _EXPECTED
