"""Callee names and receiver sets for generic calls, after type arguments are
skipped by ``_identifier_chain``.

The mock and assertion passes read these. Pinned so the effect of skipping a
type-argument list is explicit: TS ``type_arguments`` under a curried callee
used to supply the called name (``factory.make<Bar>()(x)`` read as ``bar``).
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity.ast_utils import _callee_names

_CALL_KINDS = ("invocation_expression", "call_expression", "method_invocation")


def _calls(language: str, source: str) -> dict[str, tuple[str, set[str]] | None]:
    try:
        from tree_sitter import Parser

        from repowise.core.ingestion.parser import _get_language

        tree = Parser(_get_language(language)).parse(source.encode())
    except Exception:
        pytest.skip(f"no {language} grammar on this build")
    out: dict[str, tuple[str, set[str]] | None] = {}
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in _CALL_KINDS:
            out[node.text.decode()] = _callee_names(node)
        stack.extend(node.children)
    return out


def test_a_curried_typescript_generic_reads_its_callee_not_its_type_argument() -> None:
    calls = _calls("typescript", "const g = factory.make<Bar>()(x); const m = vi.mocked<Foo>(dep);")
    assert calls["factory.make<Bar>()(x)"] == ("make", {"factory"})
    assert calls["factory.make<Bar>()"] == ("make", {"factory"})
    assert calls["vi.mocked<Foo>(dep)"] == ("mocked", {"vi"})


def test_a_java_explicit_type_argument_never_joins_the_receivers() -> None:
    calls = _calls("java", "class T { void m() { Foo f = Mockito.<Foo>mock(Foo.class); } }")
    assert calls["Mockito.<Foo>mock(Foo.class)"] == ("mock", {"mockito"})


def test_csharp_generic_calls_are_unchanged() -> None:
    """C#'s grammar names the list ``type_argument_list``, which is not
    skipped, so the type argument still reads as the called name. Recorded as
    the current behaviour, not endorsed."""
    calls = _calls(
        "csharp", "class T { void M() { var s = Substitute.For<IFoo>(); Arg.Any<MockFoo>(); } }"
    )
    assert calls["Substitute.For<IFoo>()"] == ("ifoo", {"substitute", "for"})
    assert calls["Arg.Any<MockFoo>()"] == ("mockfoo", {"arg", "any"})
