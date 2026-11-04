"""Go: taking a variable's address is a write of it.

``fill(&buf, c)`` hands the callee the variable itself, and the callee may
assign through it. Read as a plain read of ``buf``, the span around the call
passed ``buf`` into the helper by value and did not return it: the callee
then filled the helper's copy and the caller went on with the old one (#3467).

The write may not happen, so it is a may-def: a def with a paired use. The
address of a field or an element reaches the variable only where the path
stays inside the value (:func:`go_values.written_value`).
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
}
type Q struct{ x int }

var shared P

func fill(dst *[]byte, b byte) { *dst = append(*dst, b) }
func load(dst *P) error         { return nil }
"""


def _functions(body: str):
    try:
        from repowise.core.ingestion.parser import _get_language
    except ImportError:
        pytest.skip("tree-sitter language pack missing for go")
    if _get_language("go") is None:
        pytest.skip("tree-sitter language pack missing for go")
    src = _TYPES + textwrap.dedent(body)
    return analyze_file("m.go", "go", src.encode(), flagged_only=False).functions


def _f(body: str):
    return next(fn for fn in _functions(body) if fn.name == "f")


def _at_marker(signature: str, statement: str, before: str = ""):
    """The analysis of ``f`` and the line of *statement*, which follows a marker."""
    fn = _f(f"\nfunc f({signature}) {{\n{before}\n    marker := 0\n{statement}\n    _ = marker\n}}\n")
    marker = next(d.line for d in fn.def_use.definitions if d.var == "marker")
    return fn, marker + 1


def _writes(signature: str, statement: str, before: str = "", var: str = "p") -> bool:
    fn, line = _at_marker(signature, statement, before)
    return any(d.var == var and d.line == line for d in fn.def_use.definitions)


def _reads(signature: str, statement: str, before: str = "", var: str = "p") -> bool:
    fn, line = _at_marker(signature, statement, before)
    return any(
        u.name == var and u.line == line and not u.echo
        for b in fn.def_use.blocks.values()
        for u in b.uses
    )


def _may(signature: str, statement: str, before: str = "", var: str = "p") -> bool:
    """The write is recorded with its paired use, as a write that may not happen."""
    fn, line = _at_marker(signature, statement, before)
    return any(
        u.name == var and u.line == line and u.echo
        for b in fn.def_use.blocks.values()
        for u in b.uses
    )


# -- the address of the variable itself -------------------------------------------


@pytest.mark.parametrize(
    ("signature", "before"),
    [
        ("p P", ""),  # a struct
        ("p []byte", ""),  # a slice: the callee can replace the header
        ("p map[string]int", ""),  # a map: the callee can replace the map
        ("p *P", ""),  # a pointer: the callee can repoint it
        ("p int", ""),
        ("", "    var p P"),
        ("", "    p := make([]byte, 0, 8)"),
    ],
    ids=["struct", "slice", "map", "pointer", "int", "var", "short var"],
)
def test_address_of_a_local_is_a_write_and_a_read(signature, before):
    assert _writes(signature, "    use(&p)", before)
    assert _reads(signature, "    use(&p)", before)
    assert _may(signature, "    use(&p)", before)


@pytest.mark.parametrize(
    "statement",
    [
        "    q := &p\n    _ = q",
        "    use(1, &p, 2)",
        "    x := T{dst: &p}\n    _ = x",
        "    go use(&p)",
        "    defer use(&p)",
        "    use((&p))",
        "    if err := load(&p); err != nil {\n        return\n    }",
    ],
    ids=["stored", "among arguments", "in a literal", "go", "defer", "parenthesised", "if head"],
)
def test_address_of_is_a_write_wherever_it_stands(statement):
    assert _writes("p P", statement)


def test_address_of_a_package_variable_is_no_write_of_a_local():
    fn, line = _at_marker("", "    use(&shared)")
    assert not any(d.line == line for d in fn.def_use.definitions)


def test_a_plain_argument_is_still_only_a_read():
    assert not _writes("p P", "    use(p)")
    assert _reads("p P", "    use(p)")


