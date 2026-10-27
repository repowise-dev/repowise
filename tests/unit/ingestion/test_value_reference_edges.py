"""A method, function or class passed as a value, in Go, Rust, Kotlin, TS/JS and Python.

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

    def test_func_value_in_keyed_map_composite_literal(self, tmp_path: Path) -> None:
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
            "func Greet() {}\n"
            "\n"
            "func Setup() {\n"
            '\t_ = map[string]any{"greet": Greet, "handler": handlers.Index}\n'
            "}\n"
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "server.go::Greet", "references")
        assert not _inbound(graph, "server.go::Greet", "calls")
        assert _inbound(graph, "handlers/handlers.go::Index", "references")
        assert not _inbound(graph, "handlers/handlers.go::Index", "calls")

    def test_func_value_in_struct_composite_literal(self, tmp_path: Path) -> None:
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
            "type Config struct {\n"
            "    OnEvent func()\n"
            "    Handler func()\n"
            "}\n"
            "\n"
            "func Greet() {}\n"
            "\n"
            "func Setup() {\n"
            "\t_ = Config{OnEvent: Greet, Handler: handlers.Index}\n"
            "}\n"
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "server.go::Greet", "references")
        assert not _inbound(graph, "server.go::Greet", "calls")
        assert _inbound(graph, "handlers/handlers.go::Index", "references")
        assert not _inbound(graph, "handlers/handlers.go::Index", "calls")

    def test_func_value_in_slice_composite_literal(self, tmp_path: Path) -> None:
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
            "func Greet() {}\n"
            "\n"
            "func Setup() {\n"
            "\t_ = []func(){Greet, handlers.Index}\n"
            "}\n"
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "server.go::Greet", "references")
        assert not _inbound(graph, "server.go::Greet", "calls")
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
    """A bare identifier never reaches a method; a qualified name may.

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
    def test_languages_without_the_captures_gain_nothing(self, tmp_path: Path) -> None:
        """The pass is self-gating on the captures, not on a language list."""
        (tmp_path / "App.java").write_text(
            "class App {\n"
            "    static void handler() {}\n"
            "    static void setup() { register(handler); }\n"
            "}\n"
        )
        graph = _build(tmp_path)
        assert not _edges_of_type(graph, "references")


