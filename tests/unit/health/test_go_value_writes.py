"""Go: a write through a field or an index of a value is a write of the variable.

Go copies a struct and an array on assignment and on a call. ``p.x += n`` with
``p`` a struct therefore changes the variable ``p`` itself, and a helper that
is handed ``p`` and returns nothing changes only its own copy (#3377). A
pointer, a map and a slice share their storage with the copy, so the same
write through one of those is not a write of the variable.

The def/use dialect reads the declared type from the file. Where the file does
not say (a name bound from another file's function, a type declared
elsewhere), the write counts as a write of the variable too: returning it is
right whichever it turns out to be.
"""

from __future__ import annotations

import textwrap

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import analyze_file, find_extractions

_TYPES = """
package m

type P struct {
    x   int
    q   Q
    ptr *Q
    xs  []int
    arr [4]int
    m   map[string]int
}
type Q struct{ x int }
type H [4]int
type M map[string]int
type S []int
type A = P

func mk() P      { return P{} }
func mkp() *P    { return &P{} }
func two() (P, error) { return P{}, nil }
func named() (out P) { return }
func namedp() (out *P) { return }
func pair() (a, b P) { return }
"""


def _functions(body: str):
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip("tree-sitter language pack missing for go")
    if _get_language("go") is None:
        pytest.skip("tree-sitter language pack missing for go")
    src = _TYPES + textwrap.dedent(body)
    return analyze_file("m.go", "go", src.encode(), flagged_only=False).functions


def _f(body: str):
    return next(fn for fn in _functions(body) if fn.name == "f")


def _is_write(signature: str, statement: str, before: str = "") -> bool:
    fn = _f(f"\nfunc f({signature}) {{\n{before}\n    marker := 0\n{statement}\n}}\n")
    marker = next(d.line for d in fn.def_use.definitions if d.var == "marker")
    return any(d.var == "p" and d.line == marker + 1 for d in fn.def_use.definitions)


def _is_read(signature: str, statement: str, before: str = "") -> bool:
    fn = _f(f"\nfunc f({signature}) {{\n{before}\n    marker := 0\n{statement}\n}}\n")
    marker = next(d.line for d in fn.def_use.definitions if d.var == "marker")
    return any(
        u.name == "p" and u.line == marker + 1 for b in fn.def_use.blocks.values() for u in b.uses
    )


def _kind(signature: str, statement: str, before: str = "") -> str:
    """What the dialect makes of the target of the last assignment in ``f``."""
    return _storage(signature, statement, before)[1]


def _storage(signature: str, statement: str, before: str = ""):
    from tree_sitter import Parser

    from repowise.core.analysis.health.dataflow.dialects.go_values import storage
    from repowise.core.ingestion.parser import _get_language

    src = _TYPES + f"\nfunc f({signature}) {{\n{before}\n{statement}\n}}\n"
    root = Parser(_get_language("go")).parse(src.encode()).root_node
    targets = []
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        if node.type == "assignment_statement":
            targets.append(node.child_by_field_name("left").named_children[0])
    last = max(targets, key=lambda n: n.start_byte)
    return storage(last)


