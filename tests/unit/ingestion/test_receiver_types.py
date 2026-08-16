"""What the receiver-type scan reads off a function body, and what it refuses.

The refusals matter more than the matches. Every wrong type this scan returns
is a wrong edge the resolver's validator has to catch, and the cases below are
the ones a real precision audit found rather than the ones that read well.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.languages.receiver_types import (
    RECEIVER_TYPE_LANGUAGES,
    declared_types,
)


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

    def test_package_qualifier_is_dropped(self) -> None:
        body = "void run(com.example.cache.Ticker ticker) { }"
        assert declared_types(body, "java")["ticker"] == "Ticker"


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
        body = "void run() { Reader source = a(); if (x) { Writer source = b(); } }"
        assert "source" not in declared_types(body, "java")

    def test_a_cast_is_not_a_declaration(self) -> None:
        assert declared_types("void run() { var n = (Node) raw; }", "java") == {}

    def test_a_qualified_constant_argument_is_not_a_declaration(self) -> None:
        assert declared_types("void run() { call(Config.DEFAULT, other); }", "java") == {}

    def test_a_declaration_in_a_line_comment_is_ignored(self) -> None:
        assert declared_types("void run() { // a Node node; once lived here\n }", "java") == {}

    def test_an_unregistered_language_yields_nothing(self) -> None:
        body = "func run(w *TimerWheel) { }"
        assert declared_types(body, "go") == {}


def test_the_language_set_is_what_the_patterns_declare() -> None:
    """Excluding a language means removing its shapes, not gating a caller."""
    assert set(RECEIVER_TYPE_LANGUAGES) == {"csharp", "java"}


@pytest.mark.parametrize("language", ["java", "csharp"])
def test_an_empty_body_is_not_an_error(language: str) -> None:
    assert declared_types("", language) == {}
