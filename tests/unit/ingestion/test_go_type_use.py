"""Go Phase 3 — call & type-reference resolution.

Covers the full chain that turns Go's package semantics into graph edges
the dead-code analyzer can read:

* ``@param.type`` capture → ``TypeReference`` extraction with the Go head
  extractor (unwraps ``*T`` / ``[]T`` / ``map[K]V`` / ``pkg.T``);
* ``_resolve_go_type_refs`` → ``type_use`` edges for types used only as
  field / parameter / return / composite-literal types;
* package-aware call resolution (``pkg.Func`` across a multi-file package,
  and bare same-package sibling calls);
* the end-to-end dead-code outcome: types/functions used cross-file or
  cross-package are no longer flagged, while genuinely-dead symbols still
  are (the honesty guard).

All tests drive the real parser, GraphBuilder and analyzer — no mocking of
the resolution path.
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


# --- A small multi-package module written to disk, mirroring Hugo's -------
# cache/dynacache layout (multi-file package + cross-package consumer).

_MODULE = "example.com/app"

_SOURCES: dict[str, str] = {
    "go.mod": "module example.com/app\n\ngo 1.22\n",
    # --- dynacache: a multi-file package ---
    "cache/dynacache/dynacache.go": (
        "package dynacache\n\n"
        "// Cache is used cross-package as a return type.\n"
        "type Cache struct {\n"
        "\tpart *Partition\n"
        "}\n\n"
        "// Partition is referenced from options.go (cross-file) and here.\n"
        "type Partition struct{}\n\n"
        "func (c *Cache) Part() *Partition { return c.part }\n\n"
        "// newPartition is private and called from the sibling options.go.\n"
        "func newPartition() *Partition { return &Partition{} }\n\n"
        "// orphanInternal is private and never called — genuinely dead.\n"
        "func orphanInternal() {}\n"
    ),
    "cache/dynacache/options.go": (
        "package dynacache\n\n"
        "// Options is used cross-package as a composite literal.\n"
        "type Options struct {\n"
        "\tseed *Partition\n"
        "}\n\n"
        "// New is exported and called cross-package from consumer.\n"
        "func New(o Options) *Cache {\n"
        "\t_ = newPartition()\n"
        "\treturn &Cache{}\n"
        "}\n"
    ),
    # --- consumer: imports dynacache, uses it only via types + a call ---
    "consumer/consumer.go": (
        "package consumer\n\n"
        'import "example.com/app/cache/dynacache"\n\n'
        "// Build references New (call), Options (composite) and Cache\n"
        "// (return type) across the package boundary.\n"
        "func Build() *dynacache.Cache {\n"
        "\treturn dynacache.New(dynacache.Options{})\n"
        "}\n\n"
        "// DeadExport is exported and never referenced — genuinely dead.\n"
        "func DeadExport() {}\n"
    ),
    # --- main: the entry that reaches consumer.Build ---
    "cmd/app/main.go": (
        "package main\n\n"
        'import "example.com/app/consumer"\n\n'
        "func main() { consumer.Build() }\n"
    ),
}


def _build_graph(repo: Path) -> nx.DiGraph:
    """Write the module to *repo*, parse every .go file, build the graph."""
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


# ---------------------------------------------------------------------------
# Parser: Go type-ref extraction + head unwrapping
# ---------------------------------------------------------------------------


class TestGoTypeRefExtraction:
    def _parse(self, body: str) -> list:
        info = _file_info("p/f.go", "/repo/p/f.go")
        return _PARSER.parse_file(info, body.encode("utf-8")).type_refs

    def test_field_param_return_composite_captured(self) -> None:
        refs = self._parse(
            "package p\n"
            "type Opt struct { inner *Bar }\n"
            "func New(o Opt) *Baz { return &Baz{} }\n"
        )
        names = {r.type_name for r in refs}
        assert {"Bar", "Opt", "Baz"} <= names

    def test_builtins_filtered(self) -> None:
        refs = self._parse(
            "package p\n"
            "func f(s string, n int, b bool, e error) (float64, byte) { return 0, 0 }\n"
        )
        names = {r.type_name for r in refs}
        assert names.isdisjoint({"string", "int", "bool", "error", "float64", "byte"})

    def test_qualifier_dropped_and_containers_unwrapped(self) -> None:
        refs = self._parse(
            "package p\n"
            "type T struct {\n"
            "\ta *Alpha\n"
            "\tb []Beta\n"
            "\tc map[string]Gamma\n"
            "\td pkg.Delta\n"
            "}\n"
        )
        names = {r.type_name for r in refs}
        # Pointer/slice/map unwrapped to their named element; map key (string)
        # filtered; qualifier on pkg.Delta dropped to the bare type.
        assert {"Alpha", "Beta", "Gamma", "Delta"} <= names
        assert "pkg" not in names
        assert "string" not in names

    def test_single_uppercase_generic_param_skipped(self) -> None:
        refs = self._parse("package p\nfunc f[T any](x T) T { return x }\n")
        assert "T" not in {r.type_name for r in refs}


# ---------------------------------------------------------------------------
# type_use edges + call edges (end-to-end through GraphBuilder)
# ---------------------------------------------------------------------------


class TestGoTypeUseEdges:
    def test_cross_package_type_use_edges(self, tmp_path: Path) -> None:
        graph = _build_graph(tmp_path)
        consumer = "consumer/consumer.go"
        # Cache lives in dynacache.go, Options in options.go — both reached
        # from consumer only as types, never imported by name.
        assert graph.has_edge(consumer, "cache/dynacache/dynacache.go")
        assert graph.has_edge(consumer, "cache/dynacache/options.go")
        cache_edge = graph[consumer]["cache/dynacache/dynacache.go"]
        opt_edge = graph[consumer]["cache/dynacache/options.go"]
        assert "Cache" in cache_edge.get("imported_names", [])
        assert "Options" in opt_edge.get("imported_names", [])

    def test_same_package_cross_file_type_use_edge(self, tmp_path: Path) -> None:
        graph = _build_graph(tmp_path)
        # options.go references Partition (defined in dynacache.go) as a field.
        edge = graph.get_edge_data(
            "cache/dynacache/options.go", "cache/dynacache/dynacache.go", {}
        )
        assert "Partition" in edge.get("imported_names", [])


class TestGoCallResolution:
    def test_package_qualified_call_resolves_across_files(self, tmp_path: Path) -> None:
        graph = _build_graph(tmp_path)
        # consumer.Build calls dynacache.New; New is defined in options.go,
        # not the package's first file. The package-aware resolver must
        # still produce the calls edge.
        build_id = "consumer/consumer.go::Build"
        new_id = "cache/dynacache/options.go::New"
        assert graph.has_edge(build_id, new_id)
        assert graph[build_id][new_id]["edge_type"] == "calls"

    def test_same_package_bare_call_resolves(self, tmp_path: Path) -> None:
        graph = _build_graph(tmp_path)
        # New (options.go) calls newPartition (dynacache.go) with no import.
        caller = "cache/dynacache/options.go::New"
        callee = "cache/dynacache/dynacache.go::newPartition"
        assert graph.has_edge(caller, callee)
        assert graph[caller][callee]["edge_type"] == "calls"


# ---------------------------------------------------------------------------
# Dead-code outcome — the honesty guard
# ---------------------------------------------------------------------------


class TestGoDeadCodeOutcome:
    def _report(self, graph: nx.DiGraph):
        analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
        return analyzer.analyze(
            {
                "detect_unreachable_files": False,
                "detect_zombie_packages": False,
                "detect_unused_internals": True,
                "min_confidence": 0.0,
            }
        )

    def test_used_types_and_functions_not_flagged(self, tmp_path: Path) -> None:
        report = self._report(_build_graph(tmp_path))
        unused_exports = {
            f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT
        }
        # Cross-package / cross-file uses must clear these.
        assert {"New", "Cache", "Options", "Partition", "Build"}.isdisjoint(unused_exports)

    def test_genuinely_dead_export_still_flagged(self, tmp_path: Path) -> None:
        report = self._report(_build_graph(tmp_path))
        unused_exports = {
            f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT
        }
        assert "DeadExport" in unused_exports

    def test_private_called_across_siblings_not_flagged(self, tmp_path: Path) -> None:
        report = self._report(_build_graph(tmp_path))
        unused_internals = {
            f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL
        }
        # newPartition is called from a sibling file → live now that the Go
        # exemption is lifted and same-package call edges exist.
        assert "newPartition" not in unused_internals

    def test_genuinely_dead_internal_still_flagged(self, tmp_path: Path) -> None:
        report = self._report(_build_graph(tmp_path))
        unused_internals = {
            f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL
        }
        assert "orphanInternal" in unused_internals
