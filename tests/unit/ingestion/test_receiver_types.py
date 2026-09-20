"""What the receiver-type scan reads off a function body, and what it refuses.

The refusals matter more than the matches. Every wrong type this scan returns
is a wrong edge the resolver's validator has to catch.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.languages.receiver_types import (
    IMPLICIT_FIELD_LANGUAGES,
    RECEIVER_TYPE_LANGUAGES,
    scan_declarations,
    types_by_class,
    types_in_span,
    unwrapped_names_in_span,
)


def declared_types(body: str, language: str) -> dict[str, str | None]:
    """The two halves composed over a whole body, which is what a caller sees."""
    return types_in_span(scan_declarations(body, language), 1, 10_000)


def fields(source: str, language: str, methods: list[tuple[int, int]]) -> dict[str, str | None]:
    """One class spanning the whole text, with *methods* as its bodies."""
    lines = source.count("\n") + 1
    return types_by_class(
        scan_declarations(source, language), {"f.cs::C": (1, lines)}, methods
    ).get("f.cs::C", {})


class TestJavaShapes:
    def test_parameter(self) -> None:
        body = "void run(CacheContext context, int n) { context.original(); }"
        assert declared_types(body, "java")["context"] == "CacheContext"

    def test_local_with_initialiser(self) -> None:
        body = "void run() { LocalCache cache = build(); cache.put(k); }"
        assert declared_types(body, "java")["cache"] == "LocalCache"

    def test_bare_local(self) -> None:
        assert declared_types("void run() { TimerWheel wheel; }", "java")["wheel"] == "TimerWheel"

    def test_var_from_constructor(self) -> None:
        body = "void run() { var buffer = new BoundedBuffer(); }"
        assert declared_types(body, "java")["buffer"] == "BoundedBuffer"

    def test_enhanced_for(self) -> None:
        body = "void run() { for (Node n : nodes) { n.getValue(); } }"
        assert declared_types(body, "java")["n"] == "Node"

    def test_catch_binding(self) -> None:
        body = "void run() { try { go(); } catch (LoadException e) { e.report(); } }"
        assert declared_types(body, "java")["e"] == "LoadException"

    def test_type_arguments_are_stripped(self) -> None:
        body = "void run(AsyncCache<Int, Int> cache) { }"
        assert declared_types(body, "java")["cache"] == "AsyncCache"

    def test_nested_type_arguments(self) -> None:
        body = "void run(Registry<Map<String, Int>> registry) { }"
        assert declared_types(body, "java")["registry"] == "Registry"

    def test_type_arguments_nested_three_deep_are_a_stated_ceiling(self) -> None:
        """Two levels is what the pattern balances; a third yields no type.

        Deliberate. A deeper group would need a real bracket matcher, and
        failing to type a name costs an edge, never a wrong one.
        """
        body = "void run(Registry<Map<String, List<Int>>> registry) { }"
        assert "registry" not in declared_types(body, "java")

    def test_a_lowercase_package_qualifier_is_a_stated_ceiling(self) -> None:
        """A fully-qualified declaration is not matched, and that is measured.

        Admitting one means the type may start lowercase, which costs ~40% of
        the scan's time on a Java-heavy repo and bought six edges on caffeine.
        An uppercase qualifier — C#'s ``Ns.Type x`` — is still matched.
        """
        body = "void run(com.example.cache.Ticker ticker) { }"
        assert "ticker" not in declared_types(body, "java")


class TestCsharpShapes:
    def test_parameter(self) -> None:
        body = "public void Invoke(IOcelotLoggerFactory factory) { }"
        assert declared_types(body, "csharp")["factory"] == "IOcelotLoggerFactory"

    def test_var_from_constructor(self) -> None:
        body = "public void Go() { var builder = new DownstreamRouteBuilder(); }"
        assert declared_types(body, "csharp")["builder"] == "DownstreamRouteBuilder"

    def test_namespace_qualified_local(self) -> None:
        body = "public void Go() { Ocelot.Configuration.Route route = null; }"
        assert declared_types(body, "csharp")["route"] == "Route"

    def test_pattern_match_binding(self) -> None:
        body = "public void Go() { if (thing is Placeholder p) { p.Name(); } }"
        assert declared_types(body, "csharp")["p"] == "Placeholder"


class TestPythonShapes:
    """Python annotates after the name and constructs without a keyword.

    Neither shape is ``T name``, so nothing the C family matches applies here.
    """

    def test_annotated_parameter(self) -> None:
        body = "def run(self, crawler: Crawler):\n    crawler.stop()"
        assert declared_types(body, "python")["crawler"] == "Crawler"

    def test_annotated_assignment(self) -> None:
        body = "def run():\n    record: LogRecord = caplog.records[0]"
        assert declared_types(body, "python")["record"] == "LogRecord"

    def test_construction(self) -> None:
        body = "def run():\n    graph = DependencyGraph()\n    graph.add_arc(x)"
        assert declared_types(body, "python")["graph"] == "DependencyGraph"

    def test_a_parameter_in_a_multi_line_signature(self) -> None:
        body = "def run(\n    self,\n    jar: CookieJar,\n): ..."
        assert declared_types(body, "python")["jar"] == "CookieJar"


class TestPythonRefusals:
    """Prose is the false-positive source the C-family shape never had."""

    def test_a_docstring_is_not_a_declaration(self) -> None:
        """``context: The caller`` reads as an annotation until docstrings go."""
        body = '''def run():
    """Do the thing.

    Args:
        context: Crawler that owns this run.
    """
    context.stop()'''
        assert "context" not in declared_types(body, "python")

    def test_a_hash_comment_is_not_a_declaration(self) -> None:
        body = "def run():\n    # loader: SpiderLoader once lived here\n    pass"
        assert declared_types(body, "python") == {}

    def test_a_generic_annotation_is_refused(self) -> None:
        """``x: Optional[Crawler]`` is an Optional, not a Crawler.

        Unwrapping the payload would type ``x`` as something it provably is
        not, and the validator would happily accept the result.
        """
        body = "def run(a: Optional[Crawler], b: list[Spider]):\n    a.stop()"
        types = declared_types(body, "python")
        assert "a" not in types
        assert "b" not in types

    def test_a_factory_call_is_not_a_construction(self) -> None:
        """``x = Foo.bar()`` calls a class; it does not build a ``Foo``."""
        body = "def run():\n    result = GroupResult.restore(id)\n    result.get()"
        assert "result" not in declared_types(body, "python")

    def test_a_keyword_argument_is_not_a_construction(self) -> None:
        """``f(logger=Emitter())`` binds a parameter of ``f``, not a local.

        Read as a declaration it answers for a ``logger`` that came from
        somewhere else, which is a wrong edge rather than a missed one. The C
        family never had this shape because its equivalent needs ``var``.
        """
        body = "def run():\n    logger = get_logger()\n    dispatch(logger=Emitter())"
        assert declared_types(body, "python") == {}

    def test_a_lowercase_callee_is_not_a_construction(self) -> None:
        body = "def run():\n    spider = build_spider()\n    spider.crawl()"
        assert declared_types(body, "python") == {}

    def test_two_types_for_one_name_yields_neither(self) -> None:
        body = "def run():\n    thing = Crawler()\n    thing = SpiderLoader()"
        assert declared_types(body, "python") == {"thing": None}

    def test_python_never_reaches_class_scope(self) -> None:
        """A Python field is written ``self.foo``, so no bare receiver names one.

        The declaration is still found — it is the resolver that declines to
        ask class scope for a language whose fields are always qualified.
        """
        assert "python" not in IMPLICIT_FIELD_LANGUAGES


class TestRefusals:
    def test_a_member_type_of_a_builtin_is_refused(self) -> None:
        """``Map.Entry`` bare-names to ``Entry``, which some repo class has.

        Discarding a qualifier is right when it is a package and wrong when it
        is a type. A builtin head is the case that can be told apart, and it
        produced real wrong edges before this rule existed.
        """
        body = "void run() { Map.Entry<Object, Object> entry = it.next(); }"
        assert "entry" not in declared_types(body, "java")

    def test_builtin_types_are_refused(self) -> None:
        body = "void run(String name, Object value) { }"
        assert declared_types(body, "java") == {}

    def test_single_letter_type_parameter_is_refused(self) -> None:
        body = "<T> void run(T item) { item.hash(); }"
        assert "item" not in declared_types(body, "java")

    def test_two_types_for_one_name_yields_neither(self) -> None:
        """And the name stays in the scope, mapped to nothing.

        A caller has to tell "this scope never mentions the name" from "this
        scope mentions it and has no answer", because only the first may fall
        through to a wider scope. Both types here are deliberately repo-shaped:
        with builtins this case never reaches the conflict at all.
        """
        body = "void run() { CacheLoader source = a(); if (x) { NodeFactory source = b(); } }"
        assert declared_types(body, "java") == {"source": None}

    def test_a_cast_is_not_a_declaration(self) -> None:
        assert declared_types("void run() { var n = (Node) raw; }", "java") == {}

    def test_a_qualified_constant_argument_is_not_a_declaration(self) -> None:
        assert declared_types("void run() { call(Config.DEFAULT, other); }", "java") == {}

    def test_a_declaration_in_a_line_comment_is_ignored(self) -> None:
        assert declared_types("void run() { // a Node node; once lived here\n }", "java") == {}

    def test_an_unregistered_language_yields_nothing(self) -> None:
        # Ruby rather than Kotlin: Kotlin registered its shapes and now types
        # `w: TimerWheel`. Ruby is one of the languages P8 left unattempted,
        # and it annotates nothing to begin with.
        body = "def run(w)\n  w.tick\nend"
        assert declared_types(body, "ruby") == {}


class TestKotlinShapes:
    """Kotlin annotates after the name, and one shape reaches almost all of it.

    A typed `val`, a typed `var`, a function parameter and a primary
    constructor's property are all `name: Type`, which is why there is one
    annotation pattern here rather than four. Measured over ktor and exposed,
    it is 6,291 typed `val`s, 1,242 `var`s and roughly 8,800 parameters,
    against 1,183 for the construction shape.
    """

    def test_a_typed_val(self) -> None:
        body = "fun run() {\n    val ctx: PipelineContext = call.context\n}"
        assert declared_types(body, "kotlin")["ctx"] == "PipelineContext"

    def test_a_typed_var(self) -> None:
        body = "fun run() {\n    var engine: HttpEngine = make()\n}"
        assert declared_types(body, "kotlin")["engine"] == "HttpEngine"

    def test_a_parameter(self) -> None:
        body = "fun handle(call: ApplicationCall, n: Int) { call.respond() }"
        assert declared_types(body, "kotlin")["call"] == "ApplicationCall"

    def test_a_parameter_in_a_multi_line_signature(self) -> None:
        body = "fun handle(\n    request: HttpRequest,\n    n: Int,\n) { }"
        assert declared_types(body, "kotlin")["request"] == "HttpRequest"

    def test_a_primary_constructor_property(self) -> None:
        body = "class Server(private val config: ServerConfig) { }"
        assert declared_types(body, "kotlin")["config"] == "ServerConfig"

    def test_a_construction(self) -> None:
        body = "fun run() {\n    val registry = PluginRegistry()\n}"
        assert declared_types(body, "kotlin")["registry"] == "PluginRegistry"

    def test_a_nullable_type_keeps_its_type(self) -> None:
        """``Foo?`` is still a ``Foo`` at the call site, and it is 15% of
        ktor's typed declarations. Refusing it refuses a correct answer."""
        body = "fun run() {\n    val timeout: Duration? = null\n}"
        assert declared_types(body, "kotlin")["timeout"] == "Duration"

    def test_a_generic_keeps_its_outer_type(self) -> None:
        """The reverse of Python's rule, for the same reason spelled
        backwards: Kotlin's ``Column<T>`` *is* a ``Column``, where Python's
        ``Optional[T]`` is not a ``T``."""
        body = "fun run(column: Column<String>) { }"
        assert declared_types(body, "kotlin")["column"] == "Column"


