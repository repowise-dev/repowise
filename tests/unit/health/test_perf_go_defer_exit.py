"""``defer_in_loop`` exit shapes for the Go perf dialect (#2938).

A ``defer`` whose next statement in the same block leaves the loop — a
``return``, or a ``break`` that exits the ``for`` (not a ``switch``/``select``
case, and not merely an inner loop of a nest) — can only be reached once, so
the deferred call is registered at most once and the leak anti-pattern does
not apply. Every other shape keeps firing.

The cases avoid io/regex/concat sinks so the only marker in play is
``defer_in_loop``.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.complexity import walk_file

_REPORTED = [("defer_in_loop", "")]


def _hits(src: str):
    fc = walk_file("t.go", "go", src.encode())
    return sorted((h.kind, h.detail) for h in fc.perf_hits)


# ---------------------------------------------------------------------------
# The defer runs once: the next statement leaves the loop (or the function)
# ---------------------------------------------------------------------------

_EXIT_CASES = [
    (
        "package m\nfunc f(xs []int){ for _, x := range xs { defer g(); break } }",
        "range loop: defer then break",
    ),
    (
        "package m\nfunc f(n int){ for i := 0; i < n; i++ { defer g(); break } }",
        "three-clause for: defer then break",
    ),
    (
        "package m\nfunc f(){ for { defer g(); break } }",
        "bare for{}: defer then break",
    ),
    (
        "package m\nfunc f(n int){ for n > 0 { defer g(); break } }",
        "for-cond: defer then break",
    ),
    (
        "package m\nfunc f(xs []int){ for _, x := range xs { defer g(); return } }",
        "range loop: defer then bare return",
    ),
    (
        "package m\nfunc f(xs []int) error { for _, x := range xs { defer g(); return nil } }",
        "defer then return nil",
    ),
    (
        "package m\nfunc f(xs []int) (int, error) { for _, x := range xs { defer g(); return x, nil } }",
        "defer then multi-value return",
    ),
    (
        "package m\n"
        "func f(xs []string, cached bool){\n"
        "\tfor _, n := range xs {\n"
        "\t\tif cached {\n"
        "\t\t\tdefer g()\n"
        "\t\t\tbreak\n"
        "\t\t}\n"
        "\t\th()\n"
        "\t}\n}",
        "hugo shape: defer then break inside an if in the body",
    ),
    (
        "package m\nfunc f(xs []int){ for _, x := range xs {\n"
        "\tdefer g()\n"
        "\t// the reader above is all we need\n"
        "\tbreak\n} }",
        "comment between the defer and the break is skipped",
    ),
    (
        "package m\nfunc f(xs []int){\nouter:\n\tfor _, x := range xs {\n"
        "\t\tdefer g()\n"
        "\t\tbreak outer\n"
        "\t}\n}",
        "labeled break naming the loop exits it",
    ),
    (
        "package m\nfunc f(xs []int){\nouter:\n\tfor _, x := range xs {\n"
        "\t\tfor j := 0; j < x; j++ {\n"
        "\t\t\tdefer g()\n"
        "\t\t\tbreak outer\n"
        "\t\t}\n"
        "\t\th()\n"
        "\t}\n}",
        "labeled break out of an inner loop exits every loop",
    ),
    (
        "package m\nfunc f(ch chan int){ for { select {\n"
        "\tcase <-ch:\n"
        "\t\tdefer g()\n"
        "\t\treturn\n"
        "} } }",
        "return inside a select case exits the function",
    ),
]


@pytest.mark.parametrize("src,note", _EXIT_CASES, ids=[c[1] for c in _EXIT_CASES])
def test_defer_exit_suppresses_finding(src, note):
    assert _hits(src) == [], note


# ---------------------------------------------------------------------------
# The defer repeats: the next statement does not leave the loop
# ---------------------------------------------------------------------------

_REPEAT_CASES = [
    (
        "package m\nfunc f(xs []int){ for _, x := range xs { defer g() } }",
        "baseline: plain defer in a range loop",
    ),
    (
        "package m\nfunc f(xs []int){ for _, x := range xs { h(); defer g() } }",
        "defer as the last statement of the body",
    ),
    (
        "package m\nfunc f(xs []int){ for _, x := range xs { defer g(); h(); break } }",
        "a call between the defer and the break means it is not the next statement",
    ),
    (
        "package m\nfunc f(xs []int){ for _, x := range xs { defer g(); continue } }",
        "defer then continue",
    ),
    (
        "package m\nfunc f(xs []int){ for _, x := range xs { switch x {\n"
        "\tcase 1:\n"
        "\t\tdefer g()\n"
        "\t\tbreak\n"
        "\tdefault:\n"
        "\t\th()\n} } }",
        "unlabeled break in a switch case leaves the case, not the loop",
    ),
    (
        "package m\nfunc f(ch chan int, xs []int){ for _, x := range xs {\n"
        "\tselect {\n"
        "\tcase <-ch:\n"
        "\t\tdefer g()\n"
        "\t\tbreak\n"
        "\tdefault:\n"
        "\t\th()\n"
        "\t}\n} }",
        "unlabeled break in a select case leaves the case, not the loop",
    ),
    (
        "package m\nfunc f(v any, xs []int){ for _, x := range xs { switch v.(type) {\n"
        "\tcase int:\n"
        "\t\tdefer g()\n"
        "\t\tbreak\n"
        "\tdefault:\n"
        "\t\th()\n} } }",
        "unlabeled break in a type-switch case leaves the case, not the loop",
    ),
    (
        "package m\nfunc f(xs []int){ for _, x := range xs {\n"
        "inner:\n"
        "\tswitch x {\n"
        "\tcase 1:\n"
        "\t\tdefer g()\n"
        "\t\tbreak inner\n"
        "\tdefault:\n"
        "\t\th()\n"
        "\t}\n} }",
        "labeled break naming the switch (inside the loop) keeps the loop running",
    ),
    (
        "package m\nfunc f(xs []int){ for _, x := range xs {\n"
        "blk:\n"
        "\tif x > 1 {\n"
        "\t\tdefer g()\n"
        "\t\tbreak blk\n"
        "\t}\n"
        "\th()\n} }",
        "labeled break naming an if-block inside the loop keeps the loop running",
    ),
    (
        "package m\nfunc f(xs [][]int){ for _, row := range xs { for _, x := range row {\n"
        "\tdefer g()\n"
        "\tbreak\n"
        "} } }",
        "unlabeled break exits only the inner loop; the outer one re-enters",
    ),
    (
        "package m\nfunc f(xs []int){ for _, x := range xs {\n"
        "\tdefer g()\n"
        "\tswitch x {\n"
        "\tcase 1:\n"
        "\t\th()\n"
        "\t}\n"
        "} }",
        "a defer before a switch that does not exit keeps firing (shape guard)",
    ),
    (
        "package m\nfunc f(xs []int){ for _, x := range xs {\n"
        "\tdefer g()\n"
        "\tgoto done\n"
        "} \ndone:\n\th()\n}",
        "goto after the defer is out of scope for the syntactic check",
    ),
    (
        "package m\nfunc f(xs []int){ for _, x := range xs { break; defer g() } }",
        "break BEFORE the defer does not cover it",
    ),
    (
        "package m\nfunc f(xs []int){ for _, x := range xs { defer g(); defer h(); break } }",
        "only the last defer is covered by the break",
    ),
    (
        "package m\nfunc f(xs []int){ for _, x := range xs { defer g(); break missing } }",
        "a break with an undefined label stays conservative",
    ),
    (
        "package m\nfunc f(xs [][]int){ for _, row := range xs { if len(row) > 0 {\n"
        "\tfor _, x := range row {\n"
        "\t\tdefer g()\n"
        "\t\tbreak\n"
        "\t}\n"
        "\th()\n"
        "} } }",
        "inner-loop break behind an if still re-enters via the outer loop",
    ),
    (
        "package m\nfunc f(ch chan int){ for { select {\n"
        "\tcase <-ch:\n"
        "\t\tdefer g()\n"
        "\t\th()\n"
        "} } }",
        "plain defer inside a select case",
    ),
    (
        "package m\nfunc f(xs []int){ for _, x := range xs { switch x {\n"
        "\tcase 1:\n"
        "\t\tdefer g()\n"
        "\t\th()\n"
        "} } }",
        "plain defer inside a switch case",
    ),
]


@pytest.mark.parametrize("src,note", _REPEAT_CASES, ids=[c[1] for c in _REPEAT_CASES])
def test_defer_repeat_keeps_finding(src, note):
    assert _hits(src) == _REPORTED, note


def test_two_plain_defers_report_both():
    src = (
        "package m\nfunc f(xs []int){ for _, x := range xs {\n"
        "\tdefer g()\n"
        "\tdefer h()\n"
        "} }"
    )
    assert _hits(src) == [("defer_in_loop", ""), ("defer_in_loop", "")]


def test_only_the_uncovered_defer_reports():
    """A covered defer (followed by the exit) and an uncovered one in the
    same body: exactly one finding, for the uncovered one."""
    src = (
        "package m\nfunc f(xs []int){ for _, x := range xs {\n"
        "\tdefer g()\n"
        "\tif x > 1 {\n"
        "\t\tdefer h()\n"
        "\t\tbreak\n"
        "\t}\n"
        "\th()\n"
        "} }"
    )
    assert _hits(src) == [("defer_in_loop", "")]


# ---------------------------------------------------------------------------
# The issue's own repro shapes, io markers included
# ---------------------------------------------------------------------------


def test_issue_repro_os_open_shapes():
    """The five shapes from #2938: three stop firing, two keep firing."""
    suppress = [
        'package m\nimport "os"\n'
        "func f(n string){ for { f, _ := os.Open(n); defer f.Close(); break } }",
        'package m\nimport "os"\n'
        "func f(n string, cached bool){ for {\n"
        "\tif cached {\n"
        "\t\tf, _ := os.Open(n)\n"
        "\t\tdefer f.Close()\n"
        "\t\tbreak\n"
        "\t}\n"
        "\tos.Stat(n)\n} }",
        'package m\nimport "os"\n'
        "func f(n string){ for { f, _ := os.Open(n); defer f.Close(); return } }",
    ]
    for src in suppress:
        kinds = [h.kind for h in walk_file("t.go", "go", src.encode()).perf_hits]
        assert "defer_in_loop" not in kinds, src

    repeat = [
        'package m\nimport "os"\n'
        "func f(n string){ for { f, _ := os.Open(n); defer f.Close(); f.Stat() } }",
        'package m\nimport "os"\n'
        "func f(n string){ for { switch n {\n"
        '\tcase "a":\n'
        "\t\tf, _ := os.Open(n)\n"
        "\t\tdefer f.Close()\n"
        "\t\tbreak\n"
        '\tdefault:\n'
        "\t\th()\n} } }",
    ]
    for src in repeat:
        kinds = [h.kind for h in walk_file("t.go", "go", src.encode()).perf_hits]
        assert "defer_in_loop" in kinds, src