def _write(root: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        (root / name).write_text(body)


class TestTypeScriptValueReferences:
    """A function or class handed over by name: registered, passed, exported."""

    @pytest.mark.parametrize(
        ("body", "caller"),
        [
            ("export function setup() { register(handler); }\n", "setup"),
            ('export function setup(e: any) { e.on("x", handler); }\n', "setup"),
            ("export function setup() { setTimeout(handler, 10); }\n", "setup"),
            ("export function setup() { return { extensions: [handler] }; }\n", "setup"),
            ("export function setup() { return { onMessage: handler }; }\n", "setup"),
            ("export function setup() { return { handler }; }\n", "setup"),
            ("export function setup() { return handler; }\n", "setup"),
            ("export function setup() { let h; h = handler; return h; }\n", "setup"),
            ("export default handler;\n", "__module__"),
        ],
    )
    def test_value_positions_reference_an_imported_function(
        self, tmp_path: Path, body: str, caller: str
    ) -> None:
        _write(
            tmp_path,
            {
                "handler.ts": "export function handler() {}\n",
                "setup.ts": 'import { handler } from "./handler";\n' + body,
            },
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "handler.ts::handler", "references") == {f"setup.ts::{caller}"}
        assert not _inbound(graph, "handler.ts::handler", "calls")

    def test_default_export_registered_by_an_importer(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            {
                "ext.ts": "function safeguard() {}\nexport default safeguard;\n",
                "runner.ts": (
                    'import safeguard from "./ext";\n'
                    "export function build() { return { extensions: [safeguard] }; }\n"
                ),
            },
        )
        graph = _build(tmp_path)
        assert "runner.ts::build" in _inbound(graph, "ext.ts::safeguard", "references")

    def test_class_and_method_values(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            {
                "svc.ts": "export class FooService {}\n",
                "mod.ts": (
                    'import { FooService } from "./svc";\n'
                    "export class Panel {\n"
                    "  onClick() {}\n"
                    "  mount(el: any) { el.listen(this.onClick); }\n"
                    "}\n"
                    "export function providers() { return [FooService]; }\n"
                ),
            },
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "svc.ts::FooService", "references") == {"mod.ts::providers"}
        assert _inbound(graph, "mod.ts::Panel::onClick", "references") == {
            "mod.ts::Panel::mount"
        }

    @pytest.mark.parametrize(
        "body",
        [
            # A local of the same name hides the function.
            "export function setup() { const handler = 1; register(handler); }\n",
            # So does a parameter.
            "export function setup(handler: any) { register(handler); }\n",
            # A string names nothing.
            'export function setup() { register("handler"); }\n',
            # A property on an untyped object is not the method of that name.
            "export function setup(cfg: any) { register(cfg.handler); }\n",
        ],
    )
    def test_negatives_produce_no_reference(self, tmp_path: Path, body: str) -> None:
        _write(
            tmp_path,
            {
                "lib.ts": "export function handler() {}\nexport class Store {\n  handler() {}\n}\n",
                "setup.ts": 'import { handler } from "./lib";\n' + body,
            },
        )
        graph = _build(tmp_path)
        assert not _edges_of_type(graph, "references")

    def test_a_call_stays_a_call(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            {
                "handler.ts": "export function handler() { return 1; }\n",
                "setup.ts": (
                    'import { handler } from "./handler";\n'
                    "export function setup() { register(handler()); }\n"
                ),
            },
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "handler.ts::handler", "calls") == {"setup.ts::setup"}
        assert not _edges_of_type(graph, "references")


class TestJavaScriptValueReferences:
    @pytest.mark.parametrize(
        ("body", "caller"),
        [
            ("function setup() { app.use(handler); }\n", "setup"),
            ("function setup() { return [handler]; }\n", "setup"),
            ("module.exports.handler = handler;\n", "__module__"),
            ("function View() { return <button onClick={handler} />; }\n", "View"),
        ],
    )
    def test_value_positions_reference_a_same_file_function(
        self, tmp_path: Path, body: str, caller: str
    ) -> None:
        _write(tmp_path, {"app.jsx": "function handler() {}\n" + body})
        graph = _build(tmp_path)
        assert _inbound(graph, "app.jsx::handler", "references") == {f"app.jsx::{caller}"}

    def test_a_shadowing_parameter_produces_no_reference(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            {"app.js": "function handler() {}\nfunction setup(handler) { app.use(handler); }\n"},
        )
        graph = _build(tmp_path)
        assert not _edges_of_type(graph, "references")


class TestPythonValueReferences:
    @pytest.mark.parametrize(
        ("body", "caller"),
        [
            ("def setup(bus):\n    bus.subscribe(callback=on_message)\n", "setup"),
            ("def setup(xs):\n    return list(map(on_message, xs))\n", "setup"),
            ('def setup():\n    return {"msg": on_message}\n', "setup"),
            ("def setup():\n    return [on_message]\n", "setup"),
            ("def setup():\n    return (on_message,)\n", "setup"),
            ("def setup():\n    return on_message\n", "setup"),
            ("def setup():\n    h = on_message\n    return h\n", "setup"),
            ("def setup(bus):\n    bus.subscribe(handlers.on_message)\n", "setup"),
        ],
    )
    def test_value_positions_reference_an_imported_function(
        self, tmp_path: Path, body: str, caller: str
    ) -> None:
        _write(
            tmp_path,
            {
                "handlers.py": "def on_message(m):\n    return m\n",
                "setup.py": "import handlers\nfrom handlers import on_message\n\n" + body,
            },
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "handlers.py::on_message", "references") == {f"setup.py::{caller}"}
        assert not _inbound(graph, "handlers.py::on_message", "calls")

    def test_a_wildcard_import_still_reaches_its_names(self, tmp_path: Path) -> None:
        """The per-file name prefilter must not drop a name only ``*`` binds."""
        _write(
            tmp_path,
            {
                "handlers.py": "def on_message(m):\n    return m\n",
                "setup.py": "from handlers import *\n\ndef setup(bus):\n    bus.subscribe(on_message)\n",
            },
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "handlers.py::on_message", "references") == {"setup.py::setup"}

    def test_class_and_bound_method_values(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            {
                "app.py": (
                    "class NotFound(Exception):\n    pass\n\n"
                    "class Panel:\n"
                    "    def on_click(self):\n        pass\n\n"
                    "    def mount(self, el):\n        el.listen(self.on_click)\n\n"
                    "def setup(app):\n    app.register_error_handler(404, NotFound)\n"
                ),
            },
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "app.py::NotFound", "references") == {"app.py::setup"}
        assert _inbound(graph, "app.py::Panel::on_click", "references") == {"app.py::Panel::mount"}

    @pytest.mark.parametrize(
        "body",
        [
            "def setup(on_message):\n    register(on_message)\n",
            "def setup():\n    on_message = 1\n    register(on_message)\n",
            'def setup():\n    register("on_message")\n',
            "def setup(cfg):\n    register(cfg.on_message)\n",
        ],
    )
    def test_negatives_produce_no_reference(self, tmp_path: Path, body: str) -> None:
        _write(
            tmp_path,
            {
                "lib.py": (
                    "def on_message(m):\n    return m\n\n"
                    "class Store:\n    def on_message(self):\n        pass\n"
                ),
                "setup.py": "from lib import on_message\n\n" + body,
            },
        )
        graph = _build(tmp_path)
        assert not _edges_of_type(graph, "references")

    def test_a_call_stays_a_call(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            {
                "lib.py": "def on_message(m):\n    return m\n",
                "setup.py": (
                    "from lib import on_message\n\n"
                    "def setup():\n    register(on_message(1))\n"
                ),
            },
        )
        graph = _build(tmp_path)
        assert _inbound(graph, "lib.py::on_message", "calls") == {"setup.py::setup"}
        assert not _edges_of_type(graph, "references")
