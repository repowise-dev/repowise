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

    def test_a_test_beside_a_route_is_not_a_convention_route(self, tmp_path: Path) -> None:
        # A test file publishes no endpoint, so it is not loaded by convention
        # and gets no edge of its own.
        (tmp_path / "utils").mkdir()
        (tmp_path / "utils" / "auth.ts").write_text("export const auth = {};\n")
        (tmp_path / "app" / "routes").mkdir(parents=True)
        (tmp_path / "app" / "routes" / "foo.test.tsx").write_text(
            "import { auth } from '../../utils/auth';\nexport function loader() { return auth; }\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["remix"])
        assert not graph.has_edge("app/routes/foo.test.tsx", "utils/auth.ts")

    def test_astro_page_links_to_helper(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "lib").mkdir(parents=True)
        (tmp_path / "src" / "lib" / "db.ts").write_text("export const db = {};\n")
        (tmp_path / "src" / "pages").mkdir()
        page = tmp_path / "src" / "pages" / "index.astro"
        page.write_text("---\nimport { db } from '../lib/db';\n---\n<p>{db}</p>\n")
        parsed = _build_parsed(tmp_path)
        rel = "src/pages/index.astro"
        parsed[rel] = ASTParser().parse_file(
            _file_info(rel, str(page.resolve()), "astro"), page.read_bytes()
        )
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert graph.has_edge(rel, "src/lib/db.ts")


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


class TestReactRouterFrameworkMode:
    @staticmethod
    def _write_app(repo: Path, app_dir: str = "app") -> None:
        (repo / app_dir / "routes").mkdir(parents=True)
        (repo / app_dir / "root.tsx").write_text(
            "export function Layout() { return null; }\n"
            "export default function App() { return null; }\n"
        )
        (repo / app_dir / "routes.ts").write_text(
            'import { type RouteConfig, index } from "@react-router/dev/routes";\n'
            'export default [index("routes/home.tsx")] satisfies RouteConfig;\n'
        )
        (repo / app_dir / "routes" / "home.tsx").write_text(
            "export function meta() { return []; }\n"
            "export default function Home() { return null; }\n"
        )

    @staticmethod
    def _edges(repo: Path, tech_stack: list[str] | None = None) -> nx.DiGraph:
        parsed = _build_parsed(repo)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        add_framework_edges(graph, parsed, _ctx(repo, parsed), tech_stack=tech_stack or [])
        return graph

    def test_config_file_anchors_root_routes_and_route_modules(self, tmp_path: Path) -> None:
        self._write_app(tmp_path)
        (tmp_path / "react-router.config.ts").write_text(
            "export default { ssr: true } satisfies Config;\n"
        )
        graph = self._edges(tmp_path)
        assert graph.has_edge("framework:react-router", "app/root.tsx")
        assert graph.has_edge("framework:react-router", "app/routes.ts")
        assert graph.has_edge("app/routes.ts", "app/routes/home.tsx")
        # No names on the edge: the runtime calls whichever exports the module has.
        assert graph["app/routes.ts"]["app/routes/home.tsx"]["imported_names"] == []

    def test_dev_dependency_links_every_route_helper(self, tmp_path: Path) -> None:
        self._write_app(tmp_path)
        (tmp_path / "package.json").write_text(
            '{"name": "web", "devDependencies": {"@react-router/dev": "^7.1.0"}}'
        )
        for name in ("about", "shell", "settings", "old"):
            (tmp_path / "app" / "routes" / f"{name}.tsx").write_text(
                "export default function Page() { return null; }\n"
            )
        (tmp_path / "app" / "routes.ts").write_text(
            "import { index, layout, prefix, route } from '@react-router/dev/routes';\n"
            "export default [\n"
            "  index('routes/home.tsx'),\n"
            "  route('about', './routes/about.tsx'),\n"
            "  layout('routes/shell.tsx', [\n"
            "    ...prefix('account', [route('settings', 'routes/settings.tsx')]),\n"
            "  ]),\n"
            "  // route('old', 'routes/old.tsx'),\n"
            "];\n"
        )
        graph = self._edges(tmp_path)
        assert graph.has_edge("framework:react-router", "app/root.tsx")
        for name in ("home", "about", "shell", "settings"):
            assert graph.has_edge("app/routes.ts", f"app/routes/{name}.tsx")
        assert not graph.has_edge("app/routes.ts", "app/routes/old.tsx")

    def test_app_directory_from_config(self, tmp_path: Path) -> None:
        self._write_app(tmp_path, app_dir="src")
        (tmp_path / "react-router.config.ts").write_text(
            'export default { appDirectory: "src", ssr: false } satisfies Config;\n'
        )
        graph = self._edges(tmp_path)
        assert graph.has_edge("framework:react-router", "src/root.tsx")
        assert graph.has_edge("framework:react-router", "src/routes.ts")
        assert graph.has_edge("src/routes.ts", "src/routes/home.tsx")

    def test_splat_path_does_not_open_a_block_comment(self, tmp_path: Path) -> None:
        self._write_app(tmp_path)
        (tmp_path / "react-router.config.ts").write_text("export default {};\n")
        for name in ("docs", "about"):
            (tmp_path / "app" / "routes" / f"{name}.tsx").write_text(
                "export default function Page() { return null; }\n"
            )
        (tmp_path / "app" / "routes.ts").write_text(
            "import { index, route } from '@react-router/dev/routes';\n"
            "export default [\n"
            '  index("routes/home.tsx"),\n'
            '  route("docs/*", "routes/docs.tsx"),\n'
            '  route("about", "routes/about.tsx"),\n'
            "  /* more routes later */\n"
            "];\n"
        )
        graph = self._edges(tmp_path)
        for name in ("home", "docs", "about"):
            assert graph.has_edge("app/routes.ts", f"app/routes/{name}.tsx")

    def test_trailing_line_comment_names_no_route(self, tmp_path: Path) -> None:
        self._write_app(tmp_path)
        (tmp_path / "react-router.config.ts").write_text("export default {};\n")
        (tmp_path / "app" / "routes" / "old.tsx").write_text(
            "export default function Old() { return null; }\n"
        )
        (tmp_path / "app" / "routes.ts").write_text(
            "import { index } from '@react-router/dev/routes';\n"
            'export default [index("routes/home.tsx"), // route("old", "routes/old.tsx")\n'
            "];\n"
        )
        graph = self._edges(tmp_path)
        assert graph.has_edge("app/routes.ts", "app/routes/home.tsx")
        assert not graph.has_edge("app/routes.ts", "app/routes/old.tsx")

    def test_app_directory_at_the_repo_root(self, tmp_path: Path) -> None:
        self._write_app(tmp_path, app_dir="")
        (tmp_path / "react-router.config.ts").write_text(
            'export default { appDirectory: "." } satisfies Config;\n'
        )
        graph = self._edges(tmp_path)
        assert graph.has_edge("framework:react-router", "root.tsx")
        assert graph.has_edge("framework:react-router", "routes.ts")
        assert graph.has_edge("routes.ts", "routes/home.tsx")

    def test_same_files_without_the_framework_get_no_edges(self, tmp_path: Path) -> None:
        # A Remix app (or any repo) with a root.tsx but no React Router config
        # or dev dependency is left alone.
        self._write_app(tmp_path)
        (tmp_path / "package.json").write_text(
            '{"name": "web", "dependencies": {"@remix-run/react": "^2.0.0"}}'
        )
        graph = self._edges(tmp_path, tech_stack=["remix"])
        assert "framework:react-router" not in graph
        assert not graph.has_edge("app/routes.ts", "app/routes/home.tsx")
