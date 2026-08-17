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
        # Kotlin rather than Go: Go registered its shapes and now types this
        # exact line. Kotlin is still one of the languages P8 left unattempted.
        body = "fun run(w: TimerWheel) { }"
        assert declared_types(body, "kotlin") == {}


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
    assert set(RECEIVER_TYPE_LANGUAGES) == {"csharp", "go", "java", "python"}


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
