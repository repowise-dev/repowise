"""A method or function passed as a value, in Go, Rust and Kotlin.

``list.map(Foo::bar)``, ``register(pkg.Handler)`` and ``Config { on_tick:
my_func }`` all name something callable and never call it. Go and Rust already
captured these shapes, but as ``@call.site`` with no argument list, so they
reached the graph as ordinary ``calls`` edges — a value reference
indistinguishable from an invocation, and an execution flow that steps through
a handler nothing has yet run. Java spells it ``Foo::bar`` and has the same
defect; it is untouched here.

Two halves are pinned here. The shapes must produce a ``references`` edge and
must **not** produce a ``calls`` edge; and the receiver decides what may be
named, because a bare identifier sits in a position a local also occupies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder


def _build(repo: Path):
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _edges_of_type(graph, kind: str) -> set[tuple[str, str]]:
    return {(u, v) for u, v, d in graph.edges(data=True) if d.get("edge_type") == kind}


def _inbound(graph, symbol_id: str, kind: str) -> set[str]:
    if not graph.has_node(symbol_id):
        return set()
    return {
        pred
        for pred in graph.predecessors(symbol_id)
        if graph[pred][symbol_id].get("edge_type") == kind
    }


class TestKotlinCallableReferences:
    def test_qualified_reference_reaches_a_method(self, tmp_path: Path) -> None:
        (tmp_path / "handlers.kt").write_text(
            "package app\n"
            "class Handlers {\n"
            "    fun onTick(n: Int): Int = n\n"
            "}\n"
            "class Runner {\n"
            "    fun run(h: Handlers, xs: List<Int>) {\n"
            "        xs.map(Handlers::onTick)\n"
            "    }\n"
            "}\n"
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "handlers.kt::Handlers::onTick", "references")
        assert not _inbound(graph, "handlers.kt::Handlers::onTick", "calls")

    def test_unqualified_reference_reaches_a_top_level_function(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pipeline.kt").write_text(
            "package app\n"
            "fun transform(n: Int): Int = n + 1\n"
            "fun run(xs: List<Int>) {\n"
            "    xs.map(::transform)\n"
            "}\n"
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "pipeline.kt::transform", "references")

    def test_plain_member_access_is_not_a_reference(self, tmp_path: Path) -> None:
        """``Foo.bar`` and ``Foo::bar`` are one node shape apart from the token.

        The grammar gives them the same tree, so the query matches ``::``
        literally. Without that, every qualified property read would mint an
        edge.
        """
        (tmp_path / "config.kt").write_text(
            "package app\n"
            "object Defaults {\n"
            "    fun timeout(): Int = 30\n"
            "}\n"
            "fun read(): Int = Defaults.timeout()\n"
        )
        graph = _build(tmp_path)
        assert not _edges_of_type(graph, "references")


class TestGoFunctionValues:
    @staticmethod
    def _layout(tmp_path: Path, setup_body: str) -> None:
        (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.21\n")
        (tmp_path / "handlers").mkdir()
        (tmp_path / "handlers" / "handlers.go").write_text(
            "package handlers\n\nfunc Index() {}\n"
        )
        (tmp_path / "server.go").write_text(
            "package app\n"
            "\n"
            'import "example.com/app/handlers"\n'
            "\n"
            "func Register(f func()) {}\n"
            "\n"
            "func Setup() {\n"
            f"{setup_body}"
            "}\n"
        )

    def test_qualified_func_value_in_argument_position(self, tmp_path: Path) -> None:
        self._layout(tmp_path, "\tRegister(handlers.Index)\n")
        graph = _build(tmp_path)
        assert _inbound(graph, "handlers/handlers.go::Index", "references")
        assert not _inbound(graph, "handlers/handlers.go::Index", "calls")

    def test_an_ordinary_qualified_call_is_still_a_call(self, tmp_path: Path) -> None:
        self._layout(tmp_path, "\thandlers.Index()\n")
        graph = _build(tmp_path)
        assert _inbound(graph, "handlers/handlers.go::Index", "calls")
        assert not _inbound(graph, "handlers/handlers.go::Index", "references")


class TestRustFunctionValues:
    def test_callback_argument_is_a_reference(self, tmp_path: Path) -> None:
        (tmp_path / "main.rs").write_text(
            "fn my_handler() {}\n"
            "fn register(f: fn()) {}\n"
            "fn setup() {\n"
            "    register(my_handler);\n"
            "}\n"
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "main.rs::my_handler", "references")
        assert not _inbound(graph, "main.rs::my_handler", "calls")

    def test_associated_function_as_a_callback_argument(self, tmp_path: Path) -> None:
        """``register(Foo::bar)`` — the shape a bare-identifier pattern misses.

        Rust spells "pass this method" with a path, so the pattern matching a
        lone identifier never sees the idiom the whole change is about.
        """
        (tmp_path / "main.rs").write_text(
            "struct Foo;\n"
            "impl Foo {\n"
            "    fn bar() {}\n"
            "}\n"
            "fn register(f: fn()) {}\n"
            "fn setup() {\n"
            "    register(Foo::bar);\n"
            "}\n"
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "main.rs::Foo::bar", "references")
        assert not _inbound(graph, "main.rs::Foo::bar", "calls")

    def test_struct_field_initialiser_is_a_reference(self, tmp_path: Path) -> None:
        (tmp_path / "main.rs").write_text(
            "fn on_tick() {}\n"
            "struct Config { tick: fn() }\n"
            "fn make() -> Config {\n"
            "    Config { tick: on_tick }\n"
            "}\n"
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "main.rs::on_tick", "references")


class TestRustMacroInvocations:
    """``foo!(..)`` names a ``macro_rules! foo``; a macro is not a function.

    Hand-read across ripgrep, serde and bevy, four of the eight wrong rows in
    the rust precision cell were this one shape, and every one had the *right*
    target: the resolver found the correct ``macro_rules!`` at the correct site
    and filed it under the wrong edge type.

    So the site keeps its resolution and changes only its type. That is a
    deliberate divergence from the value-reference shapes above, which route
    through the ``@reference.name`` capture and so inherit
    ``_add_reference_edges``'s 0.85 confidence floor. That floor was bought by
    a *bare identifier* in argument position, where an ordinary local looks
    exactly like a function name; ``foo!`` is unambiguous. Applying it here
    would delete every macro edge the repo-wide unique-name tier answers -
    160 of 669 across the three repositories - rather than retype them,
    discarding a real dependency to move a precision number.
    """

    def test_a_macro_invocation_is_a_reference_not_a_call(self, tmp_path: Path) -> None:
        (tmp_path / "main.rs").write_text(
            "macro_rules! shout { () => {} }\n"
            "fn run() {\n"
            "    shout!();\n"
            "}\n"
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "main.rs::shout", "references")
        assert not _inbound(graph, "main.rs::shout", "calls")

    def test_an_ordinary_call_beside_it_is_still_a_call(self, tmp_path: Path) -> None:
        """The macro node type decides it, not the absence of an argument capture.

        Reading that absence as "not an invocation" would reclassify every
        ordinary call the argument capture happens to miss.
        """
        (tmp_path / "main.rs").write_text(
            "macro_rules! shout { () => {} }\n"
            "fn helper() {}\n"
            "fn run() {\n"
            "    shout!();\n"
            "    helper();\n"
            "}\n"
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "main.rs::helper", "calls")
        assert _inbound(graph, "main.rs::shout", "references")

    def test_a_macro_edge_below_the_reference_floor_survives(
        self, tmp_path: Path
    ) -> None:
        """The retype must not inherit ``_add_reference_edges``'s 0.85 floor.

        A macro defined in another file resolves on a lower tier than a
        same-file one. That population is the majority of macro invocations in
        the corpus, so a floor here would read as a removal rather than a
        retype.
        """
        (tmp_path / "mac.rs").write_text("macro_rules! only_here { () => {} }\n")
        (tmp_path / "main.rs").write_text("fn run() {\n    only_here!();\n}\n")
        graph = _build(tmp_path)
        assert _inbound(graph, "mac.rs::only_here", "references")

    def test_a_reclassified_edge_carries_no_call_lines(self, tmp_path: Path) -> None:
        """``call_lines`` is what a call site contributes, and this is not one.

        Every other ``references`` producer omits it, and the persistence layer
        writes the attribute through unconditionally, so setting it here would
        put a shape in the store that no consumer has ever seen.
        """
        (tmp_path / "main.rs").write_text(
            "macro_rules! shout { () => {} }\nfn run() {\n    shout!();\n}\n"
        )
        graph = _build(tmp_path)
        assert "call_lines" not in graph["main.rs::run"]["main.rs::shout"]

    def test_other_languages_keep_their_call_edges(self, tmp_path: Path) -> None:
        """The node-type list is per language and empty everywhere but rust."""
        (tmp_path / "app.py").write_text(
            "def helper():\n    return 1\n\n\ndef run():\n    return helper()\n"
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "app.py::helper", "calls")
        assert not _edges_of_type(graph, "references")


class TestReceiverDecidesWhatMayBeNamed:
    """A bare identifier admits only functions; a qualified name admits methods.

    This is what keeps the C/C++ rule that bought #1602's precision — there a
    plain name resolving to a method is a collision, since naming a member
    needs ``&Class::method`` — while letting ``Foo::bar`` reach the method it
    plainly names.
    """

    def test_bare_rust_identifier_does_not_reach_a_method(self, tmp_path: Path) -> None:
        (tmp_path / "main.rs").write_text(
            "struct Store;\n"
            "impl Store {\n"
            "    fn value(&self) -> i32 { 1 }\n"
            "}\n"
            "fn take(x: i32) {}\n"
            "fn run(s: &Store) {\n"
            "    let value = s.value();\n"
            "    take(value);\n"
            "}\n"
        )
        graph = _build(tmp_path)
        assert not _inbound(graph, "main.rs::Store::value", "references")

    def test_qualified_kotlin_reference_does_reach_a_method(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "app.kt").write_text(
            "package app\n"
            "class Store {\n"
            "    fun value(): Int = 1\n"
            "}\n"
            "class Runner {\n"
            "    fun run(xs: List<Store>) {\n"
            "        xs.map(Store::value)\n"
            "    }\n"
            "}\n"
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "app.kt::Store::value", "references")


class TestKotlinCeilings:
    """Shapes `::` reaches that this deliberately does not turn into an edge."""

    def test_a_property_reference_produces_no_edge(self, tmp_path: Path) -> None:
        """`Foo::name` is a handle on a property, and a property is not called.

        Nothing filters it at capture; the symbol kind settles it at
        resolution, which is where the same rule already serves every other
        shape.
        """
        (tmp_path / "app.kt").write_text(
            "package app\n"
            "class Foo {\n"
            "    val name: String = \"\"\n"
            "}\n"
            "fun read(xs: List<Foo>) {\n"
            "    xs.map(Foo::name)\n"
            "}\n"
        )
        graph = _build(tmp_path)
        assert not _edges_of_type(graph, "references")

    def test_a_nested_qualifier_is_not_captured(self, tmp_path: Path) -> None:
        """`A.B::c` puts a navigation_expression in the receiver slot.

        A stated ceiling rather than a defect: it had no capture before either,
        and closing it means matching a receiver of arbitrary depth.
        """
        (tmp_path / "app.kt").write_text(
            "package app\n"
            "object A {\n"
            "    object B {\n"
            "        fun c(): Int = 1\n"
            "    }\n"
            "}\n"
            "fun read() {\n"
            "    listOf(1).map(A.B::c)\n"
            "}\n"
        )
        graph = _build(tmp_path)
        assert not _edges_of_type(graph, "references")


class TestControls:
    @pytest.mark.parametrize(
        ("name", "body"),
        [
            (
                "app.py",
                "def handler():\n    pass\n\ndef setup():\n    register(handler)\n",
            ),
            (
                "app.ts",
                "function handler() {}\nfunction setup() { register(handler); }\n",
            ),
        ],
    )
    def test_languages_without_the_captures_gain_nothing(
        self, tmp_path: Path, name: str, body: str
    ) -> None:
        """The pass is self-gating on the captures, not on a language list."""
        (tmp_path / name).write_text(body)
        graph = _build(tmp_path)
        assert not _edges_of_type(graph, "references")