# (id, parameters, statements before, the write)
_VALUES = [
    ("struct-parameter", "p P", "", "p.x = 1"),
    ("struct-parameter-compound", "p P", "", "p.x += 1"),
    ("struct-parameter-increment", "p P", "", "p.x++"),
    ("struct-literal-type-parameter", "p struct{ x int }", "", "p.x = 1"),
    ("array-parameter", "p [4]int", "", "p[0] = 1"),
    ("named-array-parameter", "p H", "", "p[0] = 1"),
    ("alias-of-a-struct", "p A", "", "p.x = 1"),
    ("nested-struct-field", "p P", "", "p.q.x = 1"),
    ("array-field", "p P", "", "p.arr[0] = 1"),
    ("array-of-structs", "p [2]Q", "", "p[0].x = 1"),
    ("parenthesized", "p P", "", "(p).x = 1"),
    ("var-declaration", "", "    var p P", "p.x = 1"),
    ("var-with-a-literal", "", "    var p = P{}", "p.x = 1"),
    ("short-declaration-literal", "", "    p := P{x: 1}", "p.x = 2"),
    ("array-literal", "", "    p := [4]int{}", "p[0] = 1"),
    ("second-of-two-names", "", "    o, p := &P{}, P{}\n    _ = o", "p.x = 1"),
    ("result-of-a-function-in-this-file", "", "    p := mk()", "p.x = 1"),
    ("copy-of-a-struct-local", "o P", "    p := o", "p.x = 1"),
    ("struct-field-of-a-local", "o P", "    p := o.q", "p.x = 1"),
    ("struct-field-through-a-pointer", "o *P", "    p := o.q", "p.x = 1"),
    ("element-of-an-array", "o [2]Q", "    p := o[0]", "p.x = 1"),
    ("type-assertion-to-a-struct", "v any", "    p := v.(P)", "p.x = 1"),
    ("named-result", ") (p P", "", "p.x = 1"),
    ("result-of-a-function-with-one-named-result", "", "    p := named()", "p.x = 1"),
    ("comma-ok-assertion-to-a-struct", "v any", "    p, ok := v.(P)\n    _ = ok", "p.x = 1"),
    ("dereference-of-a-pointer-local", "o *P", "    p := *o", "p.x = 1"),
    ("value-receiver-style-parameter-list", "v int, p P", "", "p.x = v"),
]


@pytest.mark.parametrize(
    ("signature", "before", "statement"), [pytest.param(*r[1:], id=r[0]) for r in _VALUES]
)
def test_a_write_through_a_value_is_a_write_of_the_variable(signature, before, statement):
    assert _is_write(signature, "    " + statement, before)
    # ... and still a read: the rest of the value is kept.
    assert _is_read(signature, "    " + statement, before)
    if not statement.endswith("++"):
        assert _kind(signature, "    " + statement, before) == "value"


_REFERENCES = [
    ("pointer-parameter", "p *P", "", "p.x = 1"),
    ("pointer-parameter-increment", "p *P", "", "p.x++"),
    ("map-parameter", "p map[string]int", "", 'p["a"] = 1'),
    ("slice-parameter", "p []int", "", "p[0] = 1"),
    ("named-map-parameter", "p M", "", 'p["a"] = 1'),
    ("named-slice-parameter", "p S", "", "p[0] = 1"),
    ("pointer-field-on-the-path", "p P", "", "p.ptr.x = 1"),
    ("slice-field-on-the-path", "p P", "", "p.xs[0] = 1"),
    ("map-field-on-the-path", "p P", "", 'p.m["a"] = 1'),
    ("slice-of-structs", "p []Q", "", "p[0].x = 1"),
    ("address-of-a-literal", "", "    p := &P{}", "p.x = 1"),
    ("new", "", "    p := new(P)", "p.x = 1"),
    ("make", "", "    p := make([]int, 3)", "p[0] = 1"),
    ("slice-literal", "", "    p := []int{1}", "p[0] = 1"),
    ("map-literal", "", "    p := map[string]int{}", 'p["a"] = 1'),
    ("pointer-result-of-a-function-in-this-file", "", "    p := mkp()", "p.x = 1"),
    ("copy-of-a-pointer-local", "o *P", "    p := o", "p.x = 1"),
    ("pointer-field-of-a-local", "o P", "    p := o.ptr", "p.x = 1"),
    ("type-assertion-to-a-pointer", "v any", "    p := v.(*P)", "p.x = 1"),
    ("comma-ok-assertion-to-a-pointer", "v any", "    p, ok := v.(*P)\n    _ = ok", "p.x = 1"),
    ("pointer-result-of-a-function-with-one-named-result", "", "    p := namedp()", "p.x = 1"),
    (
        "a-closure-s-own-declaration-of-the-name",
        "p *P",
        "    g := func() {\n        p := P{}\n        _ = p\n    }\n    _ = g",
        "p.x = 1",
    ),
]


@pytest.mark.parametrize(
    ("signature", "before", "statement"), [pytest.param(*r[1:], id=r[0]) for r in _REFERENCES]
)
def test_a_write_through_a_reference_is_not(signature, before, statement):
    assert not _is_write(signature, "    " + statement, before)
    assert _is_read(signature, "    " + statement, before)
    if not statement.endswith("++"):
        assert _kind(signature, "    " + statement, before) == "reference"