class TestKotlinRefusals:
    def test_a_builtin_generic_is_refused(self) -> None:
        """``List<Header>`` bares to ``List``, which the language spec refuses
        — and must, because the value is a List and not a Header."""
        body = "fun run() {\n    val items: List<Header> = call.headers\n}"
        assert "items" not in declared_types(body, "kotlin")

    def test_a_named_argument_is_not_a_declaration(self) -> None:
        """Kotlin spells a named argument with ``=``, which is why the
        construction shape is anchored to ``val``/``var`` rather than to the
        start of a statement as Python's is."""
        body = "fun run() {\n    dispatch(logger = Emitter())\n}"
        assert declared_types(body, "kotlin") == {}

    def test_a_factory_call_is_not_a_construction(self) -> None:
        """``Store.of(call)`` is a call on a class. Reading the qualifier's
        last segment types ``cache`` as ``of``, which it briefly did."""
        body = "fun run() {\n    val cache = Store.of(call)\n}"
        assert "cache" not in declared_types(body, "kotlin")

    def test_an_annotation_use_site_target_is_not_a_declaration(self) -> None:
        """``@get:JvmName("x")`` reads as ``get: JvmName``. ``(`` is kept out
        of the closer set precisely to refuse it."""
        body = 'class C {\n    @get:JvmName("x")\n    fun f() { }\n}'
        assert declared_types(body, "kotlin") == {}

    def test_a_builtin_type_is_refused(self) -> None:
        assert declared_types("fun run(name: String, n: Int) { }", "kotlin") == {}

    def test_a_declaration_in_a_line_comment_is_ignored(self) -> None:
        body = "fun run() { // a val node: Node once lived here\n }"
        assert declared_types(body, "kotlin") == {}

    def test_a_supertype_clause_is_not_a_declaration(self) -> None:
        """Kotlin spells inheritance with the same colon. The name group being
        lowercase-anchored is all that separates the two."""
        assert declared_types("class Server : BaseServer() { }", "kotlin") == {}

    def test_kotlin_reaches_class_scope(self) -> None:
        """Unlike Python's, a Kotlin property is named with no qualifier, so a
        class-scope declaration can answer for a bare receiver. It earned 56 of
        ktor's 1,349 gained edges — small, and measured rather than assumed."""
        assert "kotlin" in IMPLICIT_FIELD_LANGUAGES

    def test_a_class_scope_property_is_a_field(self) -> None:
        source = (
            "class C {\n    private val logger: Logger = make()\n"
            "    fun f() {\n        logger.debug()\n    }\n}"
        )
        assert fields(source, "kotlin", [(3, 5)])["logger"] == "Logger"

    def test_a_local_is_not_a_field(self) -> None:
        source = "class C {\n    fun f() {\n        val local: Logger = make()\n    }\n}"
        assert "local" not in fields(source, "kotlin", [(2, 4)])


    def test_a_chained_construction_types_nothing(self) -> None:
        """``val x = Builder().build()`` makes ``x`` whatever ``build()``
        returns, so typing it as a ``Builder`` is a wrong answer rather than a
        missing one. ``_PY_CONSTRUCTED`` still has this shape; moving Python is
        its own measured change."""
        body = "fun f() {\n    val x = Builder().build()\n}"
        assert declared_types(body, "kotlin") == {}

    def test_a_nested_argument_chain_is_a_stated_ceiling(self) -> None:
        """``[^()]*`` cannot cross a nested call, so this one is still typed.
        The same direction of error as before the guard, not a new one."""
        body = "fun f() {\n    val x = Builder(g(1)).build()\n}"
        assert declared_types(body, "kotlin")["x"] == "Builder"

    def test_a_kdoc_block_comment_is_not_a_declaration(self) -> None:
        """KDoc writes ``@param connection: Store``, which is exactly Kotlin's
        declaration shape -- unlike javadoc's ``@param connection the Store``,
        which is not the C family's. Kotlin needs a block strip where Java does
        not, and this fabricated a type from prose before it had one."""
        body = "/**\n * @param connection: Store\n */\nfun run() { }"
        assert declared_types(body, "kotlin") == {}

    def test_the_java_scan_does_not_need_the_block_strip(self) -> None:
        """The asymmetry is in the shapes, not the languages. Measured on the
        same text, so the Kotlin-only table is a decision rather than an
        oversight."""
        body = "/**\n * @param connection the Store connection\n */\nvoid run() { }"
        assert declared_types(body, "java") == {}


