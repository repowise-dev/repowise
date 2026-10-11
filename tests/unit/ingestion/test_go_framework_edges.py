"""Unit tests for Gin/Echo/Chi framework edges (F5)."""

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
        language="go",
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
    for src in repo.rglob("*.go"):
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


class TestGinRoutes:
    def test_pkg_function_handler(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.21\n")
        users_dir = tmp_path / "users"
        users_dir.mkdir()
        (users_dir / "handler.go").write_text(
            "package users\n\nimport \"github.com/gin-gonic/gin\"\n\n"
            "func Index(c *gin.Context) {}\n"
        )
        (tmp_path / "main.go").write_text(
            "package main\n\nimport (\n  \"github.com/gin-gonic/gin\"\n  \"example.com/app/users\"\n)\n\n"
            "func main() {\n"
            "  r := gin.Default()\n"
            "  r.GET(\"/users\", users.Index)\n"
            "  r.Run()\n"
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["gin"])
        assert graph.has_edge("main.go", "users/handler.go")

    def test_lambda_handler_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "main.go").write_text(
            "package main\n\nimport \"github.com/gin-gonic/gin\"\n\n"
            "func main() {\n"
            "  r := gin.Default()\n"
            "  r.GET(\"/ping\", func(c *gin.Context) {})\n"
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        # Should not error or crash
        add_framework_edges(graph, parsed, ctx, tech_stack=["gin"])
        # Single file → no inter-file edges to itself
        assert not graph.has_edge("main.go", "main.go")


class TestNetHTTP:
    def test_handlefunc_pkg_function(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.21\n")
        h = tmp_path / "handlers"
        h.mkdir()
        (h / "users.go").write_text(
            "package handlers\n\nimport \"net/http\"\n\n"
            "func Index(w http.ResponseWriter, r *http.Request) {}\n"
        )
        (tmp_path / "main.go").write_text(
            "package main\n\nimport (\n  \"net/http\"\n  \"example.com/app/handlers\"\n)\n\n"
            "func main() {\n"
            "  http.HandleFunc(\"/users\", handlers.Index)\n"
            "  http.ListenAndServe(\":8080\", nil)\n"
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        added = add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert added >= 1
        assert graph.has_edge("main.go", "handlers/users.go")

    def test_mux_handle_local_function(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.21\n")
        (tmp_path / "routes.go").write_text(
            "package main\n\nimport \"net/http\"\n\n"
            "func register(mux *http.ServeMux) {\n"
            "  mux.Handle(\"/health\", health)\n"
            "}\n"
        )
        (tmp_path / "health.go").write_text(
            "package main\n\nimport \"net/http\"\n\n"
            "var health http.Handler\n\n"
            "func HealthHandler(w http.ResponseWriter, r *http.Request) {}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        # Should not crash; `health` is a var, not a function — no false edge.
        add_framework_edges(graph, parsed, ctx, tech_stack=[])


class TestGoGRPC:
    def test_register_server_to_impl_file(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.21\n")
        (tmp_path / "server.go").write_text(
            "package main\n\n"
            "type greeterServer struct{}\n\n"
            "func (s *greeterServer) SayHello() string { return \"hi\" }\n"
        )
        (tmp_path / "main.go").write_text(
            "package main\n\nimport \"google.golang.org/grpc\"\n\n"
            "func main() {\n"
            "  s := grpc.NewServer()\n"
            "  RegisterGreeterServer(s, &greeterServer{})\n"
            "}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["grpc"])
        assert graph.has_edge("main.go", "server.go")


class TestGoRouterGate:
    def test_non_router_unaffected(self, tmp_path: Path) -> None:
        (tmp_path / "main.go").write_text(
            "package main\n\nfunc main() {}\n"
        )
        parsed = _build_parsed(tmp_path)
        graph = nx.DiGraph()
        for p in parsed:
            graph.add_node(p)
        ctx = _ctx(tmp_path, parsed)
        count = add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert count == 0
