"""Go Phase 4 — structural interface satisfaction edges.

Go has no nominal ``implements`` clause: a concrete type satisfies an
interface purely by having the right method set. The
``go_interface_satisfaction`` pass reconstructs that relation and emits
``method_implements`` edges (concrete type → interface) so the dead-code
analyzer stops flagging interfaces reached only through their implementors.

These tests drive the real parser, GraphBuilder and analyzer end-to-end.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _file_info(path: str, abs_path: str) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=abs_path,
        language="go",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=path.endswith("_test.go"),
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


# A two-package module:
#  * storage/store.go     — defines interface Store (Get/Put) + a genuinely
#                           dead interface Orphan (no implementor).
#  * storage/embed.go     — interface ReadWriter embeds Store + adds Close.
#  * mem/mem.go           — concrete *MemStore implements Store and ReadWriter
#                           (methods split across two files in the package).
#  * mem/mem_close.go     — the Close method of MemStore (sibling file).
#  * cmd/app/main.go      — entry point.
_SOURCES: dict[str, str] = {
    "go.mod": "module example.com/app\n\ngo 1.22\n",
    "storage/store.go": (
        "package storage\n\n"
        "// Store is implemented by mem.MemStore in another package.\n"
        "type Store interface {\n"
        "\tGet(k string) string\n"
        "\tPut(k, v string)\n"
        "}\n\n"
        "// Orphan has no implementor anywhere — genuinely dead.\n"
        "type Orphan interface {\n"
        "\tFlux() int\n"
        "\tReflux() int\n"
        "}\n"
    ),
    "storage/embed.go": (
        "package storage\n\n"
        "// ReadWriter embeds Store and adds Close.\n"
        "type ReadWriter interface {\n"
        "\tStore\n"
        "\tClose() error\n"
        "}\n"
    ),
    "mem/mem.go": (
        "package mem\n\n"
        "// MemStore satisfies storage.Store and storage.ReadWriter.\n"
        "type MemStore struct{ m map[string]string }\n\n"
        "func (s *MemStore) Get(k string) string { return s.m[k] }\n"
        "func (s *MemStore) Put(k, v string)     { s.m[k] = v }\n"
    ),
    "mem/mem_close.go": (
        "package mem\n\n"
        "// Close lives in a sibling file of the same package.\n"
        "func (s *MemStore) Close() error { return nil }\n"
    ),
    "cmd/app/main.go": ("package main\n\nfunc main() {}\n"),
}


def _build_graph(repo: Path) -> nx.DiGraph:
    for rel, body in _SOURCES.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    builder = GraphBuilder(repo_path=repo)
    for rel in _SOURCES:
        if not rel.endswith(".go"):
            continue
        abs_path = str((repo / rel).resolve())
        parsed = _PARSER.parse_file(_file_info(rel, abs_path), (repo / rel).read_bytes())
        builder.add_file(parsed)
    return builder.build()


def _method_implements(graph: nx.DiGraph) -> set[tuple[str, str]]:
    return {
        (u, v)
        for u, v, d in graph.edges(data=True)
        if d.get("edge_type") == "method_implements"
    }


class TestInterfaceSatisfactionEdges:
    def test_concrete_to_interface_edge_cross_package(self, tmp_path: Path) -> None:
        graph = _build_graph(tmp_path)
        edges = _method_implements(graph)
        store_id = "storage/store.go::Store"
        mem_id = "mem/mem.go::MemStore"
        assert (mem_id, store_id) in edges

    def test_embedded_interface_satisfied(self, tmp_path: Path) -> None:
        # ReadWriter = Store (Get/Put) + Close. MemStore has all three, with
        # Close declared in a sibling package file.
        graph = _build_graph(tmp_path)
        edges = _method_implements(graph)
        rw_id = "storage/embed.go::ReadWriter"
        mem_id = "mem/mem.go::MemStore"
        assert (mem_id, rw_id) in edges

    def test_orphan_interface_gets_no_edge(self, tmp_path: Path) -> None:
        graph = _build_graph(tmp_path)
        edges = _method_implements(graph)
        orphan_id = "storage/store.go::Orphan"
        assert not any(v == orphan_id for _, v in edges)


class TestInterfaceSatisfactionDeadCode:
    def _findings(self, repo: Path):
        graph = _build_graph(repo)
        analyzer = DeadCodeAnalyzer(graph)
        return analyzer.analyze().findings

    def _unused_exports(self, repo: Path) -> set[str]:
        return {
            f.symbol_name
            for f in self._findings(repo)
            if f.kind == DeadCodeKind.UNUSED_EXPORT
        }

    def test_satisfied_interfaces_not_flagged(self, tmp_path: Path) -> None:
        exports = self._unused_exports(tmp_path)
        # Store and ReadWriter have an implementor → not dead exports.
        assert "Store" not in exports
        assert "ReadWriter" not in exports

    def test_orphan_interface_still_flagged(self, tmp_path: Path) -> None:
        # The honesty guard: a genuinely unimplemented interface stays dead.
        exports = self._unused_exports(tmp_path)
        assert "Orphan" in exports
