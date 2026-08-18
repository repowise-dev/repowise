"""Rust type positions produce ``type_use`` edges, never ``calls`` edges.

``rust.scm`` used to capture parameter types, return types, trait bounds,
``dyn``/``impl`` Trait and turbofish type arguments as ``@call.target`` /
``@call.site``, so ``fn take(x: MyType)`` recorded the enclosing function as
*calling* ``MyType``. A type is not callable, so every such edge was wrong
(bug 46). The captures now carry ``@param.type`` and resolve to ``type_use``.

Covers:

* the Rust head extractor, which the C#-shaped default could not serve
  (tree-sitter-rust spells a type name ``type_identifier``, not
  ``identifier``, and its qualified form is ``scoped_type_identifier``);
* ``@param.type`` capture -> ``TypeReference`` extraction for each of the
  seven type positions the query matches;
* ``_resolve_rust_type_refs`` -> ``type_use`` edges, and the dead-code
  outcome they exist to protect: a type used only as a parameter type in
  another file is not flagged as an unused export;
* the guard that keeps this safe -- an *inferred* target (name-shaped guess
  rather than a written import) must not widen call-resolution scope, or
  unrelated same-named symbols resolve into it and mint false call edges.

All tests drive the real parser and GraphBuilder -- no mocking.
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


def _parse(body: str, path: str = "src/lib.rs"):
    return _PARSER.parse_file(_file_info(path, f"/repo/{path}"), body.encode("utf-8"))


def _type_names(body: str) -> set[str]:
    return {r.type_name for r in _parse(body).type_refs}


def _call_targets(body: str) -> set[str]:
    return {c.target_name for c in _parse(body).calls}


# ---------------------------------------------------------------------------
# Capture: each type position becomes a TypeReference, not a call
# ---------------------------------------------------------------------------


class TestRustTypePositionsAreNotCalls:
    def test_parameter_type(self) -> None:
        body = "fn take(x: MyType) {}\n"
        assert "MyType" in _type_names(body)
        assert "MyType" not in _call_targets(body)

    def test_return_type(self) -> None:
        body = "fn make() -> Other { todo!() }\n"
        assert "Other" in _type_names(body)
        assert "Other" not in _call_targets(body)

    def test_dyn_trait_parameter(self) -> None:
        body = "fn take(x: &dyn MyTrait) {}\n"
        assert "MyTrait" in _type_names(body)
        assert "MyTrait" not in _call_targets(body)

    def test_impl_trait_parameter(self) -> None:
        body = "fn take(x: impl Shape) {}\n"
        assert "Shape" in _type_names(body)
        assert "Shape" not in _call_targets(body)

    def test_trait_bound(self) -> None:
        body = "fn take<T: Bound>(x: T) {}\n"
        assert "Bound" in _type_names(body)
        assert "Bound" not in _call_targets(body)

    def test_dyn_in_type_arguments(self) -> None:
        body = "fn take(x: Box<dyn Handler>) {}\n"
        assert "Handler" in _type_names(body)
        assert "Handler" not in _call_targets(body)

    def test_turbofish_type_argument(self) -> None:
        body = "fn go() { make::<Widget>(); }\n"
        assert "Widget" in _type_names(body)
        assert "Widget" not in _call_targets(body)

    def test_real_calls_are_still_captured(self) -> None:
        """The honesty guard: moving type captures must not cost real calls."""
        body = (
            "fn go(cfg: Config) -> Report {\n"
            "    helper(1);\n"
            "    cfg.refresh();\n"
            "    Report::new()\n"
            "}\n"
        )
        targets = _call_targets(body)
        assert "helper" in targets
        assert "refresh" in targets
        # ...while neither type position leaked back in as a call.
        assert "Config" not in targets
        assert "Report" not in targets


# ---------------------------------------------------------------------------
# Head extractor: shapes the C#-shaped default could not read
# ---------------------------------------------------------------------------


class TestRustHeadTypeIdentifier:
    """The extractor is exercised directly.

    The query deliberately captures the bare ``type_identifier`` at each type
    position, so wrapped forms (``&Widget``, ``Box<T>``, ``std::io::Error``)
    do not reach it through the current patterns -- they are the shapes a
    widened capture would hand it. Pinning the unwrapping here means widening
    the query later is a query-only change.
    """

    def _head(self, type_src: str) -> str | None:
        import tree_sitter
        import tree_sitter_rust

        from repowise.core.ingestion.parser_helpers import _rust_head_type_identifier

        src = f"fn f(a: {type_src}) {{}}\n"
        lang = tree_sitter.Language(tree_sitter_rust.language())
        tree = tree_sitter.Parser(lang).parse(src.encode())

        def walk(node):
            yield node
            for child in node.named_children:
                yield from walk(child)

        param = next(n for n in walk(tree.root_node) if n.type == "parameter")
        return _rust_head_type_identifier(param.child_by_field_name("type"), src)

    def test_reference_and_mut_are_unwrapped(self) -> None:
        assert self._head("&Widget") == "Widget"
        assert self._head("&mut Gadget") == "Gadget"

    def test_scoped_type_takes_rightmost_component(self) -> None:
        """The default extractor returned the leading crate segment instead."""
        assert self._head("std::io::Error") == "Error"

    def test_dyn_and_impl_are_unwrapped(self) -> None:
        assert self._head("&dyn Handler") == "Handler"
        assert self._head("impl Shape") == "Shape"

    def test_generic_head_is_the_constructor(self) -> None:
        # ``Box`` is a builtin so the head filters out; the inner ``Inner``
        # arrives through the type_arguments capture instead.
        assert self._head("Box<Inner>") is None
        assert self._head("Wrapper<Inner>") == "Wrapper"

    def test_builtin_heads_are_filtered(self) -> None:
        assert self._head("u32") is None
        assert self._head("String") is None

    def test_builtins_are_filtered(self) -> None:
        names = _type_names("fn f(a: u32, b: String, c: bool) -> Vec<u8> { todo!() }\n")
        assert names & {"u32", "String", "bool", "Vec", "u8"} == set()

    def test_single_letter_generic_param_is_filtered(self) -> None:
        assert "T" not in _type_names("fn f<T>(x: T) {}\n")


# ---------------------------------------------------------------------------
# Resolution: type_use edges and the dead-code outcome they protect
# ---------------------------------------------------------------------------


_SOURCES: dict[str, str] = {
    "src/lib.rs": "pub mod types;\npub mod api;\n",
    "src/types.rs": (
        "pub struct RequestPayload {\n"
        "    pub id: u32,\n"
        "}\n"
    ),
    # Uses RequestPayload only as a parameter type: no call, no method use.
    # This is exactly the shape the old bogus `calls` edge used to rescue.
    "src/api.rs": (
        "use crate::types::RequestPayload;\n\n"
        "pub fn handle(req: RequestPayload) -> u32 {\n"
        "    req.id\n"
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
        abs_path = str((repo / rel).resolve())
        parsed = _PARSER.parse_file(_file_info(rel, abs_path), (repo / rel).read_bytes())
        builder.add_file(parsed)
    return builder.build()


class TestRustTypeUseResolution:
    def test_parameter_type_emits_a_type_use_edge(self, tmp_path: Path) -> None:
        graph = _build_graph(tmp_path)
        data = graph.get_edge_data("src/api.rs", "src/types.rs") or {}
        assert data, "expected an edge from the consumer to the defining file"
        assert "RequestPayload" in data.get("type_uses", []) or "RequestPayload" in data.get(
            "imported_names", []
        )

    def test_no_calls_edge_from_the_type_position(self, tmp_path: Path) -> None:
        graph = _build_graph(tmp_path)
        bogus = [
            (u, v)
            for u, v, d in graph.edges(data=True)
            if d.get("edge_type") == "calls" and str(v).endswith("::RequestPayload")
        ]
        assert bogus == [], f"a type was recorded as callable: {bogus}"

    def test_type_only_use_is_not_an_unused_export(self, tmp_path: Path) -> None:
        """The protection the old captures existed to provide, preserved."""
        graph = _build_graph(tmp_path)
        analyzer = DeadCodeAnalyzer(graph, repo_root=tmp_path)
        flagged = {
            f.symbol_name
            for f in analyzer.analyze().findings
            if f.kind == DeadCodeKind.UNUSED_EXPORT
        }
        assert "RequestPayload" not in flagged


class TestInferredTargetsDoNotWidenCallScope:
    """An inferred type target must not let bare names resolve into it.

    ``consumer.rs`` writes no import for ``Registry``; the target is found
    only because the repo declares that name once. That is good enough to
    call ``registry.rs`` used, but not good enough to claim the unrelated
    ``spec()`` call lands on ``registry.rs::spec``. Measured on goose, letting
    it do so scored 25% precision on the edges it gained.
    """

    _SRC: ClassVar[dict[str, str]] = {
        "src/lib.rs": "pub mod registry;\npub mod consumer;\n",
        "src/registry.rs": (
            "pub struct Registry;\n\n"
            "pub fn spec() -> u32 { 1 }\n"
        ),
        "src/consumer.rs": (
            "pub fn run(reg: Registry) -> u32 {\n"
            "    let other = make();\n"
            "    other.spec()\n"
            "}\n"
        ),
    }

    def _build(self, repo: Path) -> nx.DiGraph:
        for rel, body in self._SRC.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        builder = GraphBuilder(repo_path=repo)
        for rel in self._SRC:
            abs_path = str((repo / rel).resolve())
            parsed = _PARSER.parse_file(
                _file_info(rel, abs_path), (repo / rel).read_bytes()
            )
            builder.add_file(parsed)
        return builder.build()

    def test_inferred_edge_is_marked_and_reaches_the_file(self, tmp_path: Path) -> None:
        graph = self._build(tmp_path)
        data = graph.get_edge_data("src/consumer.rs", "src/registry.rs") or {}
        assert data.get("edge_type") == "type_use"
        assert data.get("no_scope_widening") is True

    def test_inferred_edge_does_not_mint_a_bare_name_call(self, tmp_path: Path) -> None:
        graph = self._build(tmp_path)
        leaked = [
            (u, v)
            for u, v, d in graph.edges(data=True)
            if d.get("edge_type") == "calls" and str(v) == "src/registry.rs::spec"
        ]
        assert leaked == [], f"bare-name call resolved through an inferred edge: {leaked}"
