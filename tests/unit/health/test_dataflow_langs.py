"""Dataflow def/use + CFG + Extract Method coverage for the ported languages.

Covers the Go and TS/JS ``DefUseDialect``s and the language-agnostic CFG /
slicer once the Python-specific grammar was lifted onto ``LanguageNodeMap``.
Best-effort like the other dataflow tests: each language skips when its
tree-sitter pack is missing rather than failing.
"""

from __future__ import annotations

import textwrap

import pytest

from repowise.core.analysis.health.complexity.languages import get_language_map
from repowise.core.analysis.health.dataflow import (
    analyze_file,
    build_cfgs_for_file,
    find_extractions,
)


def _require(language: str) -> None:
    try:
        from repowise.core.ingestion.parser import _get_language
    except Exception:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    if _get_language(language) is None:
        pytest.skip(f"tree-sitter language pack missing for {language}")


def _analyses(language: str, src: str):
    _require(language)
    res = analyze_file(f"m.{language}", language, textwrap.dedent(src).encode(), flagged_only=False)
    if res.stats.functions_seen == 0:
        pytest.skip(f"tree-sitter language pack missing for {language}")
    return res.functions


def _first(language: str, src: str):
    fns = _analyses(language, src)
    assert fns, "no function analysed"
    return fns[0]


def _def_names(fn) -> set[str]:
    return {d.var for d in fn.def_use.definitions}


def _use_names(fn) -> set[str]:
    return {u.name for b in fn.def_use.blocks.values() for u in b.uses}


# == Go =========================================================================

# Every Go write shape in one function: ``:=``, ``var``, ``=``, ``+=``, ``x++``,
# a ``range`` binder, a multi-assign, and a selector/index target (not a local).
_GO_SHAPES = """
package main
func shapes(input []int, base int) int {
    total := 0
    var scale int = 2
    for i, v := range input {
        total += v * scale
        seen[i] = v
        cfg.count++
    }
    a, b := base, total
    a, b = b, a
    return a + b
}
"""


def test_go_def_use_classification():
    fn = _first("go", _GO_SHAPES)
    defs = _def_names(fn)
    # ``:=`` / ``var`` / ``range`` / multi-assign targets are all locals.
    assert {"total", "scale", "i", "v", "a", "b"} <= defs
    assert {"input", "base"} <= _def_names(fn)  # parameters seeded as defs
    # A selector target (``cfg.count++``) and index target (``seen[i] = v``) bind
    # no local: their bases are reads, never defs.
    assert "seen" not in defs
    assert "cfg" not in defs
    assert "count" not in defs
    uses = _use_names(fn)
    assert {"scale", "v", "seen", "cfg", "base"} <= uses


def test_go_for_clause_and_while_heads():
    fn = _first(
        "go",
        """
        package main
        func loops(n int) int {
            sum := 0
            for j := 0; j < n; j++ {
                sum += j
            }
            for sum < 100 {
                sum *= 2
            }
            return sum
        }
        """,
    )
    # ``j`` is bound by the C-style for-clause initializer.
    assert "j" in _def_names(fn)
    # The CFG has two loop headers with back-edges.
    headers = [b for b in fn.cfg.blocks if b.kind == "loop_header"]
    assert len(headers) == 2
    assert fn.cfg.back_edges()


def test_go_method_receiver_is_seeded():
    fn = _first(
        "go",
        """
        package main
        func (s *Server) handle(req int) int {
            x := s.lookup(req)
            return x
        }
        """,
    )
    assert "s" in _def_names(fn)  # the receiver is available in the body


def test_go_else_if_chain_branches():
    # The C-family ``else if`` nests in the ``alternative`` field; each arm is a
    # branch and every path reaches exit.
    fn = _first(
        "go",
        """
        package main
        func grade(x int) int {
            y := 0
            if x == 1 {
                y = 1
            } else if x == 2 {
                y = 2
            } else if x == 3 {
                y = 3
            } else {
                y = 4
            }
            return y
        }
        """,
    )
    branches = [b for b in fn.cfg.blocks if b.kind == "branch"]
    assert len(branches) == 3  # three ``if`` tests
    assert fn.cfg.exit_id in fn.cfg.reachable_ids()


def test_go_if_else_branch_and_continue():
    fn = _first(
        "go",
        """
        package main
        func f(items []int, x int) int {
            total := 0
            for _, it := range items {
                if it > x {
                    total += it
                } else {
                    total -= it
                }
                if it == 0 {
                    continue
                }
            }
            return total
        }
        """,
    )
    branches = [b for b in fn.cfg.blocks if b.kind == "branch"]
    assert len(branches) == 2
    # The else arm and a join both exist; the loop has a back-edge.
    assert [b for b in fn.cfg.blocks if b.kind == "join"]
    assert fn.cfg.back_edges()


# A long Go function whose compute-average tail is a clean extraction.
_GO_PROCESS = """
package main
func process(records []int, threshold int) (int, int) {
    results := []int{}
    errors := 0
    for _, r := range records {
        if r < 0 {
            errors++
            continue
        }
        results = append(results, r)
    }
    total := 0
    count := 0
    for _, v := range results {
        if v > threshold {
            total += v
            count++
        } else {
            total -= v
        }
    }
    average := 0
    if count > 0 {
        average = total / count
    }
    return average, errors
}
"""


