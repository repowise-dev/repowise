"""Phase 2 regression tests: Go package-aware reachability & never-flag rules.

Validates that Go-specific dead-code semantics hold:
- package-granular reachability (entry-package siblings, imported siblings),
- ``func init`` does NOT rescue an otherwise-unimported package,
- Go never-flag path patterns and entry-symbol names,
- Go dynamic-import markers,
- ``unused_internal`` now applies to Go (the Phase 2 exemption was lifted in
  Phase 3 once package-aware call edges landed).
"""
from __future__ import annotations

import fnmatch

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.go_reachability import (
    build_go_package_files,
    is_go_file_reachable,
)


# ---------------------------------------------------------------------------
# Graph helper (mirrors tests/unit/test_dead_code.py)
# ---------------------------------------------------------------------------

def _build_graph(nodes: dict[str, dict], edges: list | None = None) -> nx.DiGraph:
    g = nx.DiGraph()
    for name, attrs in nodes.items():
        attrs = dict(attrs)
        sym_list = attrs.pop("symbols", [])
        g.add_node(name, **attrs)
        for sym in sym_list:
            sym_id = f"{name}::{sym['name']}"
            g.add_node(sym_id, node_type="symbol", file_path=name, **sym)
            g.add_edge(name, sym_id, edge_type="defines")
    for edge in edges or []:
        if len(edge) == 3:
            g.add_edge(edge[0], edge[1], **(edge[2]))
        else:
            g.add_edge(edge[0], edge[1])
    return g