class TestKotlinDelegation:
    def test_a_delegated_property_is_typed(self) -> None:
        """``by`` closes a declaration as well as punctuation does, and without
        it the whole ``by lazy`` idiom types nothing."""
        body = "fun f() {\n    val cache: Store by lazy { make() }\n}"
        assert declared_types(body, "kotlin")["cache"] == "Store"


class TestKotlinFieldScope:
    def test_a_constructor_parameter_with_a_default_is_not_a_field(self) -> None:
        """It closes on ``=`` at class scope exactly as a property does, and it
        is not a property at all -- no ``val``/``var``, so no method body can
        name it. The captured keyword is what tells the two apart."""
        source = (
            "class C(port: Int, timeout: Duration = Duration.seconds(5)) {\n"
            "    fun run() { timeout.toString() }\n}"
        )
        assert "timeout" not in fields(source, "kotlin", [(2, 2)])

    def test_a_constructor_property_with_a_default_is_still_read(self) -> None:
        """The guard must not take the real property beside it."""
        source = (
            "class C {\n    private var timeout: Duration = Duration.seconds(5)\n"
            "    fun run() { timeout.toString() }\n}"
        )
        assert fields(source, "kotlin", [(3, 3)])["timeout"] == "Duration"

    def test_a_parameter_is_never_a_field(self) -> None:
        """A `fun` parameter carries no keyword either, so class scope drops it
        whatever punctuation follows."""
        source = "class C {\n    fun run(timeout: Duration = d) { }\n}"
        assert fields(source, "kotlin", []) == {}



