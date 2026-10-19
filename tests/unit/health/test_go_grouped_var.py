"""Go: a grouped ``var ( ... )`` declares its names (#3469).

The group wraps its specs in a ``var_spec_list``, and the dialect looked for
the declared names one level too high: none of them got a definition.

Each shape is tested beside the spelling that already worked.
"""

from __future__ import annotations

import textwrap

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import analyze_file, find_extractions


def _f(src: str, name: str = "f"):
    try:
        from repowise.core.ingestion.parser import _get_language
    except ImportError:
        pytest.skip("tree-sitter language pack missing for go")
    if _get_language("go") is None:
        pytest.skip("tree-sitter language pack missing for go")
    functions = analyze_file(
        "m.go", "go", ("package m\n" + textwrap.dedent(src).lstrip("\n")).encode(), flagged_only=False
    ).functions
    return next(fn for fn in functions if fn.name == name)


def _defs(fn) -> set[tuple[str, int]]:
    return {(d.var, d.line) for d in fn.def_use.definitions}


def _reads(fn) -> set[tuple[str, int]]:
    return {(u.name, u.line) for b in fn.def_use.blocks.values() for u in b.uses if not u.echo}


def _declared(fn) -> set[str]:
    return {d.var for d in fn.def_use.definitions if d.declares}


# -- a grouped var ---------------------------------------------------------------

_GROUPED = """
func f(c int) int {
    var (
        a int
        b, d = c, 2
        // a comment between specs
        _ = c
    )
    a = c
    return a + b + d
}
"""

_ONE_PER_LINE = """
func f(c int) int {
    var a int
    var b, d = c, 2

    var _ = c

    a = c
    return a + b + d
}
"""


def test_a_grouped_var_defines_its_names_where_they_are_declared():
    fn = _f(_GROUPED)
    assert {("a", 4), ("b", 5), ("d", 5)} <= _defs(fn)
    assert _declared(fn) == {"a", "b", "d"}
    # The initialisers are read, and the blank identifier binds nothing.
    assert {("c", 5), ("c", 7)} <= _reads(fn)
    assert "_" not in {name for name, _line in _defs(fn)}


def test_a_grouped_var_reads_like_one_var_per_line():
    grouped, single = _f(_GROUPED), _f(_ONE_PER_LINE)
    assert {name for name, _line in _defs(grouped)} == {name for name, _line in _defs(single)}
    assert _declared(grouped) == _declared(single)
    assert {name for name, _line in _reads(grouped)} == {name for name, _line in _reads(single)}


def test_a_grouped_const_still_defines_its_names():
    """The control: a grouped ``const`` has no list around its specs."""
    fn = _f(
        """
        func f() int {
            const (
                k = 5
                m, n = 6, 7
            )
            return k + m + n
        }
        """
    )
    assert {("k", 4), ("m", 5), ("n", 5)} <= _defs(fn)


def test_a_one_spec_group_and_an_empty_group():
    fn = _f(
        """
        func f(c int) int {
            var (
                only = c
            )
            var ()
            return only
        }
        """
    )
    assert ("only", 4) in _defs(fn)
    assert ("c", 4) in _reads(fn)


# The shape of client_golang's ``(*histogramCounts).observe``: names declared
# in a group, read by the statements after it.
_OBSERVE = """
func f(v int, hc *H) {
    {DECL}
    frac := v * 2
    if scale > 0 {
        key = frac * scale
    } else {
        key = frac
        if frac == 5 {
            key--
        }
    }
    if v > zero {
        hc.add(key)
    }
    println(v)
    println(frac)
    println(key)
}
"""
_DECL_GROUPED = "var (\n        key   int\n        scale = hc.scale\n        zero  = hc.zero\n    )"
_DECL_SINGLE = "var key int\n    var scale = hc.scale\n    var zero = hc.zero\n    _ = 0\n    _ = 0"


@pytest.mark.parametrize("decl", [_DECL_GROUPED, _DECL_SINGLE], ids=["grouped", "one per line"])
def test_a_span_that_reads_a_declared_name_is_given_it(decl):
    fn = _f(_OBSERVE.replace("{DECL}", decl))
    first = next(d.line for d in fn.def_use.definitions if d.var == "frac")
    spans = [x for x in find_extractions(fn, get_language_map("go")) if x.start_line >= first]
    assert spans, "expected a span after the declarations"
    for x in spans:
        source_reads = {name for name, line in _reads(fn) if x.start_line <= line <= x.end_line}
        for name in ("scale", "zero"):
            if name in source_reads:
                assert name in x.params, (x.start_line, x.end_line, x.params)


# A span that holds the declarations, with one of the names read after it.
_DECLARED_IN_THE_SPAN = """
func f(v int, hc *H) {
    println(v)
    {DECL}
    if scale > 0 {
        hc.add(v * scale)
    } else {
        hc.add(v)
    }
    println(v)
    println(hc)
    println(zero)
}
"""
_PAIR_GROUPED = "var (\n        scale = hc.scale\n        zero  = hc.zero\n    )"
_PAIR_SINGLE = "var scale = hc.scale\n    var zero = hc.zero\n    _ = 0\n    _ = 0"


@pytest.mark.parametrize("decl", [_PAIR_GROUPED, _PAIR_SINGLE], ids=["grouped", "one per line"])
def test_a_span_holding_the_declarations_returns_the_name_used_after(decl):
    fn = _f(_DECLARED_IN_THE_SPAN.replace("{DECL}", decl))
    declared_at = min(d.line for d in fn.def_use.definitions if d.var == "zero")
    last_read = max(line for name, line in _reads(fn) if name == "zero")
    holding = [
        x
        for x in find_extractions(fn, get_language_map("go"))
        if x.start_line <= declared_at <= x.end_line < last_read
    ]
    assert holding, "expected a span that holds the declarations"
    assert all(x.returns == ("zero",) for x in holding)
