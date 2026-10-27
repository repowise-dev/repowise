"""Go: the receive of a ``select`` case binds its names (#3469, #3451).

``case v := <-ch:`` and ``case x = <-ch:`` are ``receive_statement`` nodes,
which were no write kind: ``v`` had no definition and ``x`` no write on its
case line. A ``select`` is one CFG statement, and the receive happens only if
its case is the one taken, so both are may-defs: they add to what a later
read sees and hide nothing. That holds for the ``:=`` form too, whose name
lives only in its case and often repeats one from outside (``case err :=
<-errCh:`` beside an outer ``err``).
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


def _may(fn) -> set[tuple[str, int]]:
    return {(d.var, d.line) for d in fn.def_use.definitions if d.may}


# -- the receive of a select case --------------------------------------------------

_SELECT = """
func f(ch chan int, done chan bool, s *S) int {
    var got int
    select {
    case v := <-ch:
        got = v
    case got = <-ch:
    case x, ok := <-ch:
        got = x
        _ = ok
    case s.last = <-ch:
    case <-done:
    case ch <- got:
    }
    return got
}
"""


def test_a_select_case_receive_binds_its_names():
    fn = _f(_SELECT)
    defs = _defs(fn)
    assert {("v", 5), ("got", 7), ("x", 8), ("ok", 8)} <= defs
    # ``:=`` declares; ``=`` assigns a variable that exists.
    assert {"v", "x", "ok"} <= _declared(fn)
    line_7 = [d for d in fn.def_use.definitions if (d.var, d.line) == ("got", 7)]
    assert len(line_7) == 1 and not line_7[0].declares


def test_a_select_case_receive_is_a_may_def_in_both_forms():
    fn = _f(_SELECT)
    assert {("v", 5), ("got", 7), ("x", 8), ("ok", 8)} <= _may(fn)
    # The declaration before the ``select`` is certain, as it was.
    assert ("got", 3) not in _may(fn)


def test_a_select_case_receive_reads_its_channel_and_its_target_base():
    fn = _f(_SELECT)
    reads = _reads(fn)
    assert {("ch", 5), ("ch", 7), ("ch", 8), ("ch", 11), ("done", 12)} <= reads
    # ``s.last = <-ch`` writes through a pointer: ``s`` is read, not written.
    assert ("s", 11) in reads
    assert "s" not in {name for name, line in _defs(fn) if line == 11}
    # A send reads both sides and binds nothing.
    assert {("ch", 13), ("got", 13)} <= reads
    assert not {name for name, line in _defs(fn) if line in (12, 13)}


def test_a_name_bound_by_a_receive_is_read_in_its_case():
    fn = _f(_SELECT)
    assert {("v", 6), ("x", 9), ("ok", 10)} <= _reads(fn)


def test_a_span_in_a_case_is_given_the_name_the_receive_bound():
    fn = _f(
        """
        func f(ch chan int, out *Sink) {
            select {
            case v := <-ch:
                if v > 10 {
                    out.add(v)
                } else {
                    out.add(0)
                }
                out.note(v)
                out.done()
                println(ch)
            case <-out.stop:
            }
        }
        """
    )
    spans = [
        (x.start_line, x.end_line, x.params)
        for x in find_extractions(fn, get_language_map("go"))
        if x.start_line == 5
    ]
    assert (5, 10, ("out", "v")) in spans
    assert all("v" in params for _start, _end, params in spans)


# The value from before the ``select`` is still the one a later read can see:
# a span that holds the earlier write must return it. The slicer returns one
# value, so in the first shape (from the review of #3472) a span holding the
# write has ``n`` or ``total`` to return as well and is not offered at all; in
# the second it can return ``err`` alone.
_OUTER = """
func f(xs []int, ch chan error) error {
    err := g(xs[0])
    n := len(xs)
    if n > 3 {
        err = h(n)
        n = n * 2
    }
    total := n + 1
    log(total)
    {STATEMENT}
    use(total)
    return err
}
"""
_OUTER_ALONE = """
func f(xs []int, ch chan error) error {
    err := g(xs[0])
    n := len(xs)
    if n > 3 {
        err = h(n)
        n = n * 2
    }
    if n > 9 {
        log(n)
    } else {
        log(0)
    }
    {STATEMENT}
    return err
}
"""
_SELECT_DECLARES = "select {\n    case err := <-ch:\n        log(err)\n    default:\n        log(nil)\n    }"
_SELECT_ASSIGNS = "select {\n    case err = <-ch:\n        log(err)\n    default:\n        log(nil)\n    }"
_IF_TWIN = "if len(ch) > 0 {\n        err = <-ch\n        log(err)\n    } else {\n        log(nil)\n    }"
_STATEMENTS = pytest.mark.parametrize(
    "statement", [_SELECT_DECLARES, _SELECT_ASSIGNS, _IF_TWIN], ids=[":=", "=", "if twin"]
)


def _spans_holding_the_earlier_write(shape: str, statement: str):
    fn = _f(shape.replace("{STATEMENT}", statement))
    write = next(
        d.line for d in fn.def_use.definitions if d.var == "err" and not d.declares and not d.may
    )
    last_read = max(line for name, line in _reads(fn) if name == "err")
    spans = find_extractions(fn, get_language_map("go"))
    return [x for x in spans if x.start_line <= write <= x.end_line < last_read]


@_STATEMENTS
def test_no_span_holds_the_earlier_write_and_drops_it(statement):
    for shape in (_OUTER, _OUTER_ALONE):
        holding = _spans_holding_the_earlier_write(shape, statement)
        assert [(x.start_line, x.end_line) for x in holding if "err" not in x.returns] == []


@_STATEMENTS
def test_a_span_that_holds_the_earlier_write_returns_it(statement):
    holding = _spans_holding_the_earlier_write(_OUTER_ALONE, statement)
    assert [(x.start_line, x.end_line, x.returns) for x in holding] == [(3, 13, ("err",))]


def test_the_read_after_the_select_sees_the_writes_from_before_it():
    from repowise.core.analysis.health.dataflow.reaching import observed_definitions

    for statement in (_SELECT_DECLARES, _SELECT_ASSIGNS):
        fn = _f(_OUTER.replace("{STATEMENT}", statement))
        defs = fn.reaching.definitions
        last_read = max(line for name, line in _reads(fn) if name == "err")
        seen = {
            defs[i].line
            for use, indices in observed_definitions(fn.def_use, fn.reaching)
            if (use.name, use.line) == ("err", last_read) and not use.echo
            for i in indices
        }
        assert {3, 6} <= seen, statement


def test_a_plain_receive_assignment_outside_select_is_unchanged():
    """The control: ``v := <-ch`` as a statement is a short var declaration."""
    fn = _f(
        """
        func f(ch chan int) int {
            v := <-ch
            var w int
            w = <-ch
            return v + w
        }
        """
    )
    assert {("v", 3), ("w", 4), ("w", 5)} <= _defs(fn)