class TestSwiftShapes:
    """Swift annotates after the name, and one shape reaches 88% of it.

    Measured over swift-nio and Alamofire: 4,001 single-name parameters, 2,876
    stored properties, 2,104 `var`s, 1,356 underscore-label parameters, 1,304
    `let`s and 736 two-name-label parameters are all `name: Type`.
    """

    def test_a_typed_let(self) -> None:
        body = "func run() {\n    let ctx: ChannelContext = loop.context\n}"
        assert declared_types(body, "swift")["ctx"] == "ChannelContext"

    def test_a_typed_var(self) -> None:
        body = "func run() {\n    var channel: ServerChannel = make()\n}"
        assert declared_types(body, "swift")["channel"] == "ServerChannel"

    def test_a_plain_parameter(self) -> None:
        body = "func handle(request: HTTPRequest) { request.body() }"
        assert declared_types(body, "swift")["request"] == "HTTPRequest"

    def test_an_underscore_label_names_the_second_identifier(self) -> None:
        body = "func handle(_ request: HTTPRequest) { request.body() }"
        assert declared_types(body, "swift")["request"] == "HTTPRequest"

    def test_a_two_name_label_names_the_second_identifier(self) -> None:
        """The declared name is `loop`, not `on`. A pattern anchored at `(` or
        `,` takes the label and is wrong 736 times over the two repos; taking
        the identifier adjacent to the colon is right in all three spellings
        with no branch on any of them."""
        body = "func handle(on loop: EventLoop) { loop.execute() }"
        types = declared_types(body, "swift")
        assert types["loop"] == "EventLoop"
        assert "on" not in types

    def test_a_construction(self) -> None:
        body = "func run() {\n    let body = ResponseBody(request)\n}"
        assert declared_types(body, "swift")["body"] == "ResponseBody"

    def test_an_opaque_type_keeps_its_type(self) -> None:
        """`some Foo` is a `Foo` at the call site, as `any Foo` is."""
        body = "func run() {\n    let opaque: some Renderer = make()\n}"
        assert declared_types(body, "swift")["opaque"] == "Renderer"

    def test_an_optional_keeps_its_type(self) -> None:
        body = "func run() {\n    var channel: ServerChannel? = nil\n}"
        assert declared_types(body, "swift")["channel"] == "ServerChannel"

    def test_an_implicitly_unwrapped_optional_keeps_its_type(self) -> None:
        body = "func run() {\n    let session: Session! = current\n}"
        assert declared_types(body, "swift")["session"] == "Session"