def _go_file(**overrides: object) -> dict:
    base = {
        "language": "go",
        "is_entry_point": False,
        "is_test": False,
        "is_api_contract": False,
        "symbol_count": 0,
        "symbols": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# is_go_file_reachable — unit level
# ---------------------------------------------------------------------------

class TestGoReachabilityHelper:
    def test_entry_package_sibling_is_reachable(self):
        """A helper next to ``main.go`` is reachable though nothing imports it."""
        g = _build_graph(
            nodes={
                "cmd/app/main.go": _go_file(is_entry_point=True),
                "cmd/app/server.go": _go_file(),
            }
        )
        pkgs = build_go_package_files(g)
        # server.go has no importer, but its package is an entry package.
        assert is_go_file_reachable("cmd/app/server.go", g, pkgs)

    def test_imported_sibling_makes_package_reachable(self):
        """If any sibling carries an importer, the whole package is live."""
        g = _build_graph(
            nodes={
                "pkg/cache/cache.go": _go_file(),
                "pkg/cache/pruner.go": _go_file(),
                "pkg/user/user.go": _go_file(),
            },
            edges=[("pkg/user/user.go", "pkg/cache/cache.go", {"edge_type": "imports"})],
        )
        pkgs = build_go_package_files(g)
        # pruner.go itself has in_degree 0 but cache.go (its sibling) is imported.
        assert is_go_file_reachable("pkg/cache/pruner.go", g, pkgs)

    def test_orphaned_package_is_unreachable(self):
        """A package nobody imports — no entry sibling — stays unreachable."""
        g = _build_graph(
            nodes={
                "pkg/orphan/a.go": _go_file(),
                "pkg/orphan/b.go": _go_file(),
            }
        )
        pkgs = build_go_package_files(g)
        assert not is_go_file_reachable("pkg/orphan/a.go", g, pkgs)

    def test_func_init_alone_does_not_rescue_package(self):
        """``func init`` in an unimported package does not make it live.

        ``init`` runs only when the package is linked, which requires an
        import; an unimported init-only package is genuinely dead.
        """
        g = _build_graph(
            nodes={
                "pkg/orphan/init.go": _go_file(
                    has_init=True,
                    symbols=[{
                        "name": "init",
                        "kind": "function",
                        "language": "go",
                        "visibility": "private",
                        "start_line": 1,
                        "end_line": 3,
                    }],
                ),
            }
        )
        pkgs = build_go_package_files(g)
        assert not is_go_file_reachable("pkg/orphan/init.go", g, pkgs)


# ---------------------------------------------------------------------------
# Analyzer integration — _detect_unreachable_files
# ---------------------------------------------------------------------------

class TestGoUnreachableFiles:
    def test_entry_package_helper_not_flagged(self):
        g = _build_graph(
            nodes={
                "cmd/app/main.go": _go_file(is_entry_point=True),
                "cmd/app/server.go": _go_file(),
            }
        )
        report = DeadCodeAnalyzer(g, git_meta_map={}).analyze(
            {
                "detect_unused_exports": False,
                "detect_unused_internals": False,
                "detect_zombie_packages": False,
                "min_confidence": 0.0,
            }
        )
        unreachable = {f.file_path for f in report.findings if f.kind == DeadCodeKind.UNREACHABLE_FILE}
        assert "cmd/app/server.go" not in unreachable

    def test_orphan_package_still_flagged(self):
        """Honesty guard: a genuinely-unimported package still surfaces."""
        g = _build_graph(nodes={"pkg/orphan/a.go": _go_file()})
        report = DeadCodeAnalyzer(g, git_meta_map={}).analyze(
            {
                "detect_unused_exports": False,
                "detect_unused_internals": False,
                "detect_zombie_packages": False,
                "min_confidence": 0.0,
            }
        )
        unreachable = {f.file_path for f in report.findings if f.kind == DeadCodeKind.UNREACHABLE_FILE}
        assert "pkg/orphan/a.go" in unreachable


# ---------------------------------------------------------------------------
# Never-flag patterns
# ---------------------------------------------------------------------------

class TestGoNeverFlagPatterns:
    def _matches(self, path: str) -> bool:
        from repowise.core.analysis.dead_code.constants import _NEVER_FLAG_PATTERNS
        return any(fnmatch.fnmatch(path, p) for p in _NEVER_FLAG_PATTERNS)

    def test_test_file(self):
        assert self._matches("hugolib/page_test.go")

    def test_cmd_main(self):
        assert self._matches("cmd/hugo/main.go")

    def test_root_main(self):
        assert self._matches("main.go")

    def test_doc_and_docs_stubs(self):
        assert self._matches("cache/docs.go")
        assert self._matches("tpl/openapi/docs.go")
        assert self._matches("common/doc.go")

    def test_magefile(self):
        assert self._matches("magefile.go")

    def test_generated_code(self):
        assert self._matches("api/v1/types.pb.go")
        assert self._matches("internal/color_string.go")
        assert self._matches("apis/v1/zz_generated.deepcopy.go")
        assert self._matches("assets/bindata.go")
        assert self._matches("schema/model_gen.go")

    def test_ordinary_go_file_not_flagged(self):
        assert not self._matches("hugolib/page.go")


class TestGoEntrySymbols:
    def test_init_and_testmain_are_entry_symbols(self):
        from repowise.core.analysis.dead_code.analyzer import _ENTRY_POINT_SYMBOL_NAMES
        assert "init" in _ENTRY_POINT_SYMBOL_NAMES
        assert "TestMain" in _ENTRY_POINT_SYMBOL_NAMES


class TestGoDynamicMarkers:
    def test_go_directives_present(self):
        from repowise.core.analysis.dead_code.dynamic_markers import _DYNAMIC_IMPORT_MARKERS
        go_markers = _DYNAMIC_IMPORT_MARKERS.get(".go", ())
        assert "//go:generate" in go_markers
        assert "//go:embed" in go_markers
        assert "//go:linkname" in go_markers


class TestGoUnusedInternals:
    """Phase 3 lifted the blanket Go exemption: private Go symbols are now
    subject to unused_internal, gated on real ``calls`` edges from the
    package-aware call resolver."""

    def _analyze(self, g: nx.DiGraph):
        return DeadCodeAnalyzer(g, git_meta_map={}).analyze(
            {
                "detect_unreachable_files": False,
                "detect_unused_exports": False,
                "detect_zombie_packages": False,
                "detect_unused_internals": True,
                "min_confidence": 0.0,
            }
        )

    def _sym(self) -> dict:
        return {
            "name": "helper",
            "kind": "function",
            "language": "go",
            "visibility": "private",
            "start_line": 1,
            "end_line": 10,
        }

    def test_uncalled_private_go_symbol_is_flagged(self):
        """No callers → flagged now that the exemption is lifted."""
        g = _build_graph(
            nodes={"pkg/util/util.go": _go_file(symbol_count=1, symbols=[self._sym()])}
        )
        report = self._analyze(g)
        internals = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL]
        assert any(f.symbol_name == "helper" for f in internals)

    def test_private_type_kinds_not_flagged_as_internal(self):
        """Non-callable type kinds (struct/interface) are excluded from the
        call-graph internal pass — they have no call edges and their type
        usage isn't observable as a symbol-level edge."""
        g = _build_graph(
            nodes={
                "pkg/util/util.go": _go_file(
                    symbol_count=2,
                    symbols=[
                        {"name": "config", "kind": "struct", "language": "go",
                         "visibility": "private", "start_line": 1, "end_line": 5},
                        {"name": "provider", "kind": "interface", "language": "go",
                         "visibility": "private", "start_line": 6, "end_line": 9},
                    ],
                ),
            }
        )
        report = self._analyze(g)
        names = {
            f.symbol_name for f in report.findings
            if f.kind == DeadCodeKind.UNUSED_INTERNAL
        }
        assert "config" not in names
        assert "provider" not in names

    def test_called_private_go_symbol_not_flagged(self):
        """A ``calls`` edge from a sibling keeps the symbol live."""
        g = _build_graph(
            nodes={
                "pkg/util/util.go": _go_file(symbol_count=1, symbols=[self._sym()]),
                "pkg/util/caller.go": _go_file(
                    symbol_count=1,
                    symbols=[{
                        "name": "Run",
                        "kind": "function",
                        "language": "go",
                        "visibility": "public",
                        "start_line": 1,
                        "end_line": 5,
                    }],
                ),
            },
            edges=[(
                "pkg/util/caller.go::Run",
                "pkg/util/util.go::helper",
                {"edge_type": "calls"},
            )],
        )
        report = self._analyze(g)
        internals = [f for f in report.findings if f.kind == DeadCodeKind.UNUSED_INTERNAL]
        assert all(f.symbol_name != "helper" for f in internals)
