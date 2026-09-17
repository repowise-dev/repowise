"""Rust type positions produce ``type_use`` edges, never ``calls`` edges.

``rust.scm`` used to capture parameter types, return types, trait bounds,
``dyn``/``impl`` Trait and turbofish type arguments as ``@call.target`` /
``@call.site``, so ``fn take(x: MyType)`` recorded the enclosing function as
*calling* ``MyType``. A type is not callable, so every such edge was wrong.
The captures now carry ``@param.type`` and resolve to ``type_use``.

Covers:

* the Rust head extractor, which the C#-shaped default could not serve
  (tree-sitter-rust spells a type name ``type_identifier``, not
  ``identifier``, and its qualified form is ``scoped_type_identifier``);
* ``@param.type`` capture -> ``TypeReference`` extraction for each of the
  four type positions the query matches, including the wrapped shapes the
  patterns used to miss (``&Foo``, ``Box<Foo>``, ``std::io::Error``);
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
import pytest

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
# Query level: the wrapped shapes the patterns used to drop (#2298)
# ---------------------------------------------------------------------------
# ``rust.scm`` captured the bare ``type_identifier`` at its parameter /
# return / trait-bound / type-argument positions, so a wrapped type node
# never matched and its head never reached the extractor. ``&Foo``,
# ``Box<Foo>`` and ``std::io::Error`` are the ordinary way to write Rust, so
# most type uses went unrecorded and a type used only that way could read as
# an unused export (``type_use`` is in ``REACHABILITY_USE_EDGE_TYPES``).
#
# The patterns now hand the enclosing type node to the extractor, which was
# already able to unwrap every one of these shapes (pinned above at the
# extractor level). These tests are the query-level half: they assert the
# capture actually reaches the extractor from each position.


class TestWrappedTypePositionCaptures:
    """Each wrapped shape, in parameter and return position, per #2298."""

    @pytest.mark.parametrize(
        ("signature", "expected"),
        [
            # The three shapes named in the issue.
            ("fn f(x: &MyType) {}", "MyType"),
            ("fn f(x: Box<MyType>) {}", "MyType"),
            ("fn f() -> std::io::Error { unimplemented!() }", "Error"),
            # Adjacent shapes the same widening covers.
            ("fn f(x: &mut MyType) {}", "MyType"),
            ("fn f() -> &MyType { todo!() }", "MyType"),
            ("fn f() -> Box<MyType> { todo!() }", "MyType"),
            ("fn f(x: *const MyType) {}", "MyType"),
            ("fn f(x: std::io::Error) {}", "Error"),
            ("fn f(x: crate::types::MyType) {}", "MyType"),
            ("fn f(x: Wrapper<MyType>) {}", "Wrapper"),
            ("fn f(x: &mut Vec<MyType>) {}", None),
        ],
    )
    def test_shape_produces_the_head(self, signature: str, expected: str | None) -> None:
        names = _type_names(signature + "\n")
        if expected is None:
            # ``Vec`` is a builtin head; the inner arg arrives via its own
            # type_arguments capture, so no name comes out of the head walk.
            assert "MyType" in names
        else:
            assert expected in names

    def test_wrapped_shapes_are_not_recorded_as_calls(self) -> None:
        body = (
            "fn one(x: &MyType) {}\n"
            "fn two(x: Box<MyType>) {}\n"
            "fn three() -> std::io::Error { unimplemented!() }\n"
            "fn four(x: &mut MyType) {}\n"
            "fn five() -> Box<MyType> { todo!() }\n"
        )
        targets = _call_targets(body)
        assert targets.isdisjoint({"MyType", "Error"})

    def test_nested_generic_argument_is_reached_recursively(self) -> None:
        """A type_arguments pattern matches nested argument nodes too."""
        names = _type_names("fn f(x: Option<Box<Inner>>) {}\n")
        assert "Inner" in names

    def test_wrapped_trait_bound_head_is_resolved(self) -> None:
        # A scoped or generic bound used to be dropped by the bare-only
        # pattern; the extractor unwraps both.
        assert "MyTrait" in _type_names("fn f<T: ns::MyTrait>(x: T) {}\n")
        assert "Bound" in _type_names("fn f<T: Bound<Inner>>(x: T) {}\n")

    def test_wrapped_turbofish_argument_is_resolved(self) -> None:
        assert "Widget" in _type_names("fn go() { make::<std::sync::Widget>(); }\n")

    def test_type_binding_is_not_mistaken_for_a_type(self) -> None:
        """``Iterator<Item = Foo>`` binds ``Item``; the head must be no name.

        Capturing ``(_)`` inside ``type_arguments`` would match the
        ``type_binding`` node, whose head identifier the extractor reads as
        the binding name. ``Item`` is not a type, and if a repository
        happened to declare that symbol the edge would point at it.
        """
        names = _type_names("fn f() -> impl Iterator<Item = Foo> { todo!() }\n")
        assert "Item" not in names
        assert "Iterator" not in names  # head of the abstract_type is filtered/none

    def test_builtins_stay_filtered_in_wrapped_positions(self) -> None:
        body = (
            "fn f(a: &u32, b: Box<String>) -> &str { todo!() }\n"
            "fn g(x: Vec<Vec<u8>>) {}\n"
        )
        names = _type_names(body)
        assert names.isdisjoint({"u32", "String", "str", "Vec", "u8"})

    def test_single_letter_generic_param_stays_filtered_when_wrapped(self) -> None:
        assert "T" not in _type_names("fn f<T: Bound>(x: &T) -> Box<T> { todo!() }\n")

    def test_macro_type_position_mints_nothing(self) -> None:
        # A macro in type position is not a type; the extractor returns None
        # for it and no edge is created. The macro is still a call site.
        body = "fn f(x: some_macro!(Foo)) {}\n"
        assert "Foo" not in _type_names(body)
        assert "some_macro" in _call_targets(body)