class TestSwiftRefusals:
    def test_an_array_types_nothing(self) -> None:
        """The value is an Array of Header, not a Header. The type token starts
        with `[`, so it matches nothing rather than matching wrongly."""
        body = "func run() {\n    let items: [Header] = request.headers\n}"
        assert "items" not in declared_types(body, "swift")

    def test_a_dictionary_types_nothing(self) -> None:
        """`]` is kept out of the closer set precisely so the inner `k: V` of a
        dictionary type cannot close on it."""
        body = "func run() {\n    let lookup: [String: Header] = request.map\n}"
        assert declared_types(body, "swift") == {}

    def test_a_factory_call_is_not_a_construction(self) -> None:
        body = "func run() {\n    let cache = Store.shared(request)\n}"
        assert "cache" not in declared_types(body, "swift")

    def test_a_chained_construction_types_nothing(self) -> None:
        body = "func run() {\n    let x = Builder().build()\n}"
        assert declared_types(body, "swift") == {}

    def test_a_doc_comment_parameter_is_not_a_declaration(self) -> None:
        """swift-nio carries 18,722 `///` lines and 331 `- Parameter Name:`
        ones, which read exactly like `name: Type`. `///` starts with `//`, so
        one line-comment pattern covers them; Swift needs no block strip, since
        genuine `/* */` runs are 15 in swift-nio and 2 in Alamofire."""
        body = "/// - Parameter Handler: the RequestHandler to use\nfunc run() { }"
        assert declared_types(body, "swift") == {}

    def test_an_initialiser_parameter_is_not_a_field(self) -> None:
        """It sits at type scope and closes on `=` exactly as a stored property
        does, and it is a parameter. The captured keyword is what refuses it."""
        source = (
            "class C {\n    init(timeout: Duration = .seconds(5)) { }\n"
            "    func run() { timeout.tick() }\n}"
        )
        assert "timeout" not in fields(source, "swift", [(2, 2), (3, 3)])

    def test_a_stored_property_is_a_field(self) -> None:
        source = (
            "class C {\n    private let logger: Logger = Logger()\n"
            "    func run() {\n        logger.debug()\n    }\n}"
        )
        assert fields(source, "swift", [(3, 5)])["logger"] == "Logger"