@pytest.mark.parametrize(
    ("signature", "statement"),
    [
        pytest.param("v int, p ...P", "p[0].x = 1", id="field-of-an-element"),
        pytest.param("v int, p ...int", "p[0] = 1", id="element"),
        pytest.param("p ...[2]Q", "p[0][1].x = 1", id="array-element"),
    ],
)
def test_a_variadic_parameter_is_a_slice(signature, statement):
    """``p ...T`` is ``[]T`` in the body: the callee and the helper share its
    backing array. It is a local of the function all the same, and read."""
    base, kind = _storage(signature, "    " + statement)

    assert kind == "reference"
    assert base is not None and base.text == b"p"
    assert not _is_write(signature, "    " + statement)
    assert _is_read(signature, "    " + statement)


_UNKNOWN = [
    ("type-from-another-package", "p other.T", "", "p.x = 1"),
    ("type-declared-in-another-file", "p Elsewhere", "", "p.x = 1"),
    ("type-declared-in-another-file-indexed", "p Elsewhere", "", "p[0] = 1"),
    ("result-of-a-function-in-another-file", "", "    p := load()", "p.x = 1"),
    ("result-of-a-method", "o P", "    p := o.Clone()", "p.x = 1"),
    ("one-of-two-results", "", "    p, err := two()\n    _ = err", "p.x = 1"),
    ("one-of-two-named-results", "", "    p, o := pair()\n    _ = o", "p.x = 1"),
    ("the-ok-of-a-comma-ok-assertion", "v any", "    o, p := v.(*P)\n    _ = o", "p.x = 1"),
    ("received-from-a-channel", "ch chan P", "    p := <-ch", "p.x = 1"),
    (
        "an-unknown-declaration-beside-a-reference",
        "c bool",
        "    if c {\n        p := load()\n        _ = p\n    }\n    p := &P{}",
        "p.x = 1",
    ),
    ("range-variable", "ps []P", "    for _, p := range ps {", "p.x = 1\n    }"),
    ("field-of-an-unknown-type", "o other.T", "    p := o.f", "p.x = 1"),
    ("unknown-field-on-the-path", "p P", "", "p.missing.x = 1"),
    (
        "two-declarations-that-disagree",
        "c bool",
        "    if c {\n        p := &P{}\n        _ = p\n    }\n    p := P{}",
        "p.x = 1",
    ),
]


@pytest.mark.parametrize(
    ("signature", "before", "statement"), [pytest.param(*r[1:], id=r[0]) for r in _UNKNOWN]
)
def test_a_write_the_file_cannot_classify_counts_as_a_write(signature, before, statement):
    assert _is_write(signature, "    " + statement, before)
    assert _kind(signature, "    " + statement, before) == "unknown"


def test_a_package_variable_and_a_captured_one_are_not_locals():
    """The helper writes the same storage the function does."""
    fns = _functions(
        """
        var cfg P

        func f(seen []int) {
            marker := 0
            cfg.x = 1
            _ = marker
        }
        """
    )
    fn = next(fn for fn in fns if fn.name == "f")
    assert {d.var for d in fn.def_use.definitions} == {"seen", "marker"}


# -- Extract Method -------------------------------------------------------------


def _spans(body: str, name: str = "f"):
    fns = _functions(body)
    fn = next(f for f in fns if f.name == name)
    return [
        (x.start_line - _OFFSET, x.end_line - _OFFSET, x.params, x.returns)
        for x in find_extractions(fn, get_language_map("go"))
    ]


#: Lines the shared type block puts above each body.
_OFFSET = _TYPES.count("\n")

_CONDITIONAL = "".join(
    f"    if v > {i} {{\n        {{write}} {i}\n    }}\n" for i in range(1, 5)
)
_UNCONDITIONAL = "".join(
    f"    if v > {i} {{\n        note(v)\n    }}\n    {{write}} {i}\n" for i in range(1, 5)
)


def _function(parameters: str, writes: str, write: str, result: str, before: str = "") -> str:
    return (
        f"\nfunc f({parameters}) int {{\n{before}"
        + writes.replace("{write}", write)
        + f"    return {result}\n}}\n"
    )