def test_other_unary_operators_do_not_write():
    assert not _writes("p int", "    use(-p)")
    assert not _writes("p bool", "    use(!p)")
    assert not _writes("p *P", "    use(*p)")
    assert not _writes("p chan int", "    use(<-p)")


# -- the address of a part of the variable ----------------------------------------


@pytest.mark.parametrize(
    ("signature", "operand", "written"),
    [
        ("p P", "p.x", True),  # a field of a struct held by value
        ("p P", "p.q.x", True),
        ("p P", "p.arr[0]", True),  # an element of an array held by value
        ("p [4]int", "p[1]", True),
        ("p *P", "p.x", False),  # through a pointer: storage the caller shares
        ("p P", "p.ptr.x", False),
        ("p P", "p.xs[0]", False),  # a slice's elements are shared
        ("p []int", "p[0]", False),
        ("p map[string]P", 'p["k"]', False),
    ],
)
def test_address_of_a_part_writes_the_variable_only_inside_the_value(signature, operand, written):
    assert _writes(signature, f"    use(&{operand})") is written
    assert _reads(signature, f"    use(&{operand})")


def test_address_of_a_part_of_an_unknown_type_counts_as_a_write():
    # ``Other`` is declared in another file: returning the variable is right
    # whichever it holds.
    assert _writes("p Other", "    use(&p.x)")


# -- what the slicer makes of it ----------------------------------------------------

_SQUEEZE = """
func squeeze(s string, c byte) string {
    if len(s) == 0 {
        return s
    }
    buf := make([]byte, 0, 64)
    r := 0
    w := 0
    for n := len(s); r < n; {
        if s[r] == c {
            {FIRST}
            w++
            r++
            for r < n && s[r] == c {
                r++
            }
        } else {
            {SECOND}
            w++
            r++
        }
    }
    if len(buf) == 0 {
        return s[:w]
    }
    return string(buf[:w])
}
"""


def _spans_holding_the_loop(first: str, second: str) -> list[tuple]:
    body = _SQUEEZE.replace("{FIRST}", first).replace("{SECOND}", second)
    fn = next(f for f in _functions(body) if f.name == "squeeze")
    loop = next(
        st.start_line
        for b in fn.cfg.blocks
        for st in b.statements
        if st.is_head and st.kind == "for_statement"
    )
    return [
        (x.start_line, x.end_line, x.params, x.returns)
        for x in find_extractions(fn, get_language_map("go"))
        if x.start_line <= loop <= x.end_line
    ]


def test_no_span_passes_the_buffer_in_and_leaves_it_out_of_the_returns():
    spans = _spans_holding_the_loop("fill(&buf, c)", "fill(&buf, s[r])")
    assert [s for s in spans if "buf" in s[2] and "buf" not in s[3]] == []


def test_the_in_place_twin_has_no_such_span_either():
    """The control: the same writes spelled as assignments."""
    spans = _spans_holding_the_loop("buf = append(buf, c)", "buf = append(buf, s[r])")
    assert [s for s in spans if "buf" in s[2] and "buf" not in s[3]] == []


_UNMARSHAL = """
func f(data []byte, limit int) (int, error) {
    {DECL}
    if err := load(&cfg); err != nil {
        return 0, err
    }
    total := cfg.x
    if total > limit {
        total = limit
    } else {
        total += 1
    }
    println(total)
    println(limit)
    return total, nil
}
"""


def test_a_variable_filled_through_its_address_is_read_as_written_before_its_use():
    """``var cfg P; load(&cfg)``: the read after it observes the call's write,
    not only the zero value the declaration gave it."""
    fn = _f(_UNMARSHAL.replace("{DECL}", "var cfg P"))
    lines = sorted(d.line for d in fn.def_use.definitions if d.var == "cfg")
    declared, filled = lines
    assert filled == declared + 1
    reads = [
        u for b in fn.def_use.blocks.values() for u in b.uses if u.name == "cfg" and not u.echo
    ]
    assert {u.line for u in reads} == {filled, filled + 3}