class TestGoShapes:
    """Go writes ``name T``, which is the reverse of every other shape here.

    The receiver a method is declared on is the largest share of the reachable
    population — 56.5% over five Go repos — and it needs no scope of its own,
    because a function symbol's span starts at its ``func`` line.
    """

    def test_a_methods_own_receiver(self) -> None:
        body = "func (s *Server) handle() { s.route() }"
        assert declared_types(body, "go")["s"] == "Server"

    def test_a_value_receiver(self) -> None:
        body = "func (c Config) apply() { c.merge() }"
        assert declared_types(body, "go")["c"] == "Config"

    def test_a_parameter(self) -> None:
        body = "func run(d *Detector, n int) { d.Detect() }"
        assert declared_types(body, "go")["d"] == "Detector"

    def test_a_named_return(self) -> None:
        body = "func build() (out *Report, err error) { out.Write() }"
        assert declared_types(body, "go")["out"] == "Report"

    def test_a_composite_literal(self) -> None:
        assert declared_types("func f() { r := Rule{} ; r.Match() }", "go")["r"] == "Rule"

    def test_an_addressed_composite_literal(self) -> None:
        assert declared_types("func f() { r := &Rule{} }", "go")["r"] == "Rule"

    def test_a_qualified_composite_literal_keeps_the_bare_name(self) -> None:
        assert declared_types("func f() { a := config.Allowlist{} }", "go")["a"] == "Allowlist"

    def test_a_type_assertion(self) -> None:
        assert declared_types("func f() { w, ok := r.(*Writer) }", "go")["w"] == "Writer"

    def test_a_var_declaration(self) -> None:
        assert declared_types("func f() { var vc ViperConfig }", "go")["vc"] == "ViperConfig"

    def test_an_unexported_type_is_admitted(self) -> None:
        """A private method hangs off an unexported type, so refusing one on
        case would refuse most of what this mechanism exists to reach."""
        assert declared_types("func (s *startEnd) add() { }", "go")["s"] == "startEnd"

    def test_a_predeclared_type_is_refused(self) -> None:
        """Admitting a lowercase type is only safe because these are dropped."""
        assert declared_types("func f(name string, n int) { }", "go") == {}

    def test_a_constructor_call_names_no_type(self) -> None:
        """``x := NewFoo()`` types ``x`` only via the callee's return type,
        which is a second lookup this mechanism does not do."""
        assert declared_types("func f() { d := NewDetector() }", "go") == {}

    def test_a_slice_literal_does_not_type_its_element(self) -> None:
        """``x`` is a slice of Rule, not a Rule, so typing it would be wrong."""
        assert declared_types("func f() { rs := []Rule{} }", "go") == {}

    def test_a_map_literal_does_not_type_its_value(self) -> None:
        assert declared_types("func f() { m := map[string]Rule{} }", "go") == {}

    def test_a_multiplication_is_not_a_pointer_declaration(self) -> None:
        """gofmt spaces a binary operator on both sides and binds a pointer
        type tight, which is the whole of what separates these two."""
        assert declared_types("func f() { n := total(count * size) }", "go") == {}

    def test_a_declaration_in_a_line_comment_is_ignored(self) -> None:
        assert declared_types("func f() { // a *Rule rule once lived here\n }", "go") == {}

    def test_two_types_for_one_name_yields_neither(self) -> None:
        body = "func f(r *Rule) { r := Config{} }"
        assert declared_types(body, "go")["r"] is None