# ---------------------------------------------------------------------------
# Head extractor: shapes the C#-shaped default could not read
# ---------------------------------------------------------------------------


class TestRustHeadTypeIdentifier:
    """The extractor is exercised directly.

    The query now hands the enclosing type node to it at every position, so
    these are the shapes real captures deliver. Pinned here as well as at
    query level (``TestWrappedTypePositionCaptures``) so a regression is
    attributable: this half fails only if the unwrapping itself changes.
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


class TestWrappedTypeUseResolution:
    """End to end for the shapes in #2298: capture -> resolve -> edge.

    Two files per shape, one per position, each writing no ``use`` for its
    type so the resolver has to find it by the unique-owner index. That is
    the situation the issue describes: a borrowed, boxed or path-qualified
    type is the only evidence the defining file is used at all, and
    ``type_use`` being in ``REACHABILITY_USE_EDGE_TYPES`` makes that the
    difference between reachable and unreachable.
    """

    _SRC: ClassVar[dict[str, str]] = {
        "src/lib.rs": (
            "pub mod wrapped;\n"
            "pub mod ref_param;\n"
            "pub mod box_param;\n"
            "pub mod scoped_param;\n"
            "pub mod ref_return;\n"
            "pub mod box_return;\n"
            "pub mod scoped_return;\n"
        ),
        "src/wrapped.rs": (
            "pub struct BorrowedPayload;\n"
            "pub struct BoxedPayload;\n"
            "pub struct QualifiedError;\n"
            "pub struct BorrowedOut;\n"
            "pub struct BoxedOut;\n"
            "pub struct QualifiedOut;\n"
        ),
        # No import: the wrapped parameter is the only reference.
        "src/ref_param.rs": "pub fn take(x: &BorrowedPayload) {}\n",
        "src/box_param.rs": "pub fn take(x: Box<BoxedPayload>) {}\n",
        "src/scoped_param.rs": "pub fn take(e: std::io::QualifiedError) {}\n",
        # No import: the wrapped return type is the only reference.
        "src/ref_return.rs": "pub fn make() -> &'static BorrowedOut { todo!() }\n",
        "src/box_return.rs": "pub fn make() -> Box<BoxedOut> { todo!() }\n",
        "src/scoped_return.rs": "pub fn make() -> std::io::QualifiedOut { todo!() }\n",
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

    @pytest.mark.parametrize(
        ("consumer", "type_name"),
        [
            ("src/ref_param.rs", "BorrowedPayload"),
            ("src/box_param.rs", "BoxedPayload"),
            ("src/scoped_param.rs", "QualifiedError"),
            ("src/ref_return.rs", "BorrowedOut"),
            ("src/box_return.rs", "BoxedOut"),
            ("src/scoped_return.rs", "QualifiedOut"),
        ],
    )
    def test_wrapped_position_emits_a_type_use_edge(
        self, tmp_path: Path, consumer: str, type_name: str
    ) -> None:
        graph = self._build(tmp_path)
        data = graph.get_edge_data(consumer, "src/wrapped.rs") or {}
        assert data, f"{consumer}: no edge to the defining file"
        assert data.get("edge_type") == "type_use"
        assert type_name in data.get("type_uses", [])
        assert type_name in data.get("imported_names", [])

    def test_no_calls_edge_from_a_wrapped_type_position(self, tmp_path: Path) -> None:
        graph = self._build(tmp_path)
        declared = {
            "BorrowedPayload",
            "BoxedPayload",
            "QualifiedError",
            "BorrowedOut",
            "BoxedOut",
            "QualifiedOut",
        }
        bogus = [
            (u, v)
            for u, v, d in graph.edges(data=True)
            if d.get("edge_type") == "calls" and str(v).rsplit("::", 1)[-1] in declared
        ]
        assert bogus == [], f"a type was recorded as callable: {bogus}"

    def test_wrapped_type_only_use_is_not_an_unused_export(self, tmp_path: Path) -> None:
        graph = self._build(tmp_path)
        analyzer = DeadCodeAnalyzer(graph, repo_root=tmp_path)
        flagged = {
            f.symbol_name
            for f in analyzer.analyze().findings
            if f.kind == DeadCodeKind.UNUSED_EXPORT
        }
        # Every one of these is named only through a wrapped type position.
        assert flagged.isdisjoint(
            {
                "BorrowedPayload",
                "BoxedPayload",
                "QualifiedError",
                "BorrowedOut",
                "BoxedOut",
                "QualifiedOut",
            }
        )


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