def test_go_extract_method_fires():
    lmap = get_language_map("go")
    extractions = find_extractions(_first("go", _GO_PROCESS), lmap)
    assert extractions, "expected at least one Go extraction"
    best = extractions[0]
    # The strongest extraction is the compute-average tail.
    assert "average" in best.returns
    assert "results" in best.params and "threshold" in best.params
    assert len(best.returns) <= 1
    assert best.ccn_removed >= 1
    assert best.slice_nloc >= 6
    # The whole function body is never offered as an extraction.
    assert not (best.start_line <= 4 and best.end_line >= 27)


def test_go_extractions_are_deterministic():
    lmap = get_language_map("go")
    fn = _first("go", _GO_PROCESS)

    def serialize():
        return [
            (e.start_line, e.end_line, e.params, e.returns, e.ccn_removed)
            for e in find_extractions(fn, lmap)
        ]

    first = serialize()
    for _ in range(3):
        assert serialize() == first


# == TypeScript / JavaScript ====================================================

_TS_SHAPES = """
function shapes(input: number[], base: number): number {
    let total = 0;
    const scale = 2;
    var legacy = 1;
    for (const v of input) {
        total += v * scale;
        legacy++;
    }
    const [a, b] = pair(total);
    const {p, q} = obj;
    obj.attr = base;
    arr[0] = total;
    return a + b + p + q + legacy;
}
"""


def test_ts_def_use_classification():
    fn = _first("typescript", _TS_SHAPES)
    defs = _def_names(fn)
    # let / const / var / array-destructure / object-destructure are all locals.
    assert {"total", "scale", "legacy", "v", "a", "b", "p", "q"} <= defs
    assert {"input", "base"} <= defs  # parameters
    # Member / subscript targets bind no local; their bases are reads.
    assert "obj" not in defs
    assert "attr" not in defs
    assert "arr" not in defs
    uses = _use_names(fn)
    assert {"scale", "v", "obj", "base", "total"} <= uses


def test_js_def_use_destructuring():
    # Plain JS (no type annotations) exercises the shared dialect on the JS pack.
    fn = _first(
        "javascript",
        """
        function f(items, factor) {
            let acc = 0;
            for (const x of items) {
                acc += x * factor;
            }
            const [head, ...tail] = items;
            return acc + head + tail.length;
        }
        """,
    )
    defs = _def_names(fn)
    assert {"acc", "x", "head", "tail"} <= defs
    assert {"items", "factor"} <= defs


_TS_PROCESS = """
function process(records: number[], threshold: number): [number, number] {
    const results: number[] = [];
    let errors = 0;
    for (const r of records) {
        if (r < 0) {
            errors += 1;
            continue;
        }
        results.push(r);
    }
    let total = 0;
    let count = 0;
    for (const v of results) {
        if (v > threshold) {
            total += v;
            count += 1;
        } else {
            total -= v;
        }
    }
    let average = 0;
    if (count > 0) {
        average = total / count;
    }
    return [average, errors];
}
"""


def test_ts_extract_method_fires():
    lmap = get_language_map("typescript")
    extractions = find_extractions(_first("typescript", _TS_PROCESS), lmap)
    assert extractions, "expected at least one TS extraction"
    best = extractions[0]
    assert "average" in best.returns
    assert "results" in best.params and "threshold" in best.params
    assert len(best.returns) <= 1
    assert best.ccn_removed >= 1
    assert best.slice_nloc >= 6


def test_ts_guard_cascade_has_no_extraction():
    # Every span contains a return -> no single-exit slice.
    lmap = get_language_map("typescript")
    src = """
        function classify(x: number): string {
            if (x < 0) {
                return "neg";
            }
            if (x === 0) {
                return "zero";
            }
            if (x < 10) {
                return "small";
            }
            return "large";
        }
        """
    assert find_extractions(_first("typescript", src), lmap) == []


def test_ts_extractions_are_deterministic():
    lmap = get_language_map("typescript")
    fn = _first("typescript", _TS_PROCESS)

    def serialize():
        return [
            (e.start_line, e.end_line, e.params, e.returns, e.ccn_removed)
            for e in find_extractions(fn, lmap)
        ]

    first = serialize()
    for _ in range(3):
        assert serialize() == first


# == flagged-only gate (the per-language budget contract) =======================


def test_go_flagged_only_gate_skips_small_functions():
    _require("go")
    lines = ["package main", "func big(x int) int {", "    y := 0"]
    for i in range(12):
        lines += [f"    if x == {i} {{", f"        y = {i}", "    }"]
    lines += ["    return y", "}", "", "func tiny() int {", "    return 1", "}", ""]
    result = build_cfgs_for_file("m.go", "go", "\n".join(lines).encode())
    if result.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for go")
    assert result.stats.functions_seen == 2
    assert result.stats.functions_built == 1  # only the flagged big function
    assert [fc.name for fc in result.functions] == ["big"]


def test_ts_flagged_only_gate_skips_small_functions():
    _require("typescript")
    lines = ["function big(x: number): number {", "    let y = 0;"]
    for i in range(12):
        lines += [f"    if (x === {i}) {{", f"        y = {i};", "    }"]
    lines += ["    return y;", "}", "", "function tiny(): number {", "    return 1;", "}", ""]
    result = build_cfgs_for_file("m.ts", "typescript", "\n".join(lines).encode())
    if result.stats.functions_seen == 0:
        pytest.skip("tree-sitter language pack missing for typescript")
    assert result.stats.functions_seen == 2
    assert result.stats.functions_built == 1
    assert [fc.name for fc in result.functions] == ["big"]