class TestClassScope:
    """What counts as a field, given that a class span contains every method.

    The refusals are again the load-bearing half: a local read as a field
    answers for every call in the class rather than for one body.
    """

    def test_a_private_field(self) -> None:
        source = "class C {\nprivate readonly IRouteCreator _creator;\n}"
        assert fields(source, "csharp", [])["_creator"] == "IRouteCreator"

    def test_a_java_field_without_the_underscore_convention(self) -> None:
        source = "class C {\nprivate final CacheLoader loader;\n}"
        assert fields(source, "java", [])["loader"] == "CacheLoader"

    def test_a_local_inside_a_method_is_not_a_field(self) -> None:
        source = "class C {\nvoid run() {\nCacheLoader loader = a();\n}\n}"
        assert fields(source, "java", [(2, 4)]) == {}

    def test_a_parameter_at_class_scope_is_not_a_field(self) -> None:
        """Some constructors and static methods are extracted as no symbol at
        all, so their parameter lists sit at class scope with only the closing
        punctuation to tell them apart from a field."""
        source = "class C {\npublic C(ITestOutputHelper output, IRouteCreator maker) { }\n}"
        assert fields(source, "csharp", []) == {}

    def test_a_field_with_an_initialiser(self) -> None:
        source = "class C {\nstatic final NodeFactory factory = build();\n}"
        assert fields(source, "java", [])["factory"] == "NodeFactory"

    def test_an_inner_class_field_answers_for_the_inner_class(self) -> None:
        source = "class C {\nprivate CacheLoader outer;\nclass D {\nprivate NodeFactory inner;\n}\n}"
        by_class = types_by_class(
            scan_declarations(source, "java"),
            {"f.java::C": (1, 6), "f.java::C::D": (3, 5)},
            [],
        )
        assert by_class["f.java::C"] == {"outer": "CacheLoader"}
        assert by_class["f.java::C::D"] == {"inner": "NodeFactory"}

    def test_a_var_local_is_never_a_field(self) -> None:
        source = "class C {\nvar maker = new RouteCreator();\n}"
        assert fields(source, "csharp", []) == {}

    def test_two_types_for_one_field_name_yields_neither(self) -> None:
        source = "class C {\nprivate CacheLoader thing;\nprivate NodeFactory thing;\n}"
        assert fields(source, "java", []) == {"thing": None}

    def test_a_file_with_no_class_yields_nothing(self) -> None:
        assert types_by_class(scan_declarations("int x;", "java"), {}, []) == {}


def test_the_language_set_is_what_the_patterns_declare() -> None:
    """Excluding a language means removing its shapes, not gating a caller."""
    assert set(RECEIVER_TYPE_LANGUAGES) == {
        "cpp",
        "csharp",
        "go",
        "java",
        "kotlin",
        "python",
        "swift",
    }


def test_go_is_not_a_field_language() -> None:
    """Go declares no field this mechanism can read, and must not claim to.

    Its shapes capture no closer, so class scope would drop every one of them
    anyway — but the set is the contract, and a package-level ``var`` is a
    wider scope than a field rather than the same one.
    """
    assert "go" not in IMPLICIT_FIELD_LANGUAGES


@pytest.mark.parametrize("language", ["java", "csharp", "python", "go"])
def test_an_empty_body_is_not_an_error(language: str) -> None:
    assert declared_types("", language) == {}


def unwrapped(body: str) -> frozenset[str]:
    """The names in *body* whose type came from inside a wrapper."""
    return unwrapped_names_in_span(scan_declarations(body, "cpp"), 1, 10_000)


