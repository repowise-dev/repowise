"""Rust struct/enum field type references — false-positive regression.

Covers the chain that turns a Rust field's type annotation into a graph
edge the dead-code analyzer can read:

* ``field_declaration``'s ``type:`` node now captured as ``@call.target`` in
  ``rust.scm`` (plain, scoped ``super::``/``crate::`` paths, and references);
* ``type_arguments`` generalised beyond the existing ``dyn Trait`` case, so
  a type nested in ``Option<T>`` / ``Vec<T>`` is also captured;
* the existing ``CallResolver`` free-call tiers (same-file, then
  cross-file global-unique-name) turn that capture into a ``calls`` edge
  with no changes to the resolver itself;
* the end-to-end dead-code outcome: a struct used only as a field type,
  including across files via a path-qualified reference with no ``use``
  statement, is no longer flagged as an unused export — while a genuinely
  unreferenced struct in the same crate still is (the honesty guard).

Before this fix, ``field_declaration`` had no type capture at all, so a
struct referenced only as a struct/enum field never became a ``CallSite``
and read as having "no importers" regardless of how it was used. Since
``enum_variant``'s struct-like body reuses the ``field_declaration`` node,
this single gap covered both the plain-struct-field and enum-variant-field
false positives reported against ``get_dead_code``.

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
        language="rust",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=path.endswith("_test.rs") or "/tests/" in path,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


# ---------------------------------------------------------------------------
# Parser: field type capture
# ---------------------------------------------------------------------------


class TestRustFieldTypeCapture:
    def _calls(self, body: str) -> set[str]:
        info = _file_info("p/f.rs", "/repo/p/f.rs")
        parsed = _PARSER.parse_file(info, body.encode("utf-8"))
        return {c.target_name for c in parsed.calls}

    def test_plain_struct_field_type_captured(self) -> None:
        names = self._calls("pub struct Foo { bar: Bar }\n")
        assert "Bar" in names

    def test_scoped_struct_field_type_captured(self) -> None:
        names = self._calls("pub struct Foo { bar: super::Bar }\n")
        assert "Bar" in names

    def test_enum_struct_variant_field_type_captured(self) -> None:
        # enum_variant reuses field_declaration for its struct-like body.
        names = self._calls(
            "pub enum Event {\n"
            "    Received { state: Bar },\n"
            "}\n"
        )
        assert "Bar" in names

    def test_generic_wrapped_scoped_field_type_captured(self) -> None:
        # The reported false positive's exact shape: Option<super::Bar>.
        names = self._calls(
            "pub enum Event {\n"
            "    Received { state: Option<super::Bar> },\n"
            "}\n"
        )
        assert "Bar" in names

    def test_reference_field_type_captured(self) -> None:
        names = self._calls("pub struct Foo { bar: &Bar }\n")
        assert "Bar" in names


# ---------------------------------------------------------------------------
# Graph + dead-code outcome (end-to-end through GraphBuilder)
# ---------------------------------------------------------------------------

# A crate laid out to mirror the reported bug: a struct defined in one
# module file, referenced only as an enum-variant field type from a sibling
# module file via a `super::` path — no `use` statement anywhere.
_SOURCES: dict[str, str] = {
    "Cargo.toml": '[package]\nname = "net"\nversion = "0.1.0"\n',
    "src/lib.rs": "pub mod transport;\n",
    "src/transport.rs": "pub mod state;\npub mod types;\n",
    "src/transport/state.rs": (
        "// RecvState is used only as an enum-variant field type in\n"
        "// types.rs, crossing files with no `use` statement.\n"
        "pub struct RecvState { pub id: u32 }\n\n"
        "// DeadState is never referenced anywhere — a true positive that\n"
        "// must survive the fix.\n"
        "pub struct DeadState { pub id: u32 }\n"
    ),
    "src/transport/types.rs": (
        "pub enum Event {\n"
        "    Received { state: Option<super::state::RecvState> },\n"
        "}\n"
    ),
}


def _build_graph(repo: Path) -> nx.DiGraph:
    for rel, body in _SOURCES.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    builder = GraphBuilder(repo_path=repo)
    for rel in _SOURCES:
        if not rel.endswith(".rs"):
            continue
        abs_path = str((repo / rel).resolve())
        parsed = _PARSER.parse_file(_file_info(rel, abs_path), (repo / rel).read_bytes())
        builder.add_file(parsed)
    return builder.build()


class TestRustFieldTypeUseEdge:
    def test_cross_file_field_type_produces_calls_edge(self, tmp_path: Path) -> None:
        graph = _build_graph(tmp_path)
        assert graph.has_edge(
            "src/transport/types.rs::Received",
            "src/transport/state.rs::RecvState",
        )


class TestRustFieldTypeDeadCodeOutcome:
    def _report(self, graph: nx.DiGraph):
        analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
        return analyzer.analyze(
            {
                "detect_unreachable_files": False,
                "detect_zombie_packages": False,
                "detect_unused_internals": False,
                "min_confidence": 0.0,
            }
        )

    def test_cross_file_field_referenced_struct_not_flagged(self, tmp_path: Path) -> None:
        report = self._report(_build_graph(tmp_path))
        unused_exports = {
            f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT
        }
        assert "RecvState" not in unused_exports

    def test_genuinely_dead_struct_in_same_crate_still_flagged(self, tmp_path: Path) -> None:
        # True-positive guard: the field-type capture must not turn into a
        # blanket exemption for every struct in a crate with field types.
        report = self._report(_build_graph(tmp_path))
        unused_exports = {
            f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT
        }
        assert "DeadState" in unused_exports
