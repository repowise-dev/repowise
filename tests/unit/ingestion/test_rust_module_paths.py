"""Rust module paths: brace-group imports, the 2018 file layout, ``self``/``super``.

A Rust module ``foo`` lives in ``foo.rs`` with its children under ``foo/``
(or in ``foo/mod.rs``), so ``self``, ``super`` and ``mod bar;`` resolve
against the module tree, not the importer's directory. A brace group such
as ``use crate::{a::B, c::D}`` names several modules, each resolved alone.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import ClassVar

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.rust import resolve_rust_import

_PARSER = ASTParser()


def _file_info(path: str, abs_path: str) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=abs_path,
        language="rust",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _imports(body: str) -> list[tuple[str, list[str]]]:
    parsed = _PARSER.parse_file(_file_info("src/c.rs", "/repo/src/c.rs"), body.encode("utf-8"))
    return [(imp.module_path, imp.imported_names) for imp in parsed.imports]


def _resolve(paths: list[str], module_path: str, importer: str) -> str | None:
    ctx = ResolverContext(
        path_set=set(paths), stem_map={}, graph=nx.DiGraph(), repo_path=Path("/repo")
    )
    ctx.parsed_files = {p: None for p in paths}
    return resolve_rust_import(module_path, importer, ctx)


def _build(repo: Path, sources: dict[str, str]) -> nx.DiGraph:
    builder = GraphBuilder(repo_path=repo)
    for rel, body in sources.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    for rel in sources:
        if rel.endswith(".rs"):
            info = _file_info(rel, str((repo / rel).resolve()))
            builder.add_file(_PARSER.parse_file(info, (repo / rel).read_bytes()))
    return builder.build()


class TestUseTreeExpansion:
    def test_nested_brace_group_is_one_import_per_leaf(self) -> None:
        imports = _imports("use crate::{a::{B, C as D}, e::*, f::{self}};\n")
        assert imports == [
            ("crate::a::B", ["B"]),
            ("crate::a::C", ["D"]),
            ("crate::e::*", ["*"]),
            ("crate::f", ["f"]),
        ]

    def test_plain_use_keeps_its_single_import(self) -> None:
        assert _imports("use crate::a::B;\n") == [("crate::a::B", ["B"])]

    def test_super_out_of_an_inline_module_stays_in_the_file(self) -> None:
        body = "mod tests {\n    use super::*;\n    use super::super::x::Y;\n}\n"
        assert _imports(body) == [("self::*", ["*"]), ("super::x::Y", ["Y"])]


class TestModuleTreeResolution:
    _PATHS: ClassVar[list[str]] = [
        "src/lib.rs",
        "src/transport.rs",
        "src/transport/state.rs",
        "src/transport/types.rs",
        "src/a.rs",
        "src/a/inner.rs",
        "src/bin/tool.rs",
        "src/bin/helper.rs",
    ]

    def test_mod_item_in_a_non_mod_file_resolves_under_its_directory(self) -> None:
        assert _resolve(self._PATHS, "state", "src/transport.rs") == "src/transport/state.rs"

    def test_bare_path_through_a_child_module(self) -> None:
        resolved = _resolve(self._PATHS, "state::RecvState", "src/transport.rs")
        assert resolved == "src/transport/state.rs"

    def test_super_names_the_parent_module_of_a_non_mod_file(self) -> None:
        resolved = _resolve(self._PATHS, "super::state::RecvState", "src/transport/types.rs")
        assert resolved == "src/transport/state.rs"

    def test_super_item_lands_on_the_parent_module_file(self) -> None:
        resolved = _resolve(self._PATHS, "super::RecvState", "src/transport/types.rs")
        assert resolved == "src/transport.rs"

    def test_self_names_the_child_directory_of_a_non_mod_file(self) -> None:
        assert _resolve(self._PATHS, "self::inner::Deep", "src/a.rs") == "src/a/inner.rs"

    def test_crate_root_outside_lib_keeps_its_sibling_children(self) -> None:
        assert _resolve(self._PATHS, "helper", "src/bin/tool.rs") == "src/bin/helper.rs"

    def test_self_item_of_the_importer_resolves_to_no_file(self) -> None:
        assert _resolve(self._PATHS, "self::Local", "src/a.rs") is None


class TestIntraCrateDeadCode:
    """The reported shape, in the 2018 layout: no ``use`` reaches the types."""

    _SOURCES: ClassVar[dict[str, str]] = {
        "Cargo.toml": '[package]\nname = "net"\nversion = "0.1.0"\n',
        "src/lib.rs": "pub mod transport;\npub mod a;\n\npub struct DeadRoot;\n",
        "src/transport.rs": "mod state;\nmod types;\npub use types::Event;\n",
        "src/transport/state.rs": "pub struct RecvState { pub n: u32 }\n",
        "src/transport/types.rs": (
            "pub enum Event {\n    Received { state: Option<super::state::RecvState> },\n}\n"
        ),
        "src/a.rs": (
            "mod inner;\npub use self::inner::Deep;\n"
            "mod tests {\n    use super::*;\n}\n"
        ),
        "src/a/inner.rs": "pub struct Deep;\n",
    }

    def _unused(self, graph: nx.DiGraph) -> set[str]:
        report = DeadCodeAnalyzer(graph, git_meta_map={}).analyze(
            {"detect_zombie_packages": False, "detect_unused_internals": False,
             "min_confidence": 0.0}
        )
        return {
            f"{f.file_path}::{f.symbol_name}" if f.symbol_name else f.file_path
            for f in report.findings
            if f.kind in (DeadCodeKind.UNUSED_EXPORT, DeadCodeKind.UNREACHABLE_FILE)
        }

    def test_types_reached_through_module_paths_are_not_flagged(self, tmp_path: Path) -> None:
        unused = self._unused(_build(tmp_path, self._SOURCES))
        assert "src/transport/types.rs::Event" not in unused
        assert "src/a/inner.rs::Deep" not in unused
        assert "src/transport/state.rs" not in unused

    def test_dead_struct_in_the_crate_root_is_still_flagged(self, tmp_path: Path) -> None:
        assert "src/lib.rs::DeadRoot" in self._unused(_build(tmp_path, self._SOURCES))

    def test_inline_test_glob_adds_no_edge_to_the_parent_module(self, tmp_path: Path) -> None:
        graph = _build(tmp_path, self._SOURCES)
        assert not graph.has_edge("src/a.rs", "src/lib.rs")


class TestNoFalseEdges:
    def test_missing_brace_member_resolves_to_no_repo_file(self, tmp_path: Path) -> None:
        graph = _build(
            tmp_path,
            {
                "src/lib.rs": "pub mod b;\npub mod c;\n",
                "src/b.rs": "pub struct Present;\n",
                "src/c.rs": "use crate::{missing::Gone, b::Present};\n",
            },
        )
        assert graph["src/c.rs"]["src/b.rs"]["imported_names"] == ["Present"]
        assert not any(
            v.startswith("src/") and v != "src/b.rs"
            for v in graph.successors("src/c.rs")
            if graph.nodes[v].get("node_type") != "symbol"
        )

    def test_type_parameter_does_not_reach_a_same_named_struct(self, tmp_path: Path) -> None:
        # ``Item`` in the field is Wrapper's type parameter, not the struct in
        # b.rs, even though that struct is the only ``Item`` in the crate.
        graph = _build(
            tmp_path,
            {
                "src/lib.rs": "pub mod a;\npub mod b;\n",
                "src/a.rs": "pub struct Wrapper<Item> { pub value: Item }\n",
                "src/b.rs": "pub struct Item { pub id: u32 }\n",
            },
        )
        assert not graph.has_edge("src/a.rs", "src/b.rs")


class TestImportedNamesInScope:
    """``use a::{B}`` brings B into scope, not the rest of ``a``."""

    _LIB: ClassVar[dict[str, str]] = {
        "src/lib.rs": "pub mod a;\npub mod b;\npub mod c;\n",
        "src/a.rs": "pub struct B;\nimpl B { pub fn new() -> B { B } }\npub fn other() {}\n",
        # A second ``other`` keeps the repo-wide tier out of the answer.
        "src/b.rs": "pub fn other() {}\n",
    }

    def _callees(self, tmp_path: Path, caller: str) -> set[str]:
        graph = _build(tmp_path, {**self._LIB, "src/c.rs": caller})
        return {
            v
            for u, v, d in graph.edges(data=True)
            if u.startswith("src/c.rs::") and d.get("edge_type") == "calls"
        }

    def test_a_name_the_import_does_not_select_stays_unresolved(self, tmp_path: Path) -> None:
        caller = "use crate::{a::{B}};\npub fn go() { B::new().other(); }\n"
        assert "src/a.rs::other" not in self._callees(tmp_path, caller)

    def test_a_glob_import_brings_every_name_into_scope(self, tmp_path: Path) -> None:
        caller = "use crate::a::*;\npub fn go() { other(); }\n"
        assert "src/a.rs::other" in self._callees(tmp_path, caller)