class TestCppDeclarations:
    """The C family's shape with ``::``, a pointer star, and lowercase heads."""

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ("Slice key = x;", {"key": "Slice"}),
            ("Status s;", {"s": "Status"}),
            ("DB* db;", {"db": "DB"}),
            ("DB *db;", {"db": "DB"}),
            ("Options& opt = o;", {"opt": "Options"}),
            ("const Options& opt = o;", {"opt": "Options"}),
            ("leveldb::Slice key;", {"key": "Slice"}),
            ("void f(Slice key, DB* db);", {"key": "Slice", "db": "DB"}),
            ("Row scratch{1};", {"scratch": "Row"}),
        ],
    )
    def test_the_shapes_it_reads(self, body: str, expected: dict[str, str]) -> None:
        assert declared_types(body, "cpp") == expected

    def test_a_method_declaration_is_not_a_variable(self) -> None:
        """The commonest line in a C++ header, and the reason ``(`` cannot close.

        ``Status doIt(int x);`` reads as a ``doIt`` of type ``Status`` under any
        closer set that admits the bracket, and a header is mostly these.
        """
        assert declared_types("Status doIt(int x);", "cpp") == {}

    def test_a_constructed_local_is_given_up_rather_than_guessed(self) -> None:
        """Same exclusion seen from the other side: a real declaration, dropped."""
        assert declared_types("BtRequestMessage msg(0, 16);", "cpp") == {}

    def test_auto_names_no_type(self) -> None:
        assert declared_types("auto it = m.begin();", "cpp") == {}

    @pytest.mark.parametrize("keyword", ["struct", "class", "namespace", "union", "enum"])
    def test_a_keyword_head_is_not_a_type(self, keyword: str) -> None:
        assert declared_types(f"{keyword} foo {{", "cpp") == {}

    def test_a_builtin_is_refused(self) -> None:
        assert declared_types("int count = 0;", "cpp") == {}

    def test_a_comment_declares_nothing(self) -> None:
        source = "// Slice key is the thing;\n/* Status s; */\nDB* db;"
        assert declared_types(source, "cpp") == {"db": "DB"}


class TestCppWrapperUnwrapping:
    """``shared_ptr<Foo> p`` is a ``Foo`` at every call its arrow can reach."""

    def test_a_smart_pointer_yields_what_it_holds(self) -> None:
        assert declared_types("std::shared_ptr<Request> req;", "cpp") == {"req": "Request"}
        assert unwrapped("std::shared_ptr<Request> req;") == {"req"}

    def test_a_deleter_is_not_what_the_arrow_reaches(self) -> None:
        assert declared_types("std::unique_ptr<Peer, D> peer;", "cpp") == {"peer": "Peer"}

    def test_a_container_keeps_its_head(self) -> None:
        """The control the unwrap has to fail: ``v.size()`` is not ``Entry::size``."""
        assert declared_types("std::vector<Entry> items;", "cpp") == {"items": "vector"}
        assert unwrapped("std::vector<Entry> items;") == frozenset()

    def test_a_repo_wrapper_is_not_assumed_to_forward(self) -> None:
        """Whether ``SharedHandle``'s arrow forwards is not written here."""
        assert declared_types("SharedHandle<Thing> h;", "cpp") == {"h": "SharedHandle"}
        assert unwrapped("SharedHandle<Thing> h;") == frozenset()

    def test_weak_ptr_forwards_nowhere(self) -> None:
        assert unwrapped("std::weak_ptr<Thing> w;") == frozenset()

    def test_another_language_keeps_the_generic_head(self) -> None:
        """The unwrap is C++ only: a Kotlin ``Column<T>`` really is a ``Column``."""
        assert declared_types("val c: Column<Thing> = x", "kotlin") == {"c": "Column"}


def test_cpp_is_not_a_field_language() -> None:
    """C++ declares its fields in a header, away from the call that reads them.

    Registering it would promise a scope the scan cannot answer from, which is
    the condition that disqualified python on the same constant.
    """
    assert "cpp" not in IMPLICIT_FIELD_LANGUAGES


def test_c_has_no_shapes_of_its_own() -> None:
    """``c`` shares the C++ call strategies and is excluded here instead.

    A struct declares no method, so the pair index the fallback ends in can
    hold nothing for it.
    """
    assert "c" not in RECEIVER_TYPE_LANGUAGES
    assert scan_declarations("Widget* w;", "c") == ()