def test_the_issue_s_span_is_not_offered_with_the_write_dropped():
    """Four conditional ``p.x +=`` on a struct parameter, then ``return p.x``.
    Every span was offered as ``(p, v) -> ()``."""
    spans = _spans(_function("v int, p P", _CONDITIONAL, "p.x +=", "p.x"))

    assert all("p" in returns for _, _, _, returns in spans)
    # A conditionally written name that is also an input is refused today,
    # for a scalar as well (the control below), so nothing is offered.
    assert spans == []


def test_a_conditionally_written_scalar_is_treated_the_same_way():
    assert _spans(_function("v int, n int", _CONDITIONAL, "n +=", "n")) == []


def test_an_unconditional_write_through_a_struct_parameter_returns_it():
    spans = _spans(_function("v int, p P", _UNCONDITIONAL, "p.x +=", "p.x"))

    assert spans
    assert all(params == ("p", "v") and returns == ("p",) for _, _, params, returns in spans)


def test_an_unconditional_write_through_an_array_parameter_returns_it():
    spans = _spans(_function("v int, p [4]int", _UNCONDITIONAL, "p[0] +=", "p[0]"))

    assert spans
    assert all(returns == ("p",) for _, _, _, returns in spans)


def test_a_struct_local_declared_before_the_span_is_returned():
    spans = _spans(
        _function("v int", _UNCONDITIONAL, "p.x +=", "p.x", before="    var p P\n    p.x = v\n")
    )

    after_declaration = [s for s in spans if s[0] > 3]
    assert after_declaration
    assert all("p" in params and returns == ("p",) for _, _, params, returns in after_declaration)


def test_a_write_nobody_reads_afterwards_adds_no_return():
    """``return v``: a span that ends on the last write has nothing after it
    that reads ``p``; one that ends earlier is followed by the next ``p.x +=``."""
    spans = _spans(_function("v int, p P", _UNCONDITIONAL, "p.x +=", "v"))

    last = max(end for _, end, _, _ in spans)
    to_the_end = [s for s in spans if s[1] == last]
    earlier = [s for s in spans if s[1] < last]
    assert to_the_end and earlier
    assert all(returns == () for _, _, _, returns in to_the_end)
    assert all(returns == ("p",) for _, _, _, returns in earlier)


@pytest.mark.parametrize(
    ("parameters", "write", "result"),
    [
        pytest.param("v int, p *P", "p.x +=", "p.x", id="pointer"),
        pytest.param("v int, p map[string]int", 'p["a"] +=', 'p["a"]', id="map"),
        pytest.param("v int, p []int", "p[0] +=", "p[0]", id="slice"),
    ],
)
def test_a_reference_keeps_its_parameters_and_no_output(parameters, write, result):
    """Unchanged: both shapes are offered and return nothing."""
    conditional = _spans(_function(parameters, _CONDITIONAL, write, result))
    unconditional = _spans(_function(parameters, _UNCONDITIONAL, write, result))

    assert conditional and unconditional
    assert all(params == ("p", "v") and returns == () for _, _, params, returns in conditional)
    assert all(params == ("p", "v") and returns == () for _, _, params, returns in unconditional)


def _loop(parameters: str) -> str:
    """``p.x`` read at the top of a loop body and written further down it."""
    writes = "".join(
        f"        if v > {i} {{\n            note(v)\n        }}\n        p.x += i\n"
        for i in range(1, 5)
    )
    return (
        f"\nfunc f({parameters}, xs []int) int {{\n    t := 0\n    for _, i := range xs {{\n"
        "        if p.x > 3 {\n            t += p.x\n        }\n"
        + writes
        + "    }\n    return t\n}\n"
    )


def test_a_write_the_next_iteration_reads_is_not_offered():
    """Nothing after the span reads ``p``, but the next turn of the loop does:
    the existing loop-carry gate sees the write now."""
    assert _spans(_loop("v int, p P")) == []


def test_the_same_loop_through_a_pointer_is_offered_as_before():
    spans = _spans(_loop("v int, p *P"))

    assert spans
    assert all(params == ("i", "p", "v") and returns == () for _, _, params, returns in spans)


def test_a_pointer_receiver_keeps_its_output():
    body = (
        "\nfunc (s *P) f(v int) int {\n"
        + _CONDITIONAL.replace("{write}", "s.x +=")
        + "    return s.x\n}\n"
    )

    spans = _spans(body)

    assert spans
    assert all(returns == () for _, _, _, returns in spans)
